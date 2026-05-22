"""migrate module — file-based schema migrations.

Convention:
  migrations/
    001_create_users.feel
    002_add_email_index.feel
    ...

Each migration file is a normal Feel script that runs against a connection
bound to the name `conn`. The runner:
  1. Connects to db_path
  2. Creates `schema_migrations` table if not exists
  3. Sorts migration files lexicographically
  4. Runs every pending file in a transaction; INSERT into schema_migrations on success
  5. Returns { applied: [...], skipped: [...], failed: {id, error} | nothing }

Functions:
  migrate.apply(db_path, migrations_dir)  → result map
  migrate.status(db_path, migrations_dir) → list of { id, applied }
"""

import glob
import os


_MIGRATIONS_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    id TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""


def _discover(directory):
    if not os.path.isdir(directory):
        return []
    return sorted(glob.glob(os.path.join(directory, '*.feel')))


def _mig_id(path):
    name = os.path.basename(path)
    return name[:-len('.feel')] if name.endswith('.feel') else name


def _applied_ids(conn):
    from . import db_mod
    db_mod.exec_(conn, _MIGRATIONS_TABLE)
    rows = db_mod.query(conn, "SELECT id FROM schema_migrations")
    return {r['id'] for r in rows}


def apply(db_path, migrations_dir):
    from . import db_mod, install_into
    from interpreter import Interpreter, Environment

    conn = db_mod.connect(db_path)
    try:
        already = _applied_ids(conn)
        applied = []
        skipped = []
        failed = None

        for path in _discover(migrations_dir):
            mid = _mig_id(path)
            if mid in already:
                skipped.append(mid)
                continue

            db_mod.begin(conn)
            try:
                with open(path, encoding='utf-8') as f:
                    src = f.read()
                env = Environment()
                install_into(env)
                env.set('conn', conn)
                interp = Interpreter(env=env, filename=path, source=src)
                interp.run(src)
                db_mod.exec_(conn, "INSERT INTO schema_migrations (id) VALUES (?)", [mid])
                db_mod.commit(conn)
                applied.append(mid)
            except Exception as e:
                db_mod.rollback(conn)
                failed = {'id': mid, 'error': str(e)}
                break

        return {'applied': applied, 'skipped': skipped, 'failed': failed}
    finally:
        db_mod.close(conn)


def status(db_path, migrations_dir):
    from . import db_mod
    conn = db_mod.connect(db_path)
    try:
        already = _applied_ids(conn)
        return [{'id': _mig_id(p), 'applied': _mig_id(p) in already}
                for p in _discover(migrations_dir)]
    finally:
        db_mod.close(conn)


EXPORTS = {
    'apply':  apply,
    'status': status,
}
