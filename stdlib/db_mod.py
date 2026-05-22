"""db module — SQLite database driver via Python stdlib (no external deps).

Functions:
  db.connect(path)              -> open or create a database, return connection
  db.exec(conn, sql, params?)   -> run a DDL/DML statement, return affected row count
  db.query(conn, sql, params?)  -> run SELECT, return list of map { col: value }
  db.close(conn)                -> close a connection
  db.last_id(conn)              -> last inserted row id (for INTEGER PRIMARY KEY)
"""

import sqlite3 as _sqlite


def connect(path):
    """Open a SQLite connection. Use ':memory:' for an in-memory database.

    check_same_thread=False so the connection can be shared across HTTP handler
    threads (ThreadingHTTPServer dispatches in worker threads). Application code
    must serialize concurrent writes if needed; for demo workloads this is fine.
    """
    conn = _sqlite.connect(str(path), check_same_thread=False)
    conn.row_factory = _sqlite.Row
    return conn


def exec_(conn, sql, params=None):
    """Execute a DDL/DML statement. Returns number of affected rows."""
    cur = conn.cursor()
    if params is None:
        cur.execute(sql)
    else:
        cur.execute(sql, _normalize_params(params))
    conn.commit()
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


def _normalize_params(params):
    """Accept list or dict — pass through if already proper sqlite type."""
    if isinstance(params, (list, tuple)):
        return tuple(params)
    if isinstance(params, dict):
        return params
    raise ValueError(f"db params must be a list or map, got {type(params).__name__}")


EXPORTS = {
    'connect':   connect,
    'exec':      exec_,
    'query':     query,
    'query_one': query_one,
    'close':     close,
    'last_id':   last_id,
}
