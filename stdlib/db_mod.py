"""db module — SQLite database driver via Python stdlib (no external deps).

Connection mode: autocommit (isolation_level=None). Each statement commits
immediately unless an explicit transaction is open via `db.begin` or
`db.transaction(conn, fn)`.

Functions:
  db.connect(path)                 -> open or create a database, return connection
  db.exec(conn, sql, params?)      -> run a DDL/DML statement, return affected row count
  db.query(conn, sql, params?)     -> SELECT, return list of map { col: value }
  db.query_one(conn, sql, params?) -> first row or nothing
  db.close(conn)                   -> close a connection
  db.last_id(conn)                 -> last inserted row id

Transactions:
  db.begin(conn)                   -> START TRANSACTION
  db.commit(conn)                  -> COMMIT
  db.rollback(conn)                -> ROLLBACK
  db.transaction(conn, fn)         -> block-scoped: commits on success,
                                       rolls back on throw (recommended form)
"""

import sqlite3 as _sqlite


def connect(path):
    """Open a SQLite connection. Use ':memory:' for an in-memory database.

    Autocommit mode (isolation_level=None) is used so that BEGIN/COMMIT/ROLLBACK
    are entirely under user control. check_same_thread=False allows the
    connection to be shared across worker threads (ThreadingHTTPServer).
    """
    conn = _sqlite.connect(str(path), check_same_thread=False, isolation_level=None)
    conn.row_factory = _sqlite.Row
    return conn


def exec_(conn, sql, params=None):
    """Execute a DDL/DML statement. Returns number of affected rows.

    In autocommit mode each statement commits immediately, unless wrapped in
    an explicit transaction.
    """
    cur = conn.cursor()
    if params is None:
        cur.execute(sql)
    else:
        cur.execute(sql, _normalize_params(params))
    affected = cur.rowcount if cur.rowcount >= 0 else 0
    cur.close()
    return affected


def query(conn, sql, params=None):
    """Run a SELECT. Returns list of dict (column name -> value)."""
    cur = conn.cursor()
    if params is None:
        cur.execute(sql)
    else:
        cur.execute(sql, _normalize_params(params))
    rows = [dict(row) for row in cur.fetchall()]
    cur.close()
    return rows


def query_one(conn, sql, params=None):
    """Run a SELECT, return first row or nothing."""
    rows = query(conn, sql, params)
    return rows[0] if rows else None


def close(conn):
    """Close a connection."""
    conn.close()
    return True


def last_id(conn):
    """Return the rowid of the most recent INSERT."""
    cur = conn.cursor()
    cur.execute('SELECT last_insert_rowid()')
    val = cur.fetchone()[0]
    cur.close()
    return val


# ---------- Transactions ----------

def begin(conn):
    """Start a transaction. Subsequent db.exec calls are NOT auto-committed
    until db.commit or db.rollback is called."""
    cur = conn.cursor()
    cur.execute('BEGIN')
    cur.close()
    return True


def commit(conn):
    """Commit the current transaction."""
    cur = conn.cursor()
    cur.execute('COMMIT')
    cur.close()
    return True


def rollback(conn):
    """Roll back the current transaction."""
    cur = conn.cursor()
    cur.execute('ROLLBACK')
    cur.close()
    return True


def transaction(conn, fn):
    """Block-scoped transaction. Commits if `fn(conn)` returns normally,
    rolls back if `fn` throws.

    Recommended form:
        db.transaction(conn, fn tx -> do {
          db.exec(tx, "INSERT ...", [...])
          db.exec(tx, "UPDATE ...", [...])
        })
    """
    from interpreter import FeelFunction, Interpreter, Environment
    from errors import FeelThrow

    begin(conn)
    try:
        if isinstance(fn, FeelFunction):
            local = Environment(fn.closure)
            if fn.params:
                local.set(fn.params[0], conn)
            sub = Interpreter(env=local)
            result = sub.eval_expr(fn.body)
        else:
            result = fn(conn)
        commit(conn)
        return result
    except (Exception, FeelThrow):
        try:
            rollback(conn)
        except Exception:
            pass  # rollback might fail if already rolled back; swallow
        raise


def _normalize_params(params):
    """Accept list or dict — pass through if already proper sqlite type."""
    if isinstance(params, (list, tuple)):
        return tuple(params)
    if isinstance(params, dict):
        return params
    raise ValueError(f"db params must be a list or map, got {type(params).__name__}")


EXPORTS = {
    'connect':     connect,
    'exec':        exec_,
    'query':       query,
    'query_one':   query_one,
    'close':       close,
    'last_id':     last_id,
    'begin':       begin,
    'commit':      commit,
    'rollback':    rollback,
    'transaction': transaction,
}
