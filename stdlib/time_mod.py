"""time module — waktu & tanggal."""

import time as _time
import datetime as _dt


def now():
    """Unix epoch detik (float)."""
    return _time.time()


def now_ms():
    """Unix epoch milidetik (int)."""
    return int(_time.time() * 1000)


def sleep(ms):
    _time.sleep(float(ms) / 1000.0)


def format_time(ts=None, fmt='%Y-%m-%d %H:%M:%S'):
    if ts is None:
        ts = _time.time()
    return _dt.datetime.fromtimestamp(float(ts)).strftime(fmt)


def parse_time(s, fmt='%Y-%m-%d %H:%M:%S'):
    return _dt.datetime.strptime(s, fmt).timestamp()


def iso_now():
    return _dt.datetime.now().isoformat(timespec='seconds')


EXPORTS = {
    'now':     now,
    'now_ms':  now_ms,
    'sleep':   sleep,
    'format':  format_time,
    'parse':   parse_time,
    'iso_now': iso_now,
}
