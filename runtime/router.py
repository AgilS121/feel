"""Router: pattern compilation + route registration + matching."""

import re


def compile_pattern(pattern):
    """Convert a Feel route pattern like '/todos/{id}' to a regex + param names.

    Returns: (compiled_regex, ['id'])
    """
    param_names = []
    regex_parts = ['^']
    i = 0
    while i < len(pattern):
        c = pattern[i]
        if c == '{':
            end = pattern.find('}', i)
            if end == -1:
                raise ValueError(f"unclosed '{{' in route pattern: {pattern!r}")
            name = pattern[i + 1:end]
            if not name.isidentifier():
                raise ValueError(f"invalid param name '{name}' in pattern {pattern!r}")
            param_names.append(name)
            regex_parts.append(f'(?P<{name}>[^/]+)')
            i = end + 1
        else:
            regex_parts.append(re.escape(c))
            i += 1
    regex_parts.append('$')
    return re.compile(''.join(regex_parts)), param_names


def match_route(compiled_regex, path):
    """Try to match `path` against compiled regex. Returns dict of params or None."""
    m = compiled_regex.match(path)
    if m is None:
        return None
    return m.groupdict()


class RouteRegistry:
    """Registry of (method, pattern) -> handler. Per-process singleton."""

    def __init__(self):
        # routes: list of (method, pattern_string, compiled_regex, param_names, handler)
        self.routes = []

    def register(self, method, pattern, handler):
        compiled, param_names = compile_pattern(pattern)
        self.routes.append((method.upper(), pattern, compiled, param_names, handler))

    def resolve(self, method, path):
        """Returns (handler, params_dict) on match, (None, 'method_mismatch') if path matches
        but wrong method, or (None, None) if path doesn't exist at all.
        """
        method = method.upper()
        path_matched = False
        for m, _pattern, compiled, _params, handler in self.routes:
            captured = match_route(compiled, path)
            if captured is not None:
                path_matched = True
                if m == method:
                    return handler, captured
        if path_matched:
            return None, 'method_mismatch'
        return None, None

    def all_methods_for_path(self, path):
        """Return list of methods registered for paths that match."""
        methods = []
        for m, _pattern, compiled, _params, _handler in self.routes:
            if match_route(compiled, path) is not None:
                methods.append(m)
        return methods

    def clear(self):
        self.routes.clear()


# Module-level singleton — interpreter sticks routes here, server reads them
_GLOBAL_REGISTRY = RouteRegistry()


def global_registry():
    return _GLOBAL_REGISTRY
