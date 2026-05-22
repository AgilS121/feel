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


def chat_with_tools(messages, system=None, tools=None, model=None,
                    tool_executor=None, max_iterations=8):
    """Multi-turn chat where the LLM can call provided tools.

    `tools`     : list of dicts shaped for Claude's tool_use API
    `tool_executor` : callable(name: str, args: dict) -> result (any JSON-able)

    Returns the final assistant text after all tool calls resolve.
    """
    p = _detect_provider()
    if p == 'mock':
        # Mock: ignore tools, return canned response that mentions tool count
        n_tools = len(tools) if tools else 0
        last_user = next((m['content'] for m in reversed(messages)
                         if isinstance(m, dict) and m.get('role') == 'user'), '')
        if isinstance(last_user, list):
            last_user = str(last_user)
        return f'[mock-agent] {n_tools} tools available. User said: {last_user[:60]!r}'
    if p == 'claude':
        return _claude_chat_with_tools(
            messages, system=system, tools=tools, model=model,
            tool_executor=tool_executor, max_iterations=max_iterations,
        )
    raise RuntimeError(f"unknown AI provider: {p!r}")


def _claude_chat_with_tools(messages, system, tools, model, tool_executor, max_iterations):
    """Implements Claude's tool_use loop."""
    api_key = os.environ.get('ANTHROPIC_API_KEY')
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set; cannot use 'claude' provider")

    # Copy messages so we don't mutate caller's list
    msgs = list(messages)

    for _ in range(max_iterations):
        payload = {
            'model': model or _model(),
            'max_tokens': 1024,
            'messages': msgs,
        }
        if system:
            payload['system'] = system
        if tools:
            payload['tools'] = tools

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
        except urllib.error.HTTPError as e:
            err_body = e.read().decode('utf-8', errors='replace')
            raise RuntimeError(f'Claude API error {e.code}: {err_body}')

        content = body.get('content', [])
        stop_reason = body.get('stop_reason')

        if stop_reason != 'tool_use':
            # Final answer — concatenate text blocks
            texts = [b['text'] for b in content if b.get('type') == 'text']
            return '\n'.join(texts).strip()

        # Append the assistant message that requested tool use, verbatim
        msgs.append({'role': 'assistant', 'content': content})

        # Execute every tool_use block, build tool_result blocks
        tool_results = []
        for block in content:
            if block.get('type') != 'tool_use':
                continue
            name = block['name']
            args = block.get('input', {})
            tool_use_id = block['id']
            try:
                result = tool_executor(name, args) if tool_executor else None
                result_str = _stringify(result)
                tool_results.append({
                    'type': 'tool_result',
                    'tool_use_id': tool_use_id,
                    'content': result_str,
                })
            except Exception as exc:
                tool_results.append({
                    'type': 'tool_result',
                    'tool_use_id': tool_use_id,
                    'content': f'tool error: {exc}',
                    'is_error': True,
                })

        msgs.append({'role': 'user', 'content': tool_results})

    raise RuntimeError(f'chat_with_tools: exceeded {max_iterations} tool-use iterations')


def _stringify(v):
    if v is None: return ''
    if isinstance(v, (dict, list)):
        return _json.dumps(v)
    return str(v)


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
