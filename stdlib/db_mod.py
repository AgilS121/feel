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


class _Conn:
    """Unified connection wrapper. Handles paramstyle translation across
    SQLite (?), MySQL/Postgres (%s), so user code uses ? everywhere.
    """
    def __init__(self, raw, kind):
        self.raw = raw
        self.kind = kind  # 'sqlite' | 'mysql' | 'postgres'

    def cursor(self):
        return self.raw.cursor()

    def close(self):
        return self.raw.close()


def _translate_qmark_to_pct(sql):
    """Replace ? placeholders with %s, skipping anything inside quoted strings."""
    out = []
    i = 0
    in_single = False
    in_double = False
    while i < len(sql):
        c = sql[i]
        if c == "'" and not in_double:
            in_single = not in_single
            out.append(c)
        elif c == '"' and not in_single:
            in_double = not in_double
            out.append(c)
        elif c == '?' and not in_single and not in_double:
            out.append('%s')
        else:
            out.append(c)
        i += 1
    return ''.join(out)


def connect(path):
    """Open a database connection. The URL scheme picks the driver:
      ./file.db or :memory:        →  SQLite (stdlib)
      sqlite:///path               →  SQLite (explicit)
      mysql://user:pass@host/db    →  MySQL  (requires `pip install pymysql`)
      postgres://user:pass@host/db →  Postgres (requires `pip install psycopg2-binary`)

    Autocommit mode for SQLite. For MySQL/Postgres the driver default is used —
    use db.begin/commit/rollback for explicit transactions.
    """
    s = str(path)

    if s.startswith('mysql://'):
        try:
            import pymysql
        except ImportError:
            raise RuntimeError(
                "MySQL connection requires PyMySQL. Install with: pip install pymysql"
            )
        from urllib.parse import urlparse
        u = urlparse(s)
        raw = pymysql.connect(
            host=u.hostname or 'localhost',
            port=u.port or 3306,
            user=u.username or '',
            password=u.password or '',
            database=(u.path or '/').lstrip('/'),
            autocommit=True,
        )
        return _Conn(raw, 'mysql')

    if s.startswith('postgres://') or s.startswith('postgresql://'):
        try:
            import psycopg2
            import psycopg2.extras
        except ImportError:
            raise RuntimeError(
                "PostgreSQL connection requires psycopg2. Install with: "
                "pip install psycopg2-binary"
            )
        raw = psycopg2.connect(s)
        raw.autocommit = True
        return _Conn(raw, 'postgres')

    # SQLite path (default)
    if s.startswith('sqlite:///'):
        s = s[len('sqlite:///'):]
    conn = _sqlite.connect(s, check_same_thread=False, isolation_level=None)
    conn.row_factory = _sqlite.Row
    return _Conn(conn, 'sqlite')


def _prep_sql(conn, sql):
    """Translate ? placeholders for non-SQLite drivers."""
    if isinstance(conn, _Conn) and conn.kind != 'sqlite':
        return _translate_qmark_to_pct(sql)
    return sql


def exec_(conn, sql, params=None):
    """Execute a DDL/DML statement. Returns affected row count."""
    cur = conn.cursor()
    sql2 = _prep_sql(conn, sql)
    if params is None:
        cur.execute(sql2)
    else:
        cur.execute(sql2, _normalize_params(params))
    affected = cur.rowcount if cur.rowcount >= 0 else 0
    cur.close()
    return affected


def _row_to_dict(row, cursor):
    """Convert a row to a dict regardless of driver."""
    if hasattr(row, 'keys'):  # sqlite Row
        return dict(row)
    # MySQL/Postgres: tuple + cursor.description
    if cursor.description:
        cols = [d[0] for d in cursor.description]
        return dict(zip(cols, row))
    return {}


def query(conn, sql, params=None):
    """Run a SELECT. Returns list of dict (column name -> value)."""
    cur = conn.cursor()
    sql2 = _prep_sql(conn, sql)
    if params is None:
        cur.execute(sql2)
    else:
        cur.execute(sql2, _normalize_params(params))
    rows = [_row_to_dict(r, cur) for r in cur.fetchall()]
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
    """Return the rowid/last_insert_id of the most recent INSERT."""
    if isinstance(conn, _Conn):
        if conn.kind == 'sqlite':
            cur = conn.cursor()
            cur.execute('SELECT last_insert_rowid()')
            val = cur.fetchone()[0]
            cur.close()
            return val
        if conn.kind == 'mysql':
            cur = conn.cursor()
            cur.execute('SELECT LAST_INSERT_ID()')
            val = cur.fetchone()[0]
            cur.close()
            return val
        if conn.kind == 'postgres':
            # Postgres requires RETURNING id in the INSERT statement to get this.
            # As a fallback return 0; users should use INSERT...RETURNING id.
            return 0
    # legacy raw sqlite3.Connection
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


# ---------- ORM-lite query builder ----------

class _Query:
    """Mutable query builder. Each chained call mutates and returns self
    so pipeline-style and direct-style both work."""
    __slots__ = ('conn', 'table', 'wheres', 'order', 'limit_n', 'offset_n')

    def __init__(self, conn, table):
        self.conn = conn
        self.table = table
        self.wheres = []          # list of (col, op, val)
        self.order = None         # (col, direction)
        self.limit_n = None
        self.offset_n = None

    def clone(self):
        q = _Query(self.conn, self.table)
        q.wheres = list(self.wheres)
        q.order = self.order
        q.limit_n = self.limit_n
        q.offset_n = self.offset_n
        return q


def find(conn, table):
    """Start a query for a table. Returns a query object."""
    return _Query(conn, str(table))


def _is_query(x):
    return isinstance(x, _Query)


def _add_where(q, col, op, val):
    q.wheres.append((str(col), str(op), val))
    return q


def where(*args):
    """db.where(query, col, op, val)  — direct
       db.where(col, op, val)         — curried; returns fn(query) for pipelines"""
    if args and _is_query(args[0]):
        if len(args) != 4:
            raise ValueError("where: expected (query, col, op, val)")
        return _add_where(args[0], args[1], args[2], args[3])
    if len(args) == 3:
        col, op, val = args
        return lambda q: _add_where(q, col, op, val)
    raise ValueError("where: expected (col, op, val) or (query, col, op, val)")


def _add_order(q, col, direction):
    q.order = (str(col), 'DESC' if str(direction).upper() == 'DESC' else 'ASC')
    return q


def order_by(*args):
    """db.order_by(query, col [, direction])
       db.order_by(col [, direction])  — curried"""
    if args and _is_query(args[0]):
        q = args[0]
        col = args[1]
        direction = args[2] if len(args) >= 3 else 'ASC'
        return _add_order(q, col, direction)
    col = args[0]
    direction = args[1] if len(args) >= 2 else 'ASC'
    return lambda q: _add_order(q, col, direction)


def take(*args):
    """db.take(query, n)  /  db.take(n)  — curried"""
    if args and _is_query(args[0]):
        args[0].limit_n = int(args[1])
        return args[0]
    n = int(args[0])
    return lambda q: (setattr(q, 'limit_n', n) or q)


def offset(*args):
    if args and _is_query(args[0]):
        args[0].offset_n = int(args[1])
        return args[0]
    n = int(args[0])
    return lambda q: (setattr(q, 'offset_n', n) or q)


def _build_sql(q):
    sql = f'SELECT * FROM {q.table}'
    params = []
    if q.wheres:
        parts = []
        for col, op, val in q.wheres:
            parts.append(f'{col} {op} ?')
            params.append(val)
        sql += ' WHERE ' + ' AND '.join(parts)
    if q.order:
        sql += f' ORDER BY {q.order[0]} {q.order[1]}'
    if q.limit_n is not None:
        sql += f' LIMIT {q.limit_n}'
    if q.offset_n is not None:
        sql += f' OFFSET {q.offset_n}'
    return sql, params


def all_(q):
    """Execute the query and return all matching rows."""
    if not _is_query(q):
        raise ValueError("all: expected a query")
    sql, params = _build_sql(q)
    return query(q.conn, sql, params)


def first(q):
    """Execute, return the first row or nothing."""
    if not _is_query(q):
        raise ValueError("first: expected a query")
    q2 = q.clone()
    q2.limit_n = 1
    sql, params = _build_sql(q2)
    rows = query(q.conn, sql, params)
    return rows[0] if rows else None


def count(q):
    """Execute COUNT(*) instead of SELECT *."""
    if not _is_query(q):
        raise ValueError("count: expected a query")
    sql = f'SELECT COUNT(*) AS n FROM {q.table}'
    params = []
    if q.wheres:
        parts = []
        for col, op, val in q.wheres:
            parts.append(f'{col} {op} ?')
            params.append(val)
        sql += ' WHERE ' + ' AND '.join(parts)
    rows = query(q.conn, sql, params)
    return rows[0]['n'] if rows else 0


EXPORTS.update({
    'find':     find,
    'where':    where,
    'order_by': order_by,
    'take':     take,
    'offset':   offset,
    'all':      all_,
    'first':    first,
    'count':    count,
})
