import re

TOKENS = [
    ('COMMENT',   r'--[^\n]*'),
    ('NUMBER',    r'\d+(\.\d+)?'),
    ('STRING',    r'"[^"]*"'),
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
    ('SKIP',      r'[ \t]+'),
    ('KEYWORD',   r'\b(let|define|taking|show|record|when|otherwise|repeat|times|for|in|and|or|not|true|false|nothing)\b'),
    ('IDENT',     r'[a-zA-Z_][a-zA-Z0-9_]*'),
]

TOKEN_RE = re.compile('|'.join(f'(?P<{name}>{pattern})' for name, pattern in TOKENS))

class Token:
    def __init__(self, type_, value, line):
        self.type = type_
        self.value = value
        self.line = line
    def __repr__(self):
        return f'Token({self.type}, {self.value!r})'

def tokenize(source):
    tokens = []
    line = 1
    for m in TOKEN_RE.finditer(source):
        kind = m.lastgroup
        value = m.group()
        if kind == 'SKIP' or kind == 'COMMENT':
            continue
        elif kind == 'NEWLINE':
            line += 1
            tokens.append(Token('NEWLINE', '\n', line))
        elif kind == 'NUMBER':
            value = float(value) if '.' in value else int(value)
            tokens.append(Token('NUMBER', value, line))
        elif kind == 'STRING':
            tokens.append(Token('STRING', value[1:-1], line))
        elif kind == 'KEYWORD':
            tokens.append(Token(value.upper(), value, line))
        else:
            tokens.append(Token(kind, value, line))
    return tokens
