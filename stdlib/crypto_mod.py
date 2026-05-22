"""crypto module — password hashing, JWT, HMAC, random tokens, base64.

All operations use Python stdlib only (no external deps):
  - PBKDF2-SHA256 for password hashing  (hashlib.pbkdf2_hmac)
  - HS256 for JWT                       (hmac + hashlib)
  - secrets module for cryptographic randomness

Functions:
  crypto.hash_password(password, iterations?)    -> versioned hash string
  crypto.verify_password(password, hashed)       -> bool (constant-time compare)
  crypto.jwt_sign(payload_map, secret)           -> JWT string (HS256)
  crypto.jwt_verify(token, secret)               -> payload map, or nothing if invalid
  crypto.hmac_sha256(key, message)               -> hex string
  crypto.random_bytes(n)                         -> hex string (2n chars)
  crypto.random_token(n?)                        -> URL-safe random token
  crypto.base64_encode(text_or_bytes)            -> base64 string
  crypto.base64_decode(b64_string)               -> decoded text (UTF-8)
"""

import base64 as _b64
import hashlib
import hmac
import json as _json
import secrets


def hash_password(password, iterations=100000):
    """Hash a password using PBKDF2-SHA256. Returns a self-describing string:
    pbkdf2_sha256$<iter>$<base64-salt>$<base64-hash>
    """
    iters = int(iterations)
    salt = secrets.token_bytes(16)
    derived = hashlib.pbkdf2_hmac('sha256', str(password).encode('utf-8'), salt, iters)
    return f'pbkdf2_sha256${iters}${_b64.b64encode(salt).decode()}${_b64.b64encode(derived).decode()}'


def verify_password(password, hashed):
    """Constant-time compare a password against a hash produced by hash_password."""
    try:
        parts = str(hashed).split('$')
        if len(parts) != 4:
            return False
        algo, iter_str, salt_b64, hash_b64 = parts
        if algo != 'pbkdf2_sha256':
            return False
        iters = int(iter_str)
        salt = _b64.b64decode(salt_b64)
        expected = _b64.b64decode(hash_b64)
        derived = hashlib.pbkdf2_hmac('sha256', str(password).encode('utf-8'), salt, iters)
        return hmac.compare_digest(derived, expected)
    except Exception:
        return False


def jwt_sign(payload, secret):
    """Sign a payload (map) into a JWT (HS256). Returns the token string."""
    if not isinstance(payload, dict):
        raise ValueError("jwt_sign: payload must be a map")
    # sort_keys ensures Python and Go produce byte-identical JSON for the same
    # payload, so a token signed by one runtime verifies in the other.
    header = {'alg': 'HS256', 'typ': 'JWT'}
    h_enc = _b64url_encode(_json.dumps(header, separators=(',', ':'), sort_keys=True).encode('utf-8'))
    p_enc = _b64url_encode(_json.dumps(payload, separators=(',', ':'), sort_keys=True).encode('utf-8'))
    signing_input = f'{h_enc}.{p_enc}'.encode('utf-8')
    sig = hmac.new(str(secret).encode('utf-8'), signing_input, hashlib.sha256).digest()
    s_enc = _b64url_encode(sig)
    return f'{h_enc}.{p_enc}.{s_enc}'


def jwt_verify(token, secret):
    """Verify a JWT signature. Returns the payload dict on success, nothing on failure."""
    try:
        parts = str(token).split('.')
        if len(parts) != 3:
            return None
        h_enc, p_enc, s_enc = parts
        signing_input = f'{h_enc}.{p_enc}'.encode('utf-8')
        expected_sig = hmac.new(str(secret).encode('utf-8'), signing_input, hashlib.sha256).digest()
        actual_sig = _b64url_decode(s_enc)
        if not hmac.compare_digest(expected_sig, actual_sig):
            return None
        return _json.loads(_b64url_decode(p_enc).decode('utf-8'))
    except Exception:
        return None


def hmac_sha256(key, message):
    """HMAC-SHA256 as hex string."""
    return hmac.new(str(key).encode('utf-8'), str(message).encode('utf-8'), hashlib.sha256).hexdigest()


def random_bytes(n):
    """Cryptographically secure random bytes, returned as hex string of length 2n."""
    return secrets.token_hex(int(n))


def random_token(n=32):
    """URL-safe random token, ~n bytes of entropy."""
    return secrets.token_urlsafe(int(n))


def base64_encode(data):
    """Base64 encode. Accepts string (encoded to UTF-8) or bytes."""
    if isinstance(data, str):
        data = data.encode('utf-8')
    return _b64.b64encode(data).decode('ascii')


def base64_decode(s):
    """Base64 decode. Returns string (assumes UTF-8 content)."""
    return _b64.b64decode(str(s)).decode('utf-8')


def _b64url_encode(data):
    return _b64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii')


def _b64url_decode(s):
    pad = 4 - (len(s) % 4)
    if pad != 4:
        s = s + ('=' * pad)
    return _b64.urlsafe_b64decode(s.encode('ascii'))


EXPORTS = {
    'hash_password':   hash_password,
    'verify_password': verify_password,
    'jwt_sign':        jwt_sign,
    'jwt_verify':      jwt_verify,
    'hmac_sha256':     hmac_sha256,
    'random_bytes':    random_bytes,
    'random_token':    random_token,
    'base64_encode':   base64_encode,
    'base64_decode':   base64_decode,
}
