"""HTTP server runtime — wraps Python's stdlib http.server."""

import json as _json
import os as _os
import time as _time
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs


def _make_uploaded_file(field_name, filename, content_type, content):
    """Build a Feel-visible upload record: a map with name/size/content_type/content
    plus a save_to bound method. Behaves naturally with req.files['x'].save_to(...)
    since Feel field-access on a dict returns the value, which can then be called."""
    def save_to(path):
        path = str(path)
        parent = _os.path.dirname(path)
        if parent:
            _os.makedirs(parent, exist_ok=True)
        with open(path, 'wb') as f:
            f.write(content)
        return _os.path.abspath(path)

    return {
        'field_name':   field_name,
        'name':         filename,
        'size':         len(content),
        'content_type': content_type or 'application/octet-stream',
        'content':      content,
        'save_to':      save_to,
    }


def _parse_multipart(body_raw, content_type_header):
    """Parse multipart/form-data into (files, form_fields).

    Uses email.parser (stdlib) — robust against quoted boundaries / CRLF variants.
    Returns: (dict[name -> FeelUploadedFile], dict[name -> str]).
    """
    from email.parser import BytesParser
    from email.policy import default as default_policy

    # email parser wants headers + body together. Synthesize a wrapper.
    wrapper = b'MIME-Version: 1.0\r\nContent-Type: ' + content_type_header.encode('latin-1') + b'\r\n\r\n' + body_raw
    msg = BytesParser(policy=default_policy).parsebytes(wrapper)

    files = {}
    form = {}
    if not msg.is_multipart():
        return files, form

    for part in msg.iter_parts():
        cd = part.get('Content-Disposition', '')
        if 'form-data' not in cd:
            continue
        field_name = part.get_param('name', header='Content-Disposition')
        filename = part.get_param('filename', header='Content-Disposition')
        payload = part.get_payload(decode=True) or b''
        if filename:
            files[field_name] = _make_uploaded_file(
                field_name=field_name,
                filename=filename,
                content_type=part.get_content_type(),
                content=payload,
            )
        else:
            form[field_name] = payload.decode('utf-8', errors='replace')
    return files, form


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
        self._files = None
        self._form = None

    def _ensure_multipart_parsed(self):
        if self._files is not None:
            return
        ct = self.headers.get('content-type') or ''
        if 'multipart/form-data' in ct.lower():
            self._files, self._form = _parse_multipart(self.body_raw, ct)
        else:
            self._files, self._form = {}, {}

    @property
    def files(self):
        """Map of upload field name → FeelUploadedFile (empty if not multipart)."""
        self._ensure_multipart_parsed()
        return self._files

    @property
    def form(self):
        """Map of non-file form fields → string (empty if not multipart)."""
        self._ensure_multipart_parsed()
        return self._form

    @property
    def body(self):
        """Decoded body. Returns:
        - map for application/json
        - map for multipart/form-data (form fields)
        - map for application/x-www-form-urlencoded (key/value pairs)
        - string otherwise
        """
        if self._body_cached is not None:
            return self._body_cached
        if not self.body_raw:
            self._body_cached = None
            return None
        ct = (self.headers.get('content-type') or '').lower()
        if 'multipart/form-data' in ct:
            self._ensure_multipart_parsed()
            self._body_cached = self._form
            return self._form
        if 'application/x-www-form-urlencoded' in ct:
            from urllib.parse import parse_qs as _parse_qs
            text = self.body_raw.decode('utf-8', errors='replace')
            parsed = _parse_qs(text, keep_blank_values=True)
            # Collapse single-element lists to scalar (matches `query` convention).
            form = {k: (v[0] if len(v) == 1 else v) for k, v in parsed.items()}
            self._form = form  # also expose via request.form for symmetry with multipart
            self._files = self._files if self._files is not None else {}
            self._body_cached = form
            return form
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


def _serve_static(registry, path):
    """Look up path in static mounts. Returns FeelResponse or None."""
    import mimetypes as _mime
    match = registry.match_static(path)
    if match is None:
        return None
    prefix, fs_dir, sub_path = match
    # Block traversal: realpath of join must be under realpath(fs_dir).
    fs_dir_real = _os.path.realpath(fs_dir)
    target = _os.path.realpath(_os.path.join(fs_dir, sub_path))
    if not (target == fs_dir_real or target.startswith(fs_dir_real + _os.sep)):
        return FeelResponse(status=403, body={'error': 'forbidden'})
    # If target is a directory, look for index.html.
    if _os.path.isdir(target):
        idx = _os.path.join(target, 'index.html')
        if _os.path.isfile(idx):
            target = idx
        else:
            return FeelResponse(status=404, body={'error': 'not found', 'path': path})
    if not _os.path.isfile(target):
        return FeelResponse(status=404, body={'error': 'not found', 'path': path})
    ctype, _enc = _mime.guess_type(target)
    if not ctype:
        ctype = 'application/octet-stream'
    with open(target, 'rb') as f:
        data = f.read()
    return FeelResponse(status=200, body=data, content_type=ctype)


def _make_handler_class(registry, error_mapper, log_fn, cors=False):
    """Create a BaseHTTPRequestHandler subclass bound to a registry."""

    cors_headers = {
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Methods': 'GET, POST, PUT, PATCH, DELETE, HEAD, OPTIONS',
        'Access-Control-Allow-Headers': 'Content-Type, Authorization, X-Requested-With',
        'Access-Control-Max-Age': '86400',
    } if cors else {}

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

            # CORS preflight: auto-answer OPTIONS for any path so browser
            # can verify the actual request is allowed.
            if cors and method == 'OPTIONS':
                self.send_response(204)
                for k, v in cors_headers.items():
                    self.send_header(k, v)
                self.send_header('Content-Length', '0')
                self.end_headers()
                log_fn(method, path, 204, _time.time() - t_start)
                return

            # PANIC MODE — every request gets 503 until the process restarts.
            # User handlers do NOT run while panic is active.
            try:
                from stdlib.security_mod import is_panic_mode, panic_reason
                if is_panic_mode():
                    resp = FeelResponse(status=503, body={
                        'error': 'service unavailable (panic mode)',
                        'reason': panic_reason(),
                    })
                    status, ctype, body_bytes = resp.encode()
                    self.send_response(status)
                    self.send_header('Content-Type', ctype)
                    self.send_header('Content-Length', str(len(body_bytes)))
                    for k, v in cors_headers.items():
                        self.send_header(k, v)
                    self.end_headers()
                    self.wfile.write(body_bytes)
                    log_fn(method, path, status, _time.time() - t_start)
                    return
            except Exception:
                pass

            # WebSocket upgrade — handshake then call the WS handler.
            from runtime.websocket import is_websocket_upgrade, perform_handshake, FeelWebSocket
            if method == 'GET' and is_websocket_upgrade(headers):
                ws_handler, ws_params = registry.resolve('WS', path)
                if ws_handler is not None:
                    try:
                        perform_handshake(self.wfile, headers)
                        ws = FeelWebSocket(self.rfile, self.wfile, self.connection, path)
                        request = FeelRequest(
                            method='WS', path=path, query=query,
                            headers=headers, body_raw=b'', params=ws_params
                        )
                        request._ws = ws  # passed through to handler
                        ws_handler(request)
                    except Exception as exc:
                        try:
                            log_fn('WS', path, 500, _time.time() - t_start)
                        except Exception:
                            pass
                    else:
                        log_fn('WS', path, 101, _time.time() - t_start)
                    return  # connection consumed by ws

            handler, info = registry.resolve(method, path)

            try:
                if handler is None and info == 'method_mismatch':
                    allowed = registry.all_methods_for_path(path)
                    resp = FeelResponse(
                        status=405,
                        body={'error': 'method not allowed', 'allowed': allowed}
                    )
                    resp.headers['Allow'] = ', '.join(allowed)
                elif handler is None and method in ('GET', 'HEAD'):
                    # Try static-mount lookup before 404.
                    static_resp = _serve_static(registry, path)
                    if static_resp is not None:
                        resp = static_resp
                    else:
                        resp = FeelResponse(status=404, body={'error': 'not found', 'path': path})
                elif handler is None:
                    resp = FeelResponse(status=404, body={'error': 'not found', 'path': path})
                else:
                    request = FeelRequest(
                        method=method, path=path, query=query,
                        headers=headers, body_raw=body_raw, params=info
                    )
                    raw = handler(request)
                    resp = FeelResponse.from_handler_return(raw)
                    # Extract session cookies (stuffed by session.set / session.clear)
                    if isinstance(resp.body, dict) and '__cookies__' in resp.body:
                        cookies = resp.body.pop('__cookies__')
                        # We can only set one Set-Cookie via send_header per call, so
                        # accumulate them in resp.headers under unique keys; the writer
                        # below iterates resp.headers in order.
                        for i, c in enumerate(cookies):
                            resp.headers[f'Set-Cookie__{i}'] = c
            except Exception as exc:
                resp = error_mapper(exc)

            status, ctype, body_bytes = resp.encode()
            self.send_response(status)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(body_bytes)))
            for k, v in cors_headers.items():
                self.send_header(k, v)
            for k, v in resp.headers.items():
                # Allow multiple Set-Cookie via __i suffix
                actual_name = 'Set-Cookie' if k.startswith('Set-Cookie__') else k
                self.send_header(actual_name, v)
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


def serve(port=3000, host='127.0.0.1', registry=None, error_mapper=None, log_fn=None,
          cors=False, cert_file=None, key_file=None):
    """Start the HTTP server. Blocks until KeyboardInterrupt.

    If cors=True, every response includes permissive CORS headers and
    OPTIONS preflight requests are auto-answered with 204.
    If cert_file and key_file are provided, the server runs over HTTPS (TLS).
    """
    from .router import global_registry
    if registry is None:
        registry = global_registry()
    if error_mapper is None:
        error_mapper = _default_error_mapper
    if log_fn is None:
        log_fn = _default_log

    handler_cls = _make_handler_class(registry, error_mapper, log_fn, cors=cors)
    server = ThreadingHTTPServer((host, port), handler_cls)
    actual_port = server.server_address[1]

    scheme = 'http'
    if cert_file and key_file:
        import ssl
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(certfile=cert_file, keyfile=key_file)
        server.socket = ctx.wrap_socket(server.socket, server_side=True)
        scheme = 'https'

    cors_note = ' (CORS enabled)' if cors else ''
    print(f'[feel] serving on {scheme}://{host}:{actual_port}{cors_note}', file=sys.stderr)
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
