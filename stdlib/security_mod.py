"""security module — application-layer hardening primitives.

LIMITATIONS (read this before relying on it):
  - These are APPLICATION-LAYER protections. They run AFTER the request
    has already reached your process. They cannot stop a SYN flood or
    volumetric DDoS. For that you need a CDN / reverse proxy (Cloudflare,
    nginx with limit_req, AWS Shield, etc.).
  - These primitives reduce risk from:
      * brute-force credential attacks
      * scrapers and abusive clients
      * cascading failures during attacks (panic mode + DB kill switch)
      * compromised handler logic running unbounded operations
  - Threshold tuning is your responsibility. Wrong thresholds either let
    attacks through or DoS your own users. Start permissive, tighten with
    real traffic data.

Functions:
  security.rate_limit(key, max, window_seconds)
      Per-key sliding window. Returns true if allowed, false if blocked.
      Use request.headers["x-forwarded-for"] or similar as the key.

  security.report_failed(key, max_failures, window_seconds)
      Track failure events per key (e.g. failed login by IP). Returns true
      once the key has exceeded max_failures inside the window.

  security.panic(reason)
      Trigger panic mode. Closes every connection registered via kill_switch,
      sets a global flag so the HTTP runtime returns 503 for all subsequent
      requests, and writes a PANIC event to the audit log.

  security.is_panic_mode()
      Returns true if panic has been triggered.

  security.panic_reason()
      Returns the reason string from the most recent panic.

  security.kill_switch(conn)
      Register a DB connection (or other closable resource) to be closed
      automatically when panic is triggered. Backend "self-destruct".

  security.audit(event)
      Append an audit log entry. `event` can be a string or a map.
      Set destination with security.set_audit_log(path), default stderr.

  security.set_audit_log(path)
      Direct audit events to a file (appended). Pass nothing to revert
      to stderr.

  security.reset()
      Clear all state (rate buckets, failures, panic). Test-only.
"""

import json as _json
import os
import sys
import time
from collections import deque
from threading import RLock


_lock = RLock()
_panic_flag = False
_panic_reason_text = ''
_kill_switches = []          # list of objects with .close()
_rate_buckets = {}           # key -> deque of float timestamps
_failed_attempts = {}        # key -> deque of float timestamps
_audit_path = None           # None = stderr


def rate_limit(key, max_requests, window_seconds):
    """Sliding window rate limit. True if allowed, False if exceeded."""
    with _lock:
        now = time.time()
        cutoff = now - float(window_seconds)
        bucket = _rate_buckets.setdefault(str(key), deque())
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= int(max_requests):
            return False
        bucket.append(now)
        return True


def report_failed(key, max_failures, window_seconds):
    """Record a failure event. Returns True once the threshold is crossed
    (i.e. this caller should treat the key as blocked)."""
    with _lock:
        now = time.time()
        cutoff = now - float(window_seconds)
        bucket = _failed_attempts.setdefault(str(key), deque())
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        bucket.append(now)
        return len(bucket) >= int(max_failures)


def panic(reason):
    """Trigger panic mode. Idempotent — second call returns False."""
    global _panic_flag, _panic_reason_text
    with _lock:
        if _panic_flag:
            return False
        _panic_flag = True
        _panic_reason_text = str(reason)
        for conn in list(_kill_switches):
            try:
                conn.close()
            except Exception:
                pass
        _kill_switches.clear()
        _audit_event({'type': 'PANIC', 'reason': _panic_reason_text})
        print(f'[SECURITY] PANIC MODE ACTIVE: {reason}', file=sys.stderr)
        return True


def is_panic_mode():
    return _panic_flag


def panic_reason():
    return _panic_reason_text


def kill_switch(conn):
    """Register a connection (or anything with .close()) to be closed
    automatically when panic is triggered."""
    with _lock:
        _kill_switches.append(conn)
    return True


def audit(event):
    """Append an audit event. event can be a string or a map."""
    return _audit_event(event)


def set_audit_log(path=None):
    """Send audit events to a file (appended). Pass nothing to use stderr."""
    global _audit_path
    _audit_path = str(path) if path else None
    return True


def reset():
    """Clear all in-memory state. Intended for tests."""
    global _panic_flag, _panic_reason_text
    with _lock:
        _panic_flag = False
        _panic_reason_text = ''
        _rate_buckets.clear()
        _failed_attempts.clear()
        _kill_switches.clear()
    return True


def _audit_event(event):
    if isinstance(event, dict):
        entry = dict(event)
        entry.setdefault('time', round(time.time(), 3))
        line = _json.dumps(entry, ensure_ascii=False)
    else:
        line = f'{round(time.time(), 3)}: {event}'
    if _audit_path:
        try:
            with open(_audit_path, 'a', encoding='utf-8') as f:
                f.write(line + '\n')
        except Exception:
            print(f'[audit-fallback] {line}', file=sys.stderr)
    else:
        print(f'[audit] {line}', file=sys.stderr)
    return True


EXPORTS = {
    'rate_limit':    rate_limit,
    'report_failed': report_failed,
    'panic':         panic,
    'is_panic_mode': is_panic_mode,
    'panic_reason':  panic_reason,
    'kill_switch':   kill_switch,
    'audit':         audit,
    'set_audit_log': set_audit_log,
    'reset':         reset,
}
