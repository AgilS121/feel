"""Minimal WebSocket (RFC 6455) for the Feel interpreter.

Implements text frames + close + ping/pong. Binary frames passed through as
bytes. No extensions (per-message-deflate). Suitable for chat / live-dashboard
use cases.

The handshake and frame I/O reuse the BaseHTTPRequestHandler's connection
socket — we just take over after the upgrade response.
"""

import base64
import hashlib
import os as _os
import struct
import uuid


_GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def _accept_key(client_key):
    """Compute Sec-WebSocket-Accept from client's Sec-WebSocket-Key."""
    sha = hashlib.sha1(client_key.encode('ascii') + _GUID).digest()
    return base64.b64encode(sha).decode('ascii')


def is_websocket_upgrade(headers):
    """Detect a valid RFC 6455 upgrade request from header dict (lowercase keys)."""
    conn = (headers.get('connection') or '').lower()
    upgrade = (headers.get('upgrade') or '').lower()
    version = headers.get('sec-websocket-version') or ''
    return (
        'upgrade' in conn
        and upgrade == 'websocket'
        and version == '13'
        and headers.get('sec-websocket-key')
    )


def perform_handshake(wfile, headers):
    """Write the 101 Switching Protocols response. Caller already validated headers."""
    key = headers.get('sec-websocket-key')
    accept = _accept_key(key)
    wfile.write(
        b"HTTP/1.1 101 Switching Protocols\r\n"
        b"Upgrade: websocket\r\n"
        b"Connection: Upgrade\r\n"
        b"Sec-WebSocket-Accept: " + accept.encode('ascii') + b"\r\n"
        b"\r\n"
    )
    wfile.flush()


class FeelWebSocket:
    """Server-side WebSocket connection. Synchronous, one client per instance."""

    OP_CONT  = 0x0
    OP_TEXT  = 0x1
    OP_BIN   = 0x2
    OP_CLOSE = 0x8
    OP_PING  = 0x9
    OP_PONG  = 0xA

    def __init__(self, rfile, wfile, conn, path):
        self.rfile = rfile
        self.wfile = wfile
        self.conn = conn
        self.path = path
        self.id = str(uuid.uuid4())
        self.closed = False

    def _read_n(self, n):
        buf = b''
        while len(buf) < n:
            chunk = self.rfile.read(n - len(buf))
            if not chunk:
                raise ConnectionError("websocket: unexpected EOF")
            buf += chunk
        return buf

    def _read_frame(self):
        """Read one frame. Returns (opcode, payload_bytes, is_final). Raises on EOF."""
        hdr = self._read_n(2)
        b1, b2 = hdr[0], hdr[1]
        fin = (b1 & 0x80) != 0
        opcode = b1 & 0x0F
        masked = (b2 & 0x80) != 0
        plen = b2 & 0x7F
        if plen == 126:
            plen = struct.unpack('!H', self._read_n(2))[0]
        elif plen == 127:
            plen = struct.unpack('!Q', self._read_n(8))[0]
        mask_key = self._read_n(4) if masked else None
        payload = self._read_n(plen) if plen else b''
        if mask_key:
            payload = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))
        return opcode, payload, fin

    def _send_frame(self, opcode, payload):
        if isinstance(payload, str):
            payload = payload.encode('utf-8')
        plen = len(payload)
        header = bytes([0x80 | opcode])  # FIN=1 + opcode
        if plen < 126:
            header += bytes([plen])
        elif plen < 65536:
            header += bytes([126]) + struct.pack('!H', plen)
        else:
            header += bytes([127]) + struct.pack('!Q', plen)
        # Server frames are NOT masked per RFC 6455 §5.1.
        self.wfile.write(header + payload)
        self.wfile.flush()

    def send(self, msg):
        """Send a text or binary message. str → text, bytes → binary."""
        if self.closed:
            return False
        if isinstance(msg, bytes):
            self._send_frame(self.OP_BIN, msg)
        else:
            text = msg if isinstance(msg, str) else str(msg)
            self._send_frame(self.OP_TEXT, text)
        return True

    def receive(self):
        """Block until next message. Returns str (text), bytes (binary), or None on close."""
        if self.closed:
            return None
        buffered = b''
        buffered_opcode = None
        try:
            while True:
                opcode, payload, fin = self._read_frame()
                if opcode == self.OP_CLOSE:
                    self.closed = True
                    try:
                        self._send_frame(self.OP_CLOSE, payload[:2] if len(payload) >= 2 else b'\x03\xe8')
                    except Exception:
                        pass
                    return None
                if opcode == self.OP_PING:
                    self._send_frame(self.OP_PONG, payload)
                    continue
                if opcode == self.OP_PONG:
                    continue
                if opcode == self.OP_CONT:
                    buffered += payload
                else:
                    buffered = payload
                    buffered_opcode = opcode
                if fin:
                    if buffered_opcode == self.OP_TEXT:
                        return buffered.decode('utf-8', errors='replace')
                    return buffered
        except (ConnectionError, OSError):
            self.closed = True
            return None

    def close(self, code=1000):
        if self.closed:
            return True
        try:
            self._send_frame(self.OP_CLOSE, struct.pack('!H', code))
        except Exception:
            pass
        self.closed = True
        try:
            self.conn.shutdown(2)
        except Exception:
            pass
        return True

    def to_feel_map(self):
        """Expose as a dict so handler can call ws.send / ws.receive / ws.close."""
        return {
            'id':      self.id,
            'path':    self.path,
            'send':    self.send,
            'receive': self.receive,
            'close':   self.close,
        }
