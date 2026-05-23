"""http module — outbound HTTP client.

Wraps Python's urllib so Feel programs can call external APIs (payment gateway,
webhook, third-party REST). Same shape as Go net/http on the compile side.

Functions:
  http.get(url, headers?, timeout?)              → response
  http.post(url, body, headers?, timeout?)       → response (body auto-JSON if dict/list)
  http.put(url, body, headers?, timeout?)        → response
  http.delete(url, headers?, timeout?)           → response
  http.request(method, url, opts?)               → response  (opts: body, headers, timeout)
  http.get_json(url, headers?, timeout?)         → parsed JSON body (shortcut)

Response object (record):
  status        int  — HTTP status code
  body          string — raw body (or already-parsed dict/list if Content-Type is JSON)
  headers       map  — response headers (lowercased keys)
  ok            bool — true when 200 <= status < 300
"""

import json as _json
import urllib.request
import urllib.error
from urllib.parse import urlencode


_DEFAULT_TIMEOUT = 30.0


def _normalize_headers(h):
    if h is None:
        return {}
    if isinstance(h, dict):
        return {str(k): str(v) for k, v in h.items()}
    raise ValueError("headers must be a map")


def _encode_body(body, headers):
    """Return (bytes, headers) — auto-JSON dict/list, leave str/bytes alone."""
    if body is None:
        return None, headers
    if isinstance(body, (dict, list)):
        data = _json.dumps(body).encode('utf-8')
        if 'content-type' not in {k.lower() for k in headers.keys()}:
            headers['Content-Type'] = 'application/json'
        return data, headers
    if isinstance(body, str):
        return body.encode('utf-8'), headers
    if isinstance(body, bytes):
        return body, headers
    raise ValueError("body must be string, bytes, map, or list")


def _do(method, url, body=None, headers=None, timeout=None):
    headers = _normalize_headers(headers)
    timeout = float(timeout) if timeout is not None else _DEFAULT_TIMEOUT
    data, headers = _encode_body(body, headers)

    req = urllib.request.Request(url, data=data, method=method.upper(), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            resp_headers = {k.lower(): v for k, v in r.headers.items()}
            status = r.status
    except urllib.error.HTTPError as e:
        raw = e.read()
        resp_headers = {k.lower(): v for k, v in e.headers.items()} if e.headers else {}
        status = e.code
    except urllib.error.URLError as e:
        raise RuntimeError(f"http.{method.lower()}: {e.reason}")

    ct = resp_headers.get('content-type', '')
    text = raw.decode('utf-8', errors='replace') if raw else ''
    body_val = text
    if 'application/json' in ct.lower() and text:
        try:
            body_val = _json.loads(text)
        except _json.JSONDecodeError:
            body_val = text

    return {
        'status':  status,
        'body':    body_val,
        'headers': resp_headers,
        'ok':      200 <= status < 300,
    }


def get(url, headers=None, timeout=None):
    return _do('GET', url, body=None, headers=headers, timeout=timeout)


def post(url, body=None, headers=None, timeout=None):
    return _do('POST', url, body=body, headers=headers, timeout=timeout)


def put(url, body=None, headers=None, timeout=None):
    return _do('PUT', url, body=body, headers=headers, timeout=timeout)


def delete(url, headers=None, timeout=None):
    return _do('DELETE', url, body=None, headers=headers, timeout=timeout)


def request(method, url, opts=None):
    opts = opts or {}
    return _do(method, url,
               body=opts.get('body'),
               headers=opts.get('headers'),
               timeout=opts.get('timeout'))


def get_json(url, headers=None, timeout=None):
    """GET and return the parsed JSON body directly. Throws if status not 2xx
    or body is not JSON."""
    h = _normalize_headers(headers)
    if 'accept' not in {k.lower() for k in h.keys()}:
        h['Accept'] = 'application/json'
    resp = get(url, headers=h, timeout=timeout)
    if not resp['ok']:
        raise RuntimeError(f"http.get_json: status {resp['status']}")
    body = resp['body']
    if isinstance(body, (dict, list)):
        return body
    # Try to parse if server didn't set content-type
    try:
        return _json.loads(body) if isinstance(body, str) else body
    except _json.JSONDecodeError:
        raise RuntimeError("http.get_json: response is not valid JSON")


EXPORTS = {
    'get':       get,
    'post':      post,
    'put':       put,
    'delete':    delete,
    'request':   request,
    'get_json':  get_json,
}
