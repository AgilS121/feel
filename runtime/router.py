"""Router: pattern compilation + route registration + matching."""

import re


def compile_pattern(pattern):
    """Convert a Feel route pattern to a regex + param names.

    Supports both :param and {param} syntax:
      '/users/:id'        → matches /users/123, params={'id':'123'}
      '/users/{id}'       → same
      '/employees/:id/contract' → matches /employees/3/contract

    Returns: (compiled_regex, ['id'])
    """
    # Normalise :param → {param} so the loop only needs to handle one form
    pattern = re.sub(r':([a-zA-Z_][a-zA-Z0-9_]*)', r'{\1}', pattern)

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
        # static mounts: list of (url_prefix, fs_dir) — matched only on GET if no route hits
        self.static_mounts = []

    def register(self, method, pattern, handler):
        compiled, param_names = compile_pattern(pattern)
        self.routes.append((method.upper(), pattern, compiled, param_names, handler))
        # Keep routes sorted: fewer params first, then longer patterns first (more specific wins).
        self.routes.sort(key=lambda r: (len(r[3]), -len(r[1])))

    def mount_static(self, url_prefix, fs_dir):
        """Mount fs_dir at url_prefix. Longer prefixes are matched first."""
        # Normalize: strip trailing slash on prefix; keep leading slash.
        if not url_prefix.startswith('/'):
            url_prefix = '/' + url_prefix
        url_prefix = url_prefix.rstrip('/') or '/'
        self.static_mounts.append((url_prefix, fs_dir))
        # Sort descending by prefix length so /static/admin beats /static.
        self.static_mounts.sort(key=lambda m: -len(m[0]))

    def match_static(self, path):
        """Return (url_prefix, fs_dir, sub_path) if path matches a mounted prefix, else None."""
        for prefix, fs_dir in self.static_mounts:
            if prefix == '/' or path == prefix or path.startswith(prefix + '/'):
                sub = path[len(prefix):].lstrip('/')
                return (prefix, fs_dir, sub)
        return None

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
        self.static_mounts.clear()


# Module-level singleton — interpreter sticks routes here, server reads them
_GLOBAL_REGISTRY = RouteRegistry()


def global_registry():
    return _GLOBAL_REGISTRY
