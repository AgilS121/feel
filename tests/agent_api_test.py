"""End-to-end test for examples/agent_api.feel (mock mode)."""

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

    src_path = os.path.join(ROOT, 'examples', 'agent_api.feel')
    with open(src_path, encoding='utf-8') as f:
        src = f.read()
    src_test = src.replace('serve on 3000', '-- serve disabled for test')

    global_registry().clear()
    interp = Interpreter(filename=src_path, source=src_test,
                         search_paths=[ROOT, os.getcwd()])
    interp.run(src_test)

    server, port = serve_in_thread(port=0)
    try:
        # GET /
        status, body = _request(port, '/')
        assert status == 200
        assert body['service'] == 'Feel agents demo'
        print("  PASS  GET /")

        # POST /calc — agent with tools
        status, body = _request(port, '/calc', method='POST',
                                body={'q': 'what is 12 plus 30?'})
        assert status == 200, f'calc: {status} {body}'
        assert body['question'] == 'what is 12 plus 30?'
        # mock-agent prefix mentions tool count
        assert '[mock-agent]' in body['answer']
        assert '3 tools' in body['answer'], f'answer: {body["answer"]}'
        print("  PASS  POST /calc (agent w/ tools)")

        # POST /translate — agent without tools
        status, body = _request(port, '/translate', method='POST',
                                body={'text': 'hello world'})
        assert status == 200
        assert '[mock-agent]' in body['indonesian']
        assert '0 tools' in body['indonesian']
        print("  PASS  POST /translate (agent w/o tools)")

        # GET /tools — introspection
        status, body = _request(port, '/tools')
        assert status == 200
        assert len(body) == 3
        names = sorted(t['name'] for t in body)
        assert names == ['add', 'multiply', 'subtract'], f'names: {names}'
        # Verify metadata exposed
        for t in body:
            assert 'description' in t and t['description']
            assert t['parameters'] == ['a', 'b']
        print("  PASS  GET /tools (introspection)")
    finally:
        server.shutdown()
        server.server_close()

    print('\nAll agent_api e2e tests passed.')


if __name__ == '__main__':
    main()
