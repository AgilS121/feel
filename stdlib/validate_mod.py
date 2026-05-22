"""validate module — runtime schema check using `record` declarations.

Usage:
  record CreateUser { name: text, email: text, age: number }

  route POST "/users" -> do {
    let v = validate.shape(body, "CreateUser")
    -- v has all required fields with matching types, else throws
    save(v)
  }

Type check semantics (mirrors the parser's type slots in `record`):
  text     -> Python str
  number   -> Python int or float (not bool)
  boolean  -> Python bool
  list     -> Python list
  map      -> Python dict (and not a record)
  <Record> -> nested record check (by type name)
"""

import sys


def _interp_record_types():
    """Walk Python frames to find the Interpreter's record_types dict.
    Builtins don't naturally have access to interpreter state — this is a
    deliberate-but-ugly bridge so user code can call validate.shape from
    anywhere without explicit context passing."""
    f = sys._getframe()
    while f is not None:
        loc = f.f_locals
        obj = loc.get('self')
        if obj is not None and hasattr(obj, 'record_types'):
            return obj.record_types
        f = f.f_back
    return {}


def _check_type(value, declared_type, record_types):
    if declared_type == 'text':
        return isinstance(value, str)
    if declared_type == 'number':
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if declared_type == 'boolean':
        return isinstance(value, bool)
    if declared_type == 'list':
        return isinstance(value, list)
    if declared_type == 'map':
        return isinstance(value, dict)
    if declared_type == 'nothing':
        return value is None
    if declared_type in record_types:
        return _validate_record(value, declared_type, record_types) is not None
    return True  # unknown type → accept


def _validate_record(value, record_name, record_types):
    if record_name not in record_types:
        return None
    if not isinstance(value, dict):
        return None
    schema = record_types[record_name]
    for field_name, field_type in schema.items():
        if field_name not in value:
            return None
        if not _check_type(value[field_name], field_type, record_types):
            return None
    return value


def shape(value, record_name):
    record_types = _interp_record_types()
    if record_name not in record_types:
        raise ValueError(f"validate.shape: unknown record '{record_name}'")
    if not isinstance(value, dict):
        raise ValueError(f"validate.shape: expected map, got {type(value).__name__}")
    schema = record_types[record_name]
    errors = []
    for field_name, field_type in schema.items():
        if field_name not in value:
            errors.append(f"missing field '{field_name}'")
            continue
        if not _check_type(value[field_name], field_type, record_types):
            errors.append(f"field '{field_name}' must be {field_type}")
    if errors:
        raise ValueError(f"validation failed: {'; '.join(errors)}")
    return value


def is_valid(value, record_name):
    try:
        shape(value, record_name)
        return True
    except Exception:
        return False


def errors_for(value, record_name):
    try:
        shape(value, record_name)
        return []
    except ValueError as e:
        msg = str(e)
        if 'validation failed: ' in msg:
            return msg.split('validation failed: ', 1)[1].split('; ')
        return [msg]


EXPORTS = {
    'shape':      shape,
    'is_valid':   is_valid,
    'errors_for': errors_for,
}
