"""End-to-end test for examples/crud_rest.feel."""

import json
import os
import sys
import urllib.request
import urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ['FEEL_AI_PROVIDER'] = 'mock'


def _request(port, path, method='GET', body=None):
    url = f'http://127.0.0.1:{port}{path}'
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode('utf-8')
        headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req) as resp:
            payload = resp.read().decode('utf-8')
            return resp.status, json.loads(payload) if payload else None
    except urllib.error.HTTPError as e:
        payload = e.read().decode('utf-8')
        return e.code, json.loads(payload) if payload else None


def main():
    from interpreter import Interpreter
    from runtime.router import global_registry
    from runtime.http import serve_in_thread

    src_path = os.path.join(ROOT, 'examples', 'crud_rest.feel')
    with open(src_path, encoding='utf-8') as f:
        src = f.read()
    # Use a temp db file so we don't pollute examples/todos.json
    import tempfile, pathlib
    tmpdir = tempfile.mkdtemp(prefix='feel_crud_test_')
    db_file = os.path.join(tmpdir, 'todos.json')
    src_test = src.replace('"examples/todos.json"', json.dumps(db_file)).replace('serve on 3000', '-- serve disabled')

    global_registry().clear()
    interp = Interpreter(filename=src_path, source=src_test,
                         search_paths=[ROOT, os.getcwd()])
    interp.run(src_test)

    server, port = serve_in_thread(port=0)
    try:
        # Initially empty
        status, body = _request(port, '/todos')
        assert status == 200 and body == [], f'init: {status} {body}'
        print("  PASS  GET /todos (empty)")

        # Create
        status, body = _request(port, '/todos', method='POST', body={'title': 'Buy milk'})
        assert status == 201, f'create: {status} {body}'
        assert body['id'] == 1 and body['title'] == 'Buy milk' and body['done'] is False
        print("  PASS  POST /todos (create)")

        # Create second
        status, body = _request(port, '/todos', method='POST', body={'title': 'Read book'})
        assert status == 201 and body['id'] == 2
        print("  PASS  POST /todos (second item, auto-increment id)")

        # Create with missing title
        status, body = _request(port, '/todos', method='POST', body={})
        assert status == 400 and 'missing' in body['error']
        print("  PASS  POST /todos (missing title -> 400)")

        # List
        status, body = _request(port, '/todos')
        assert status == 200 and len(body) == 2
        print("  PASS  GET /todos (2 items)")

        # Find by id
        status, body = _request(port, '/todos/1')
        assert status == 200 and body['title'] == 'Buy milk'
        print("  PASS  GET /todos/1")

        # Find missing
        status, body = _request(port, '/todos/999')
        assert status == 404
        print("  PASS  GET /todos/999 (404)")

        # Mark done
        status, body = _request(port, '/todos/1/done', method='PATCH')
        assert status == 204, f'patch: {status}'
        print("  PASS  PATCH /todos/1/done (204)")

        # Verify done flipped
        status, body = _request(port, '/todos/1')
        assert body['done'] is True
        print("  PASS  GET /todos/1 (done is true)")

        # AI classify
        status, body = _request(port, '/todos/1/classify', method='POST')
        assert status == 200, f'classify: {status} {body}'
        assert body['category'] in ['work', 'personal', 'errand', 'other']
        print("  PASS  POST /todos/1/classify (AI mock)")

        # Delete
        status, body = _request(port, '/todos/1', method='DELETE')
        assert status == 204
        print("  PASS  DELETE /todos/1")

        # Verify list has only 1
        status, body = _request(port, '/todos')
        assert len(body) == 1 and body[0]['id'] == 2
        print("  PASS  GET /todos (after delete)")

    finally:
        server.shutdown()
        server.server_close()
        try:
            os.remove(db_file)
            os.rmdir(tmpdir)
        except OSError:
            pass

    print('\nAll crud_rest e2e tests passed.')


if __name__ == '__main__':
    main()
