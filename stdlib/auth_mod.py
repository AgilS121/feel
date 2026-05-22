"""auth module — pipeline-friendly helpers for JWT and signed sessions.

Functions:
  auth.extract_bearer(request)           extract token from Authorization header
  auth.require_jwt(request, secret)      verify JWT bearer; throws on failure
  auth.optional_jwt(request, secret)     verify or return nothing

  session.set(response, key, value, secret)   sign + set cookie (returns updated response)
  session.get(request, key, secret)            verify signed cookie, return value or nothing
  session.clear(response, key)                 expire cookie
"""

from stdlib import crypto_mod


def extract_bearer(request):
    """Pull a bearer token from the Authorization header. Returns nothing if absent."""
    headers = request.get('headers') if isinstance(request, dict) else None
    if not isinstance(headers, dict):
        return None
    auth = headers.get('authorization', '')
    if isinstance(auth, str) and auth.startswith('Bearer '):
        return auth[len('Bearer '):]
    return None


def require_jwt(request, secret):
    """Verify a Bearer JWT; throw if missing or invalid. Returns the payload map."""
    token = extract_bearer(request)
    if token is None:
        raise ValueError("auth: missing Authorization Bearer token")
    payload = crypto_mod.jwt_verify(token, secret)
    if payload is None:
        raise ValueError("auth: invalid or expired token")
    return payload


def optional_jwt(request, secret):
    """Same as require_jwt but returns None instead of throwing."""
    token = extract_bearer(request)
    if token is None:
        return None
    return crypto_mod.jwt_verify(token, secret)


EXPORTS_AUTH = {
    'extract_bearer': extract_bearer,
    'require_jwt':    require_jwt,
    'optional_jwt':   optional_jwt,
}


# ---------- session (cookie-based, HMAC-signed) ----------

def _sign_cookie_value(value, secret):
    sig = crypto_mod.hmac_sha256(secret, value)
    return f'{value}.{sig}'


def _verify_cookie_value(signed, secret):
    if not isinstance(signed, str) or '.' not in signed:
        return None
    value, sig = signed.rsplit('.', 1)
    expected = crypto_mod.hmac_sha256(secret, value)
    if expected != sig:
        return None
    return value


def session_set(response, key, value, secret, max_age=86400):
    """Mutates / wraps a response with a signed cookie. Returns the response map."""
    signed = _sign_cookie_value(str(value), secret)
    cookie = f'{key}={signed}; Path=/; Max-Age={int(max_age)}; HttpOnly; SameSite=Lax'
    # response may be a dict or a FeelResponse-shaped map. Stuff into 'cookies' list.
    if not isinstance(response, dict):
        response = {'status': 200, 'body': response}
    cookies = response.get('__cookies__', [])
    cookies.append(cookie)
    response['__cookies__'] = cookies
    return response


def session_get(request, key, secret):
    """Read a signed cookie from the request. Returns the original value or None."""
    headers = request.get('headers') if isinstance(request, dict) else None
    if not isinstance(headers, dict):
        return None
    raw = headers.get('cookie', '')
    if not isinstance(raw, str):
        return None
    for part in raw.split(';'):
        part = part.strip()
        if '=' not in part:
            continue
        k, _, v = part.partition('=')
        if k.strip() == key:
            return _verify_cookie_value(v.strip(), secret)
    return None


def session_clear(response, key):
    """Set an expired cookie to clear the session."""
    if not isinstance(response, dict):
        response = {'status': 200, 'body': response}
    cookie = f'{key}=; Path=/; Max-Age=0; HttpOnly'
    cookies = response.get('__cookies__', [])
    cookies.append(cookie)
    response['__cookies__'] = cookies
    return response


EXPORTS_SESSION = {
    'set':   session_set,
    'get':   session_get,
    'clear': session_clear,
}
