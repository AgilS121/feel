"""map module — operasi pada map (dict)."""


def get(m, key, default=None):
    if not isinstance(m, dict): return default
    return m.get(key, default)


def set_(m, key, value):
    """Return new map dengan key:value. (immutable style)"""
    out = dict(m) if isinstance(m, dict) else {}
    out[key] = value
    return out


def has(m, key):
    return isinstance(m, dict) and key in m


def delete(m, key):
    """Return new map tanpa key."""
    out = dict(m) if isinstance(m, dict) else {}
    out.pop(key, None)
    return out


def keys(m):
    return list(m.keys()) if isinstance(m, dict) else []


def values(m):
    return list(m.values()) if isinstance(m, dict) else []


def entries(m):
    """Return list of [key, value] pairs."""
    if not isinstance(m, dict): return []
    return [[k, v] for k, v in m.items()]


def size(m):
    return len(m) if isinstance(m, dict) else 0


def merge(a, b):
    out = dict(a) if isinstance(a, dict) else {}
    if isinstance(b, dict):
        out.update(b)
    return out


EXPORTS = {
    'get':     get,
    'set':     set_,
    'has':     has,
    'delete':  delete,
    'keys':    keys,
    'values':  values,
    'entries': entries,
    'size':    size,
    'merge':   merge,
}
