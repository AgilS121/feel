"""list module — operasi list yang sering dipakai."""


def take(items, n):
    return list(items)[:int(n)]


def drop(items, n):
    return list(items)[int(n):]


def slice_(items, start, end=None):
    if end is None:
        return list(items)[int(start):]
    return list(items)[int(start):int(end)]


def reverse(items):
    return list(items)[::-1]


def sort(items, key=None, descending=False):
    if key is None:
        return sorted(items, reverse=bool(descending))
    return sorted(items, key=key, reverse=bool(descending))


def unique(items):
    seen = []
    out = []
    for it in items:
        if it not in seen:
            seen.append(it)
            out.append(it)
    return out


def flatten(items):
    out = []
    for it in items:
        if isinstance(it, list):
            out.extend(it)
        else:
            out.append(it)
    return out


def zip_(*lists):
    return [list(t) for t in zip(*lists)]


def range_(start, end=None, step=1):
    if end is None:
        return list(range(int(start)))
    return list(range(int(start), int(end), int(step)))


def index_of(items, value):
    try:
        return items.index(value)
    except ValueError:
        return -1


def count(items, value):
    return items.count(value)


def all_(items):
    return all(items)


def any_(items):
    return any(items)


def fold(items, init, fn):
    """Reduce: ((acc, item) -> acc) starting from init."""
    from interpreter import FeelFunction, Interpreter, Environment
    acc = init
    for it in items:
        if isinstance(fn, FeelFunction):
            # Panggil FeelFunction manual (tanpa interpreter instance, perlu env baru)
            local = Environment(fn.closure)
            for p, v in zip(fn.params, [acc, it]):
                local.set(p, v)
            sub = Interpreter(env=local)
            acc = sub.eval_expr(fn.body)
        else:
            acc = fn(acc, it)
    return acc


def map_(items, fn):
    from interpreter import FeelFunction, Interpreter, Environment
    out = []
    for it in items:
        if isinstance(fn, FeelFunction):
            local = Environment(fn.closure)
            for p, v in zip(fn.params, [it]):
                local.set(p, v)
            sub = Interpreter(env=local)
            out.append(sub.eval_expr(fn.body))
        else:
            out.append(fn(it))
    return out


def filter_(items, fn):
    from interpreter import FeelFunction, Interpreter, Environment
    out = []
    for it in items:
        if isinstance(fn, FeelFunction):
            local = Environment(fn.closure)
            for p, v in zip(fn.params, [it]):
                local.set(p, v)
            sub = Interpreter(env=local)
            keep = sub.eval_expr(fn.body)
        else:
            keep = fn(it)
        if keep:
            out.append(it)
    return out


def first(items):
    return items[0] if items else None


def last(items):
    return items[-1] if items else None


EXPORTS = {
    'take':     take,
    'drop':     drop,
    'slice':    slice_,
    'reverse':  reverse,
    'sort':     sort,
    'unique':   unique,
    'flatten':  flatten,
    'zip':      zip_,
    'range':    range_,
    'index_of': index_of,
    'count':    count,
    'all':      all_,
    'any':      any_,
    'fold':     fold,
    'map':      map_,
    'filter':   filter_,
    'first':    first,
    'last':     last,
}
