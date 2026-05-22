"""End-to-end test for examples/crud_db.feel (SQLite-backed CRUD)."""

import json
import os
import sys
import tempfile
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

    # Use a temp db
    tmpdir = tempfile.mkdtemp(prefix='feel_db_test_')
    db_file = os.path.join(tmpdir, 'todos.db')

    src_path = os.path.join(ROOT, 'examples', 'crud_db.feel')
    with open(src_path, encoding='utf-8') as f:
        src = f.read()
    src_test = (src
                .replace('"examples/todos.db"', json.dumps(db_file))
                .replace('serve on 3000', '-- serve disabled'))

    global_registry().clear()
    interp = Interpreter(filename=src_path, source=src_test,
                         search_paths=[ROOT, os.getcwd()])
    interp.run(src_test)

    server, port = serve_in_thread(port=0)
    try:
        status, body = _request(port, '/todos')
        assert status == 200 and body == []
        print("  PASS  GET /todos (empty)")

        status, body = _request(port, '/todos', method='POST', body={'title': 'Buy milk'})
        assert status == 201
        assert body['id'] == 1 and body['title'] == 'Buy milk' and body['done'] == 0
        print("  PASS  POST /todos (create)")

        status, body = _request(port, '/todos', method='POST', body={'title': 'Read book'})
        assert status == 201 and body['id'] == 2
        print("  PASS  POST /todos (auto-increment id)")

        status, body = _request(port, '/todos', method='POST', body={})
        assert status == 400
        print("  PASS  POST /todos (missing title -> 400)")

        status, body = _request(port, '/todos')
        assert status == 200 and len(body) == 2
        print("  PASS  GET /todos (list 2)")

        status, body = _request(port, '/todos/1')
        assert status == 200 and body['title'] == 'Buy milk'
        print("  PASS  GET /todos/1")

        status, body = _request(port, '/todos/999')
        assert status == 404
        print("  PASS  GET /todos/999 (404)")

        status, body = _request(port, '/todos/1/done', method='PATCH')
        assert status == 204
        print("  PASS  PATCH /todos/1/done")

        status, body = _request(port, '/todos/1')
        assert body['done'] == 1
        print("  PASS  GET /todos/1 (done flipped)")

        status, body = _request(port, '/todos/999/done', method='PATCH')
        assert status == 404
        print("  PASS  PATCH /todos/999/done (404)")

        status, body = _request(port, '/todos/1/classify', method='POST')
        assert status == 200
        assert body['category'] in ['work', 'personal', 'errand', 'other']
        print("  PASS  POST /todos/1/classify (AI)")

        status, body = _request(port, '/chat', method='POST', body={'message': 'hello'})
        assert status == 200
        assert '[mock-agent]' in body['reply']
        print("  PASS  POST /chat (agent)")

        status, body = _request(port, '/todos/1', method='DELETE')
        assert status == 204
        print("  PASS  DELETE /todos/1")

        status, body = _request(port, '/todos')
        assert len(body) == 1 and body[0]['id'] == 2
        print("  PASS  GET /todos (1 remaining)")

    finally:
        server.shutdown()
        server.server_close()
        try:
            os.remove(db_file)
            os.rmdir(tmpdir)
        except OSError:
            pass

    print('\nAll crud_db e2e tests passed.')


if __name__ == '__main__':
    main()
