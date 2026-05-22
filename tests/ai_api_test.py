"""End-to-end test for examples/ai_api.feel (in mock mode)."""

import json
import os
import sys
import urllib.request
import urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# Force mock provider for deterministic test
os.environ['FEEL_AI_PROVIDER'] = 'mock'

from runtime.router import global_registry
from runtime.http import serve_in_thread


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
    import os.path as osp

    # Load the ai_api.feel but skip the serve line so it doesn't block
    src_path = osp.join(ROOT, 'examples', 'ai_api.feel')
    with open(src_path, encoding='utf-8') as f:
        src = f.read()
    src_no_serve = src.replace('serve on 3000', '-- serve disabled for test')

    global_registry().clear()
    interp = Interpreter(filename=src_path, source=src_no_serve)
    interp.run(src_no_serve)

    server, port = serve_in_thread(port=0)
    try:
        # GET / index
        status, body = _request(port, '/')
        assert status == 200, f'index: {status}'
        assert body['service'] == 'Feel AI demo'
        assert body['provider'] == 'mock'
        print("  PASS  GET /")

        # GET /ask?q=test
        status, body = _request(port, '/ask?q=hello')
        assert status == 200, f'ask: {status}'
        assert body['question'] == 'hello'
        assert '[mock-ai]' in body['answer']
        print("  PASS  GET /ask")

        # GET /ask without q -> 400
        status, body = _request(port, '/ask')
        assert status == 400
        print("  PASS  GET /ask (missing q -> 400)")

        # POST /summarize
        status, body = _request(port, '/summarize', method='POST',
                                body={'text': 'A long piece of text to summarize.'})
        assert status == 200
        assert body['original_length'] == 34
        assert '[mock-summary]' in body['summary']
        print("  PASS  POST /summarize")

        # POST /classify
        status, body = _request(port, '/classify', method='POST',
                                body={'text': 'Found a bug in login'})
        assert status == 200
        assert body['label'] in ['bug', 'feature', 'question', 'other']
        print("  PASS  POST /classify")

        # POST /chat
        status, body = _request(port, '/chat', method='POST',
                                body={'messages': [{'role': 'user', 'content': 'hi'}]})
        assert status == 200
        assert '[mock-chat]' in body['reply']
        print("  PASS  POST /chat")
    finally:
        server.shutdown()
        server.server_close()

    print('\nAll ai_api e2e tests passed.')


if __name__ == '__main__':
    main()
