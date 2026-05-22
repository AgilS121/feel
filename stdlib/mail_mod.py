"""mail module — SMTP send + mock provider for tests.

Providers (auto-selected):
  mock  (default if FEEL_MAIL_PROVIDER not set or = 'mock')
        — Stores sent messages in memory. Use mail.sent() to inspect in tests.
  smtp  (FEEL_MAIL_PROVIDER=smtp)
        — Uses Python stdlib smtplib. Reads:
          FEEL_SMTP_HOST  (required)
          FEEL_SMTP_PORT  (default 587)
          FEEL_SMTP_USER  (optional)
          FEEL_SMTP_PASS  (optional)
          FEEL_SMTP_TLS   (default 'true')

Functions:
  mail.send(map { to, subject, body, from? })  → bool
  mail.provider()                               → 'mock' | 'smtp'
  mail.sent()                                   → list of sent messages (mock only)
  mail.clear_sent()                             → reset mock buffer
"""

import os
import smtplib
from email.mime.text import MIMEText


_mock_sent = []


def provider():
    return (os.environ.get('FEEL_MAIL_PROVIDER') or 'mock').lower()


def sent():
    """Return list of mock-sent messages. SMTP mode returns empty."""
    return list(_mock_sent)


def clear_sent():
    _mock_sent.clear()
    return True


def _required(d, key):
    if not isinstance(d, dict) or key not in d:
        raise ValueError(f"mail.send: missing '{key}'")
    return d[key]


def send(message):
    """Send an email. `message` is a map with to, subject, body, optional from."""
    if not isinstance(message, dict):
        raise ValueError("mail.send: expected a map")
    to = str(_required(message, 'to'))
    subject = str(_required(message, 'subject'))
    body = str(_required(message, 'body'))
    sender = str(message.get('from', os.environ.get('FEEL_MAIL_FROM', 'noreply@feel.local')))

    p = provider()
    if p == 'mock':
        _mock_sent.append({
            'to': to, 'subject': subject, 'body': body, 'from': sender,
        })
        return True

    if p == 'smtp':
        host = os.environ.get('FEEL_SMTP_HOST')
        if not host:
            raise RuntimeError("mail.send: FEEL_SMTP_HOST not set")
        port = int(os.environ.get('FEEL_SMTP_PORT', '587'))
        user = os.environ.get('FEEL_SMTP_USER')
        pw = os.environ.get('FEEL_SMTP_PASS')
        use_tls = (os.environ.get('FEEL_SMTP_TLS', 'true').lower() != 'false')

        msg = MIMEText(body, 'plain', 'utf-8')
        msg['Subject'] = subject
        msg['From'] = sender
        msg['To'] = to

        try:
            with smtplib.SMTP(host, port, timeout=30) as s:
                if use_tls:
                    s.starttls()
                if user:
                    s.login(user, pw or '')
                s.send_message(msg)
        except Exception as e:
            raise RuntimeError(f"mail.send SMTP error: {e}")
        return True

    raise RuntimeError(f"mail.send: unknown provider {p!r}")


EXPORTS = {
    'send':       send,
    'provider':   provider,
    'sent':       sent,
    'clear_sent': clear_sent,
}
