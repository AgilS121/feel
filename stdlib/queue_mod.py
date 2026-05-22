"""queue module — SQLite-backed job queue with a worker loop.

Why SQLite-backed?
- Zero infrastructure: same db file as your app data, ACID semantics for
  enqueue + dequeue, persists across restarts. Good enough for low-medium
  throughput. Swap to Redis/RabbitMQ when you outgrow it (API will be kept).

Functions:
  queue.connect(db_path)                     open + init queue table
  queue.enqueue(qconn, name, payload_map)    add a job to queue 'name'
  queue.pop(qconn, name)                     atomic dequeue: returns job map or nothing
  queue.complete(qconn, job_id)              mark a popped job done (delete)
  queue.fail(qconn, job_id, error)           record failure, increment retry count
  queue.pending(qconn, name)                 count of pending jobs
  queue.process_once(qconn, name, handler)   pop + call handler(job.payload) + complete/fail
  queue.work(qconn, name, handler, poll_ms?) run forever (blocking) — used by feel worker

Job lifecycle: pending → running (lease) → done OR failed (with retries).
"""

import json as _json
import os
import time as _time

from . import db_mod


_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    queue_name TEXT NOT NULL,
    payload TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    leased_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""

_INDEX_SQL = "CREATE INDEX IF NOT EXISTS idx_jobs_queue_status ON jobs(queue_name, status)"


def connect(db_path):
    """Open or create the queue database + ensure schema."""
    conn = db_mod.connect(db_path)
    db_mod.exec_(conn, _TABLE_SQL)
    db_mod.exec_(conn, _INDEX_SQL)
    return conn


def enqueue(qconn, name, payload):
    """Add a job. payload is a map (any JSON-serializable shape)."""
    text = _json.dumps(payload) if payload is not None else 'null'
    db_mod.exec_(qconn, "INSERT INTO jobs (queue_name, payload) VALUES (?, ?)", [str(name), text])
    return db_mod.last_id(qconn)


def pop(qconn, name):
    """Atomically lease the oldest pending job for `name`. Returns the job map or None.
    Uses a transaction so two workers don't grab the same row."""
    db_mod.begin(qconn)
    try:
        row = db_mod.query_one(qconn,
            "SELECT id, payload, attempts FROM jobs WHERE queue_name = ? AND status = 'pending' ORDER BY id ASC LIMIT 1",
            [str(name)])
        if row is None:
            db_mod.commit(qconn)
            return None
        db_mod.exec_(qconn,
            "UPDATE jobs SET status = 'running', leased_at = CURRENT_TIMESTAMP, attempts = attempts + 1 WHERE id = ?",
            [row['id']])
        db_mod.commit(qconn)
        return {
            'id':       row['id'],
            'payload':  _json.loads(row['payload']) if row['payload'] != 'null' else None,
            'attempts': row['attempts'] + 1,
        }
    except Exception:
        db_mod.rollback(qconn)
        raise


def complete(qconn, job_id):
    """Mark a job done (delete from queue)."""
    db_mod.exec_(qconn, "DELETE FROM jobs WHERE id = ?", [int(job_id)])
    return True


def fail(qconn, job_id, error, max_attempts=3):
    """Mark failed. Re-enqueue (status='pending') if attempts < max_attempts,
    else 'failed' (kept for inspection)."""
    row = db_mod.query_one(qconn, "SELECT attempts FROM jobs WHERE id = ?", [int(job_id)])
    if row is None:
        return False
    if row['attempts'] >= int(max_attempts):
        db_mod.exec_(qconn,
            "UPDATE jobs SET status = 'failed', last_error = ? WHERE id = ?",
            [str(error), int(job_id)])
    else:
        db_mod.exec_(qconn,
            "UPDATE jobs SET status = 'pending', last_error = ?, leased_at = NULL WHERE id = ?",
            [str(error), int(job_id)])
    return True


def pending(qconn, name):
    """How many pending jobs in this queue."""
    row = db_mod.query_one(qconn,
        "SELECT COUNT(*) AS n FROM jobs WHERE queue_name = ? AND status = 'pending'",
        [str(name)])
    return row['n'] if row else 0


def process_once(qconn, name, handler):
    """Pop one job, call handler(payload), complete or fail. Returns True if a
    job was processed, False if queue was empty."""
    from interpreter import FeelFunction, Interpreter, Environment

    job = pop(qconn, name)
    if job is None:
        return False
    try:
        if isinstance(handler, FeelFunction):
            local = Environment(handler.closure)
            if handler.params:
                local.set(handler.params[0], job['payload'])
            sub = Interpreter(env=local)
            sub.eval_expr(handler.body)
        else:
            handler(job['payload'])
        complete(qconn, job['id'])
        return True
    except Exception as e:
        fail(qconn, job['id'], str(e))
        return True


def work(qconn, name, handler, poll_ms=500):
    """Run a worker loop forever. Blocking — use in a separate process."""
    import sys
    poll_s = float(poll_ms) / 1000.0
    print(f'[queue] worker started: queue={name!r}, poll={poll_ms}ms', file=sys.stderr)
    try:
        while True:
            processed = process_once(qconn, name, handler)
            if not processed:
                _time.sleep(poll_s)
    except KeyboardInterrupt:
        print('\n[queue] worker stopped', file=sys.stderr)


EXPORTS = {
    'connect':      connect,
    'enqueue':      enqueue,
    'pop':          pop,
    'complete':     complete,
    'fail':         fail,
    'pending':      pending,
    'process_once': process_once,
    'work':         work,
}
