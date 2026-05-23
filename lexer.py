import re

TOKENS = [
    ('COMMENT',   r'--[^\n]*'),
    ('NUMBER',    r'\d+(\.\d+)?'),
    ('STRING',    r'"(?:[^"\\]|\\.)*"'),
    ('ARROW',     r'->'),
    ('PIPE',      r'\|'),
    ('LBRACE',    r'\{'),
    ('RBRACE',    r'\}'),
    ('COLON',     r':'),
    ('COMMA',     r','),
    ('DOT',       r'\.'),
    ('PLUS',      r'\+'),
    ('MINUS',     r'-'),
    ('STAR',      r'\*'),
    ('SLASH',     r'/'),
    ('EQ',        r'=='),
    ('NEQ',       r'!='),
    ('GTE',       r'>='),
    ('LTE',       r'<='),
    ('GT',        r'>'),
    ('LT',        r'<'),
    ('ASSIGN',    r'='),
    ('LPAREN',    r'\('),
    ('RPAREN',    r'\)'),
    ('LBRACKET',  r'\['),
    ('RBRACKET',  r'\]'),
    ('NEWLINE',   r'\n'),
    ('SKIP',      r'[ \t\r]+'),
    ('KEYWORD',   r'\b(let|define|taking|show|record|when|otherwise|repeat|times|for|in|and|or|not|true|false|nothing|try|catch|throw|map|import|from|expose|assert|fn|do|route|respond|serve|on|expects|tool|agent|cors|GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\b'),
    ('IDENT',     r'[a-zA-Z_][a-zA-Z0-9_]*'),
    ('MISMATCH',  r'.'),
]

TOKEN_RE = re.compile('|'.join(f'(?P<{name}>{pattern})' for name, pattern in TOKENS))


_ESC = {
    'n': '\n', 't': '\t', 'r': '\r', '0': '\0',
    '"': '"', '\\': '\\',
    '{': '\x00',   # placeholder: literal `{` (interpolator restores after substitution)
    '}': '\x01',   # placeholder: literal `}`
}


def _unescape(s):
    """Process \X escape sequences inside a string literal body."""
    out = []
    i = 0
    while i < len(s):
        c = s[i]
        if c == '\\' and i + 1 < len(s):
            n = s[i + 1]
            out.append(_ESC.get(n, n))   # unknown escape: keep the next char as-is
            i += 2
        else:
            out.append(c)
            i += 1
    return ''.join(out)


class Token:
    __slots__ = ('type', 'value', 'line', 'col', 'offset')

    def __init__(self, type_, value, line, col, offset=0):
        self.type = type_
        self.value = value
        self.line = line
        self.col = col
        self.offset = offset

    def __repr__(self):
        return f'Token({self.type}, {self.value!r}, {self.line}:{self.col})'


def tokenize(source, filename='<input>', keep_trivia=False):
    """Tokenize source. By default drops comments and most whitespace.
    keep_trivia=True keeps COMMENT and NEWLINE tokens for tools like feelfmt.
    """
    from errors import FeelError
    tokens = []
    line = 1
    line_start = 0  # offset of start of current line
    for m in TOKEN_RE.finditer(source):
        kind = m.lastgroup
        value = m.group()
        col = m.start() - line_start + 1
        if kind == 'SKIP':
            continue
        if kind == 'COMMENT':
            if keep_trivia:
                tokens.append(Token('COMMENT', value, line, col, m.start()))
            continue
        if kind == 'NEWLINE':
            tokens.append(Token('NEWLINE', '\n', line, col, m.start()))
            line += 1
            line_start = m.end()
            continue
        if kind == 'MISMATCH':
            raise FeelError.syntax_at(
                filename, source, line, col,
                f"unexpected character: {value!r}",
                hint="this character is not part of Feel syntax"
            )
        if kind == 'NUMBER':
            value = float(value) if '.' in value else int(value)
            tokens.append(Token('NUMBER', value, line, col, m.start()))
        elif kind == 'STRING':
            tokens.append(Token('STRING', _unescape(value[1:-1]), line, col, m.start()))
        elif kind == 'KEYWORD':
            tokens.append(Token(value.upper(), value, line, col, m.start()))
        else:
            tokens.append(Token(kind, value, line, col, m.start()))
    return tokens
