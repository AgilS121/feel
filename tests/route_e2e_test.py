"""End-to-end test: load Feel routes file, start server, hit endpoints."""

import json
import os
import sys
import urllib.request
import urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from runtime.router import global_registry
from runtime.http import serve_in_thread


def _request(port, path, method='GET', body=None, headers=None):
    url = f'http://127.0.0.1:{port}{path}'
    data = None
    h = dict(headers or {})
    if body is not None:
        data = json.dumps(body).encode('utf-8')
        h.setdefault('Content-Type', 'application/json')
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req) as resp:
            payload = resp.read().decode('utf-8')
            return resp.status, json.loads(payload) if payload else None
    except urllib.error.HTTPError as e:
        payload = e.read().decode('utf-8')
        return e.code, json.loads(payload) if payload else None


def main():
    # Load Feel routes
    from interpreter import run_file
    global_registry().clear()
    run_file(os.path.join(ROOT, 'tests', 'routes_only.feel'))

    server, port = serve_in_thread(port=0)
    try:
        # GET /hello
        status, body = _request(port, '/hello')
        assert status == 200, f'hello status: {status}'
        assert body == {"message": "Hello, Feel!"}, f'hello body: {body}'
        print("  PASS  GET /hello")

        # GET /health
        status, body = _request(port, '/health')
        assert status == 200
        assert body == {"ok": True, "version": "0.3-m2"}
        print("  PASS  GET /health")

        # GET /todos/{id} — path param
        status, body = _request(port, '/todos/42')
        assert status == 200
        assert body == {"todo_id": "42", "title": "Sample"}
        print("  PASS  GET /todos/{id} (path param)")

        # POST /echo — body roundtrip
        status, body = _request(port, '/echo', method='POST', body={"x": 1, "y": [2, 3]})
        assert status == 200
        assert body == {"x": 1, "y": [2, 3]}, f'echo body: {body}'
        print("  PASS  POST /echo (body)")

        # GET /maybe-error → throw → 500
        status, body = _request(port, '/maybe-error')
        assert status == 500
        assert 'unhandled throw' in body['error']
        print("  PASS  GET /maybe-error (throw -> 500)")

        # GET /custom-status — explicit 201
        status, body = _request(port, '/custom-status')
        assert status == 201, f'custom status: {status}'
        assert body == {"created": True}
        print("  PASS  GET /custom-status (201)")

        # GET /no-content — 204 no body
        status, body = _request(port, '/no-content')
        assert status == 204, f'no-content status: {status}'
        print("  PASS  GET /no-content (204)")

        # 404
        status, body = _request(port, '/does-not-exist')
        assert status == 404
        print("  PASS  GET /unknown (404)")

        # 405 (wrong method on existing path)
        status, body = _request(port, '/hello', method='DELETE')
        assert status == 405
        print("  PASS  DELETE /hello (405)")
    finally:
        server.shutdown()
        server.server_close()

    print('\nAll route e2e tests passed.')


if __name__ == '__main__':
    main()
