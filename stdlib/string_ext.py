"""string module — operasi text yang sering dipakai."""


def trim(s):
    return s.strip() if isinstance(s, str) else s


def trim_start(s):
    return s.lstrip() if isinstance(s, str) else s


def trim_end(s):
    return s.rstrip() if isinstance(s, str) else s


def replace(s, old, new):
    return s.replace(old, new)


def starts_with(s, prefix):
    return s.startswith(prefix)


def ends_with(s, suffix):
    return s.endswith(suffix)


def pad_start(s, width, char=' '):
    s = str(s)
    return char * max(0, width - len(s)) + s


def pad_end(s, width, char=' '):
    s = str(s)
    return s + char * max(0, width - len(s))


def repeat(s, n):
    return str(s) * int(n)


def slice_(s, start, end=None):
    if end is None:
        return s[int(start):]
    return s[int(start):int(end)]


def index_of(s, substr):
    return s.find(substr)


def to_chars(s):
    return list(s)


def lines(s):
    return s.splitlines()


def words(s):
    return s.split()


EXPORTS = {
    'trim':        trim,
    'trim_start':  trim_start,
    'trim_end':    trim_end,
    'replace':     replace,
    'starts_with': starts_with,
    'ends_with':   ends_with,
    'pad_start':   pad_start,
    'pad_end':     pad_end,
    'repeat':      repeat,
    'slice':       slice_,
    'index_of':    index_of,
    'to_chars':    to_chars,
    'lines':       lines,
    'words':       words,
}
