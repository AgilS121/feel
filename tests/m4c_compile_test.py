"""M4-C: verify compiled REST API binary serves identical responses to interpreter."""

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
import urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def _has_go():
    try:
        r = subprocess.run(['go', 'version'], capture_output=True, timeout=5)
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _find_port():
    s = socket.socket(); s.bind(('127.0.0.1', 0)); port = s.getsockname()[1]; s.close()
    return port


def _wait_for_port(port, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.1)
    return False


def _http(port, path, method='GET', body=None):
    url = f'http://127.0.0.1:{port}{path}'
    data = None
    h = {}
    if body is not None:
        data = json.dumps(body).encode('utf-8')
        h['Content-Type'] = 'application/json'
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            payload = resp.read().decode('utf-8')
            return resp.status, json.loads(payload) if payload else None
    except urllib.error.HTTPError as e:
        payload = e.read().decode('utf-8')
        return e.code, json.loads(payload) if payload else None


def _run_server(cmd, port):
    """Start server in background; return Popen. Caller kills when done."""
    env = os.environ.copy()
    env['FEEL_AI_PROVIDER'] = 'mock'
    p = subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if not _wait_for_port(port, timeout=10):
        p.terminate()
        p.wait()
        raise RuntimeError(f'server on port {port} never came up')
    return p


def _build_compiled(feel_path):
    """Build a binary, replacing 'serve on 3000' with a fresh ephemeral port."""
    import tempfile
    port = _find_port()
    with open(feel_path, encoding='utf-8') as f:
        src = f.read()
    src = src.replace('serve on 3000 cors', f'serve on {port} cors').replace('serve on 3000', f'serve on {port}')
    tmpdir = tempfile.mkdtemp(prefix='feel_m4c_')
    src_path = os.path.join(tmpdir, 'patched.feel')
    with open(src_path, 'w', encoding='utf-8') as f:
        f.write(src)
    exe = os.path.join(tmpdir, 'server' + ('.exe' if os.name == 'nt' else ''))
    r = subprocess.run(
        [sys.executable, os.path.join(ROOT, 'main.py'), 'build', src_path, '-o', exe],
        capture_output=True, text=True, timeout=120, cwd=ROOT,
    )
    if r.returncode != 0:
        raise RuntimeError(f'build failed: {r.stdout}\n{r.stderr}')
    return exe, port


def _build_interpreted(feel_path):
    """Run the .feel file via interpreter on an ephemeral port. Returns (cmd, port)."""
    import tempfile
    port = _find_port()
    with open(feel_path, encoding='utf-8') as f:
        src = f.read()
    src = src.replace('serve on 3000 cors', f'serve on {port} cors').replace('serve on 3000', f'serve on {port}')
    tmpdir = tempfile.mkdtemp(prefix='feel_m4c_interp_')
    src_path = os.path.join(tmpdir, 'patched.feel')
    with open(src_path, 'w', encoding='utf-8') as f:
        f.write(src)
    return [sys.executable, os.path.join(ROOT, 'main.py'), src_path], port


def parity(label, feel_path, requests):
    """For each request in `requests`, hit both interpreter and compiled and compare."""
    # Compiled
    exe, comp_port = _build_compiled(feel_path)
    interp_cmd, interp_port = _build_interpreted(feel_path)
    comp_proc = _run_server([exe], comp_port)
    interp_proc = _run_server(interp_cmd, interp_port)
    try:
        for r in requests:
            method = r.get('method', 'GET')
            path = r['path']
            body = r.get('body')
            cs, cb = _http(comp_port, path, method, body)
            is_, ib = _http(interp_port, path, method, body)
            if cs != is_ or cb != ib:
                print(f'  FAIL  {label}: {method} {path}')
                print(f'    compiled:    status={cs}  body={cb}')
                print(f'    interpreter: status={is_}  body={ib}')
                return False
    finally:
        comp_proc.terminate(); comp_proc.wait()
        interp_proc.terminate(); interp_proc.wait()
    print(f'  PASS  {label}  ({len(requests)} request{"s" if len(requests) != 1 else ""})')
    return True


def main():
    if not _has_go():
        print('M4-C tests skipped (Go toolchain not installed)')
        return 0

    cases = [
        ('hello_api', os.path.join(ROOT, 'examples', 'hello_api.feel'), [
            {'path': '/hello'},
            {'path': '/greet/Budi'},
            {'path': '/echo', 'method': 'POST', 'body': {'x': 1, 'y': [2, 3]}},
        ]),
        ('ai_api', os.path.join(ROOT, 'examples', 'ai_api.feel'), [
            {'path': '/'},
            {'path': '/ask?q=hello'},
            {'path': '/summarize', 'method': 'POST', 'body': {'text': 'short text'}},
            {'path': '/classify', 'method': 'POST', 'body': {'text': 'bug here'}},
            {'path': '/chat', 'method': 'POST', 'body': {'messages': [{'role': 'user', 'content': 'hi'}]}},
        ]),
        ('agent_api', os.path.join(ROOT, 'examples', 'agent_api.feel'), [
            {'path': '/'},
            {'path': '/calc', 'method': 'POST', 'body': {'q': 'what is 2+2?'}},
            {'path': '/tools'},
        ]),
    ]

    failed = 0
    for label, path, reqs in cases:
        try:
            ok = parity(label, path, reqs)
            if not ok:
                failed += 1
        except Exception as e:
            print(f'  FAIL  {label}: {e}')
            failed += 1

    if failed:
        print(f'\n{failed} M4-C parity tests failed.')
        return 1
    print(f'\nAll {len(cases)} M4-C parity tests passed.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
