"""json module — encode & decode JSON."""

import json as _json


def _to_jsonable(v):
    from interpreter import FeelRecord
    if isinstance(v, FeelRecord):
        return {k: _to_jsonable(vv) for k, vv in v.fields.items()}
    if isinstance(v, dict):
        return {k: _to_jsonable(vv) for k, vv in v.items()}
    if isinstance(v, list):
        return [_to_jsonable(x) for x in v]
    return v


def encode(value, pretty=False):
    data = _to_jsonable(value)
    if pretty:
        return _json.dumps(data, indent=2, ensure_ascii=False)
    # Compact form (no spaces) — matches Go json.Marshal and is canonical.
    return _json.dumps(data, ensure_ascii=False, separators=(',', ':'))


def decode(text):
    return _json.loads(text)


EXPORTS = {
    'encode': encode,
    'decode': decode,
}
