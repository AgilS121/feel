"""cache module — in-memory key/value with TTL.

Thread-safe via a single lock. TTL in seconds. Expired entries are evicted
on read. Production cache (Redis) deferred — same API surface will swap.

Functions:
  cache.set(key, value, ttl_seconds?)        store (default no expiry)
  cache.get(key)                              value or nothing
  cache.get_or_compute(key, ttl, fn)          memoize an expensive computation
  cache.has(key)                              bool (also evicts if expired)
  cache.delete(key)                           remove one
  cache.clear()                               remove all
  cache.size()                                count of live entries
"""

import time
from threading import RLock

_lock = RLock()
_store = {}


def _now():
    return time.time()


def _expired(entry):
    _, exp = entry
    return exp is not None and exp <= _now()


def set_(key, value, ttl_seconds=None):
    with _lock:
        exp = None
        if ttl_seconds is not None:
            exp = _now() + float(ttl_seconds)
        _store[str(key)] = (value, exp)
    return value


def get(key):
    with _lock:
        k = str(key)
        if k not in _store:
            return None
        if _expired(_store[k]):
            del _store[k]
            return None
        return _store[k][0]


def get_or_compute(key, ttl_seconds, producer):
    """If cached & not expired, return cached. Otherwise call producer (zero-arg),
    cache the result with ttl, return it."""
    from interpreter import FeelFunction, Interpreter, Environment
    cached = get(key)
    if cached is not None:
        return cached
    if isinstance(producer, FeelFunction):
        local = Environment(producer.closure)
        sub = Interpreter(env=local)
        value = sub.eval_expr(producer.body)
    elif callable(producer):
        value = producer()
    else:
        raise ValueError("get_or_compute: producer must be a function")
    set_(key, value, ttl_seconds)
    return value


def has(key):
    with _lock:
        k = str(key)
        if k not in _store:
            return False
        if _expired(_store[k]):
            del _store[k]
            return False
        return True


def delete(key):
    with _lock:
        _store.pop(str(key), None)
    return True


def clear():
    with _lock:
        _store.clear()
    return True


def size():
    with _lock:
        now = _now()
        dead = [k for k, (_, exp) in _store.items() if exp is not None and exp <= now]
        for k in dead:
            del _store[k]
        return len(_store)


EXPORTS = {
    'set':            set_,
    'get':            get,
    'get_or_compute': get_or_compute,
    'has':            has,
    'delete':         delete,
    'clear':          clear,
    'size':           size,
}
