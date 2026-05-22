"""HTTP server runtime — wraps Python's stdlib http.server."""

import json as _json
import time as _time
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs


class FeelRequest:
    """Request object exposed to Feel handler."""

    def __init__(self, method, path, query, headers, body_raw, params):
        self.method = method
        self.path = path
        self.query = query        # dict (multi-values become list)
        self.headers = headers    # dict
        self.body_raw = body_raw  # bytes
        self.params = params      # path params {id: '123', ...}
        self._body_cached = None

    @property
    def body(self):
        """Decoded body: JSON dict/list if content-type JSON, else string."""
        if self._body_cached is not None:
            return self._body_cached
        if not self.body_raw:
            self._body_cached = None
            return None
        ct = (self.headers.get('content-type') or '').lower()
        text = self.body_raw.decode('utf-8', errors='replace')
        if 'application/json' in ct or text.lstrip().startswith(('{', '[')):
            try:
                self._body_cached = _json.loads(text)
                return self._body_cached
            except _json.JSONDecodeError:
                pass
        self._body_cached = text
        return text


class FeelResponse:
    """Response object built by Feel handler (or auto-built from return value)."""

    def __init__(self, status=200, body=None, content_type=None, headers=None):
        self.status = status
        self.body = body
        self.content_type = content_type
        self.headers = headers or {}

    @classmethod
    def from_handler_return(cls, value):
        """If handler returned a FeelResponse, pass through. Otherwise wrap in 200 JSON."""
        if isinstance(value, cls):
            return value
        return cls(status=200, body=value)

    def encode(self):
        """Return (status, content_type, body_bytes)."""
        ct = self.content_type
        body = self.body

        if body is None:
            return self.status, ct or 'text/plain', b''

        # If body is bytes already
        if isinstance(body, bytes):
            return self.status, ct or 'application/octet-stream', body

        # If body is str
        if isinstance(body, str):
            return self.status, ct or 'text/plain; charset=utf-8', body.encode('utf-8')

        # Else assume JSON-serializable (dict, list, FeelRecord via _to_jsonable, scalar)
        from stdlib.json_mod import _to_jsonable
        text = _json.dumps(_to_jsonable(body), ensure_ascii=False)
        return self.status, ct or 'application/json; charset=utf-8', text.encode('utf-8')


def _make_handler_class(registry, error_mapper, log_fn):
    """Create a BaseHTTPRequestHandler subclass bound to a registry."""

    class _Handler(BaseHTTPRequestHandler):
        # silence default logging — we use our own
        def log_message(self, format, *args):
            pass

        def _dispatch(self):
            t_start = _time.time()
            method = self.command
            parsed = urlparse(self.path)
            path = parsed.path
            query_raw = parse_qs(parsed.query)
            # collapse single-element lists
            query = {k: (v[0] if len(v) == 1 else v) for k, v in query_raw.items()}
            headers = {k.lower(): v for k, v in self.headers.items()}
            content_length = int(headers.get('content-length', 0) or 0)
            body_raw = self.rfile.read(content_length) if content_length else b''

            handler, info = registry.resolve(method, path)

            try:
                if handler is None and info == 'method_mismatch':
                    allowed = registry.all_methods_for_path(path)
                    resp = FeelResponse(
                        status=405,
                        body={'error': 'method not allowed', 'allowed': allowed}
                    )
                    resp.headers['Allow'] = ', '.join(allowed)
                elif handler is None:
                    resp = FeelResponse(status=404, body={'error': 'not found', 'path': path})
                else:
                    request = FeelRequest(
                        method=method, path=path, query=query,
                        headers=headers, body_raw=body_raw, params=info
                    )
                    raw = handler(request)
                    resp = FeelResponse.from_handler_return(raw)
            except Exception as exc:
                resp = error_mapper(exc)

            status, ctype, body_bytes = resp.encode()
            self.send_response(status)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(body_bytes)))
            for k, v in resp.headers.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body_bytes)

            log_fn(method, path, status, _time.time() - t_start)

        # route every HTTP method through _dispatch
        def do_GET(self):     self._dispatch()
        def do_POST(self):    self._dispatch()
        def do_PUT(self):     self._dispatch()
        def do_PATCH(self):   self._dispatch()
        def do_DELETE(self):  self._dispatch()
        def do_HEAD(self):    self._dispatch()
        def do_OPTIONS(self): self._dispatch()

    return _Handler


def _default_error_mapper(exc):
    """Map any unhandled exception to a 500 response."""
    from errors import FeelError, FeelThrow
    if isinstance(exc, FeelThrow):
        return FeelResponse(status=500, body={'error': 'unhandled throw', 'value': str(exc.value)})
    if isinstance(exc, FeelError):
        return FeelResponse(status=500, body={
            'error': 'feel runtime error',
            'message': exc.raw_message,
            'location': f'{exc.filename}:{exc.line}:{exc.col}',
        })
    return FeelResponse(status=500, body={'error': 'internal server error', 'detail': str(exc)})


def _default_log(method, path, status, elapsed):
    msg = f'{method:6s} {path:40s} -> {status}  ({elapsed*1000:.1f}ms)'
    print(msg, file=sys.stderr)


def serve(port=3000, host='127.0.0.1', registry=None, error_mapper=None, log_fn=None):
    """Start the HTTP server. Blocks until KeyboardInterrupt."""
    from .router import global_registry
    if registry is None:
        registry = global_registry()
    if error_mapper is None:
        error_mapper = _default_error_mapper
    if log_fn is None:
        log_fn = _default_log

    handler_cls = _make_handler_class(registry, error_mapper, log_fn)
    server = ThreadingHTTPServer((host, port), handler_cls)
    actual_port = server.server_address[1]
    print(f'[feel] serving on http://{host}:{actual_port}', file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\n[feel] shutting down', file=sys.stderr)
    finally:
        server.server_close()


def serve_in_thread(port=0, host='127.0.0.1', registry=None, error_mapper=None, log_fn=None):
    """Spawn server in background thread. Returns (server, actual_port). For testing."""
    import threading
    from .router import global_registry
    if registry is None:
        registry = global_registry()
    if error_mapper is None:
        error_mapper = _default_error_mapper
    if log_fn is None:
        log_fn = lambda *a, **k: None  # silent during tests

    handler_cls = _make_handler_class(registry, error_mapper, log_fn)
    server = ThreadingHTTPServer((host, port), handler_cls)
    actual_port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server, actual_port
