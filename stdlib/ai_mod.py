"""ai module — first-class AI primitives.

Providers:
- 'mock' (default if no API key): deterministic responses, for tests & demos
- 'claude': Anthropic API via raw HTTPS (no SDK dependency)
- 'openai': stub for M3

Configuration via env vars:
  FEEL_AI_PROVIDER=claude|mock|openai   (default: 'claude' if ANTHROPIC_API_KEY set, else 'mock')
  ANTHROPIC_API_KEY=sk-ant-...
  FEEL_AI_MODEL=claude-sonnet-4-6       (default: claude-sonnet-4-6)
"""

import json as _json
import os
import urllib.request
import urllib.error


def _detect_provider():
    p = os.environ.get('FEEL_AI_PROVIDER')
    if p:
        return p.lower()
    if os.environ.get('ANTHROPIC_API_KEY'):
        return 'claude'
    return 'mock'


def _model():
    return os.environ.get('FEEL_AI_MODEL', 'claude-sonnet-4-6')


# ---------- Mock provider (deterministic, no network) ----------

def _mock_ask(prompt, **kwargs):
    # Pseudo-deterministic: short echo with hash-based suffix for variety
    snippet = prompt[:80].replace('\n', ' ')
    return f'[mock-ai] response to: {snippet!r}'


def _mock_summarize(text, **kwargs):
    n = len(text)
    words = text.split()
    first_few = ' '.join(words[:8])
    return f'[mock-summary] {n} chars, starts: {first_few}'


def _mock_classify(text, options, **kwargs):
    # Deterministic: hash text, pick option by modulo
    if not options:
        return None
    idx = abs(hash(text)) % len(options)
    return options[idx]


def _mock_chat(messages, **kwargs):
    user_msgs = [m for m in messages if isinstance(m, dict) and m.get('role') == 'user']
    last = user_msgs[-1] if user_msgs else None
    content = last.get('content', '') if last else ''
    return f'[mock-chat] last user said: {content[:60]!r}'


# ---------- Claude provider (raw HTTPS, no SDK) ----------

def _claude_call(messages, system=None, max_tokens=1024, model=None):
    api_key = os.environ.get('ANTHROPIC_API_KEY')
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set; cannot use 'claude' provider")
    payload = {
        'model': model or _model(),
        'max_tokens': max_tokens,
        'messages': messages,
    }
    if system:
        payload['system'] = system
    data = _json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(
        'https://api.anthropic.com/v1/messages',
        data=data,
        method='POST',
        headers={
            'Content-Type': 'application/json',
            'x-api-key': api_key,
            'anthropic-version': '2023-06-01',
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = _json.loads(resp.read().decode('utf-8'))
            # Claude response shape: { content: [{ type: 'text', text: '...' }, ...] }
            for block in body.get('content', []):
                if block.get('type') == 'text':
                    return block.get('text', '')
            return ''
    except urllib.error.HTTPError as e:
        err_body = e.read().decode('utf-8', errors='replace')
        raise RuntimeError(f'Claude API error {e.code}: {err_body}')


def _claude_ask(prompt, **kwargs):
    return _claude_call([{'role': 'user', 'content': prompt}], **kwargs)


def _claude_summarize(text, **kwargs):
    return _claude_call(
        [{'role': 'user', 'content': f'Summarize the following text in 1-2 sentences:\n\n{text}'}],
        **kwargs
    )


def _claude_classify(text, options, **kwargs):
    options_str = ', '.join(f'"{o}"' for o in options)
    prompt = (
        f'Classify the following text into exactly one of these categories: [{options_str}].\n'
        f'Respond with ONLY the category name, no explanation.\n\n'
        f'Text: {text}'
    )
    raw = _claude_call([{'role': 'user', 'content': prompt}], **kwargs)
    # Strip quotes, whitespace
    answer = raw.strip().strip('"').strip("'")
    # Find which option matches (case-insensitive prefix)
    for opt in options:
        if answer.lower().startswith(opt.lower()) or opt.lower() in answer.lower():
            return opt
    return answer


def _claude_chat(messages, system=None, **kwargs):
    return _claude_call(messages, system=system, **kwargs)


# ---------- Dispatcher (provider-agnostic public API) ----------

def ask(prompt, **kwargs):
    """Send a single-turn prompt, return text response."""
    p = _detect_provider()
    if p == 'mock':
        return _mock_ask(prompt, **kwargs)
    if p == 'claude':
        return _claude_ask(prompt, **kwargs)
    raise RuntimeError(f"unknown AI provider: {p!r}")


def summarize(text, **kwargs):
    """Summarize text in 1-2 sentences."""
    p = _detect_provider()
    if p == 'mock':
        return _mock_summarize(text, **kwargs)
    if p == 'claude':
        return _claude_summarize(text, **kwargs)
    raise RuntimeError(f"unknown AI provider: {p!r}")


def classify(text, options, **kwargs):
    """Classify text into one of `options` (list of strings)."""
    if not isinstance(options, list) or not options:
        raise ValueError("classify: 'options' must be a non-empty list of strings")
    p = _detect_provider()
    if p == 'mock':
        return _mock_classify(text, options, **kwargs)
    if p == 'claude':
        return _claude_classify(text, options, **kwargs)
    raise RuntimeError(f"unknown AI provider: {p!r}")


def chat(messages, system=None, **kwargs):
    """Multi-turn chat. messages is a list of map { role, content }."""
    if not isinstance(messages, list):
        raise ValueError("chat: 'messages' must be a list of {role, content} dicts")
    p = _detect_provider()
    if p == 'mock':
        return _mock_chat(messages, **kwargs)
    if p == 'claude':
        return _claude_chat(messages, system=system, **kwargs)
    raise RuntimeError(f"unknown AI provider: {p!r}")


def provider():
    """Return the currently-active provider name."""
    return _detect_provider()


EXPORTS = {
    'ask':       ask,
    'summarize': summarize,
    'classify':  classify,
    'chat':      chat,
    'provider':  provider,
}
