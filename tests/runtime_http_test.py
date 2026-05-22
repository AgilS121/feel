"""Smoke test for runtime/http.py and runtime/router.py.
Standalone Python test — doesn't require Feel-side integration yet.
Run: python tests/runtime_http_test.py
"""

import json
import os
import sys
import urllib.request
import urllib.error

# add project root to path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from runtime.router import RouteRegistry, compile_pattern, match_route
from runtime.http import FeelResponse, serve_in_thread


def test_compile_pattern():
    rx, params = compile_pattern("/todos/{id}")
    assert params == ['id']
    m = rx.match("/todos/42")
    assert m and m.group('id') == '42'
    assert rx.match("/todos") is None
    assert rx.match("/todos/42/extra") is None
    print("  PASS  compile_pattern")


def test_match_static():
    rx, params = compile_pattern("/health")
    assert params == []
    assert match_route(rx, "/health") == {}
    assert match_route(rx, "/healthx") is None
    print("  PASS  match_static")


def test_match_multiple_params():
    rx, params = compile_pattern("/users/{uid}/posts/{pid}")
    assert params == ['uid', 'pid']
    assert match_route(rx, "/users/abc/posts/123") == {'uid': 'abc', 'pid': '123'}
    print("  PASS  match_multiple_params")


def test_registry_resolve():
    reg = RouteRegistry()
    reg.register("GET", "/health", lambda req: {"ok": True})
    reg.register("POST", "/users", lambda req: {"created": True})

    h, p = reg.resolve("GET", "/health")
    assert h is not None
    assert p == {}

    h, p = reg.resolve("GET", "/users")
    assert h is None and p == 'method_mismatch'

    h, p = reg.resolve("GET", "/nope")
    assert h is None and p is None
    print("  PASS  registry_resolve")


def _http_get(port, path, body=None, method='GET', headers=None):
    url = f'http://127.0.0.1:{port}{path}'
    data = None
    h = {'Content-Type': 'application/json'} if body is not None else {}
    if headers:
        h.update(headers)
    if body is not None:
        data = json.dumps(body).encode('utf-8')
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode('utf-8'))


def test_serve_basic():
    reg = RouteRegistry()
    reg.register("GET", "/hello", lambda req: {"message": "hi"})
    reg.register("GET", "/todos/{id}", lambda req: {"id": req.params['id']})
    reg.register("POST", "/echo", lambda req: {"got": req.body})

    server, port = serve_in_thread(port=0, registry=reg)
    try:
        status, body = _http_get(port, '/hello')
        assert status == 200 and body == {"message": "hi"}, f'hello: {status} {body}'

        status, body = _http_get(port, '/todos/42')
        assert status == 200 and body == {"id": "42"}, f'param: {status} {body}'

        status, body = _http_get(port, '/echo', body={"x": 1}, method='POST')
        assert status == 200 and body == {"got": {"x": 1}}, f'echo: {status} {body}'

        status, body = _http_get(port, '/nope')
        assert status == 404, f'404: {status} {body}'

        status, body = _http_get(port, '/hello', method='DELETE')
        assert status == 405, f'405: {status} {body}'
    finally:
        server.shutdown()
        server.server_close()
    print("  PASS  serve_basic")


def test_serve_error_mapping():
    from errors import FeelThrow

    reg = RouteRegistry()
    def boom(req):
        raise FeelThrow("kaboom")
    reg.register("GET", "/boom", boom)

    server, port = serve_in_thread(port=0, registry=reg)
    try:
        status, body = _http_get(port, '/boom')
        assert status == 500 and 'unhandled throw' in body['error'], f'got: {status} {body}'
    finally:
        server.shutdown()
        server.server_close()
    print("  PASS  serve_error_mapping")


def main():
    test_compile_pattern()
    test_match_static()
    test_match_multiple_params()
    test_registry_resolve()
    test_serve_basic()
    test_serve_error_mapping()
    print('\nAll runtime/http tests passed.')


if __name__ == '__main__':
    main()
