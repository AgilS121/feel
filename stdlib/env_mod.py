"""env module — environment variables + .env file loader.

Reading order for `env.get(name, default?)`:
  1. OS environment variable (os.environ)
  2. .env file in CWD (auto-loaded on first access)
  3. default argument (or nothing if no default)

`.env` format:
  KEY=value       # comment
  KEY="quoted value"
  # full-line comment
  (blank lines skipped)

Functions:
  env.get(name, default?)    — read value, returns string or default
  env.set(name, value)       — write to OS env (process-local, not persisted)
  env.has(name)              — bool: defined in OS env or loaded .env
  env.load(path?)            — explicit load from path (default ".env")
  env.all()                  — map of all loaded + OS env (OS wins)
"""

import os as _os
from threading import RLock


_lock = RLock()
_loaded = False
_dotenv_values = {}   # values loaded from .env file


def _parse_dotenv(text):
    """Parse .env text → dict. Tolerant: skip blank lines, comments, malformed lines."""
    result = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        if '=' not in line:
            continue
        key, _, value = line.partition('=')
        key = key.strip()
        value = value.strip()
        # Strip surrounding quotes (both " and ').
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        result[key] = value
    return result


def _autoload_dotenv():
    """Load .env from CWD if it exists, only once per process."""
    global _loaded
    with _lock:
        if _loaded:
            return
        _loaded = True
        candidate = _os.path.join(_os.getcwd(), '.env')
        if _os.path.isfile(candidate):
            try:
                with open(candidate, 'r', encoding='utf-8') as f:
                    _dotenv_values.update(_parse_dotenv(f.read()))
            except OSError:
                pass


def get(name, default=None):
    _autoload_dotenv()
    name = str(name)
    # OS env wins (real production overrides .env).
    val = _os.environ.get(name)
    if val is not None:
        return val
    val = _dotenv_values.get(name)
    if val is not None:
        return val
    return default


def has(name):
    _autoload_dotenv()
    name = str(name)
    return name in _os.environ or name in _dotenv_values


def set_(name, value):
    name = str(name)
    _os.environ[name] = str(value) if value is not None else ''
    return True


def load(path=None):
    """Explicit load. Useful for testing or non-CWD .env files."""
    global _loaded
    if path is None:
        path = '.env'
    if not _os.path.isfile(path):
        return False
    with open(path, 'r', encoding='utf-8') as f:
        parsed = _parse_dotenv(f.read())
    with _lock:
        _dotenv_values.update(parsed)
        _loaded = True
    return True


def all_():
    _autoload_dotenv()
    out = dict(_dotenv_values)
    out.update(_os.environ)  # OS wins
    return out


EXPORTS = {
    'get':  get,
    'has':  has,
    'set':  set_,
    'load': load,
    'all':  all_,
}
