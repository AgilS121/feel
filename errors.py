"""Error reporting untuk Feel — pesan dengan source caret + saran Bahasa Indonesia."""

# ANSI color (auto-disabled di terminal tanpa color)
import sys

_USE_COLOR = sys.stderr.isatty()


def _c(code, text):
    if not _USE_COLOR:
        return text
    return f'\033[{code}m{text}\033[0m'


def _red(s):    return _c('31', s)
def _yellow(s): return _c('33', s)
def _cyan(s):   return _c('36', s)
def _dim(s):    return _c('2', s)
def _bold(s):   return _c('1', s)


# Cache sumber per file, supaya error reporting tidak perlu re-read
_SOURCE_CACHE = {}


def register_source(filename, source):
    _SOURCE_CACHE[filename] = source.splitlines()


def _get_line(filename, source, line):
    if source is not None:
        lines = source.splitlines()
    else:
        lines = _SOURCE_CACHE.get(filename)
    if lines is None or line < 1 or line > len(lines):
        return ''
    return lines[line - 1]


def _render(filename, source, line, col, kind, msg, hint=None, length=1):
    src_line = _get_line(filename, source, line)
    header = _red(_bold(f'{kind}Error')) + f' at {_cyan(filename)}:{_yellow(str(line))}:{_yellow(str(col))}'
    parts = [header]
    if src_line:
        gutter = _dim(f'  {line:>4} | ')
        parts.append(f'{gutter}{src_line}')
        caret_indent = ' ' * (len(f'  {line:>4} | ') + max(col - 1, 0))
        caret = _red('^' * max(length, 1))
        parts.append(f'{caret_indent}{caret}')
    parts.append(_red(f'  Message: ') + msg)
    if hint:
        parts.append(_yellow(f'  Hint:    ') + hint)
    return '\n'.join(parts)


class FeelError(Exception):
    """Error utama Feel — selalu carry posisi sumber."""

    def __init__(self, message, *, filename='<input>', line=1, col=1, hint=None, kind='Syntax', length=1, source=None):
        self.filename = filename
        self.line = line
        self.col = col
        self.hint = hint
        self.kind = kind
        self.length = length
        self.raw_message = message
        self.source = source
        super().__init__(_render(filename, source, line, col, kind, message, hint, length))

    @classmethod
    def syntax(cls, token, message, hint=None, filename='<input>', source=None):
        """Bikin error syntax dari Token."""
        line = getattr(token, 'line', 1)
        col = getattr(token, 'col', 1)
        length = len(str(getattr(token, 'value', '?'))) if token is not None else 1
        return cls(message, filename=filename, line=line, col=col,
                   hint=hint, kind='Syntax', length=length, source=source)

    @classmethod
    def syntax_at(cls, filename, source, line, col, message, hint=None, length=1):
        return cls(message, filename=filename, line=line, col=col,
                   hint=hint, kind='Syntax', length=length, source=source)

    @classmethod
    def runtime(cls, node, message, hint=None, filename='<input>', source=None):
        """Build runtime error from AST node (which already carries line/col)."""
        line = getattr(node, 'line', 1) or 1
        col = getattr(node, 'col', 1) or 1
        return cls(message, filename=filename, line=line, col=col,
                   hint=hint, kind='Runtime', length=1, source=source)

    @classmethod
    def name_error(cls, node, name, filename='<input>', source=None, similar=None):
        hint = "check spelling of variable or function name"
        if similar:
            hint = f"did you mean {_bold(similar)}?"
        elif name:
            hint = f"check spelling, or define it first with 'let {name} = ...' or 'define {name} ...'"
        return cls.runtime(node, f"'{name}' is not defined", hint=hint, filename=filename, source=source)

    @classmethod
    def type_error(cls, node, message, hint=None, filename='<input>', source=None):
        return cls(message, filename=filename,
                   line=getattr(node, 'line', 1) or 1,
                   col=getattr(node, 'col', 1) or 1,
                   hint=hint, kind='Type', length=1, source=source)


class FeelThrow(Exception):
    """Internal: signal user-level throw, ditangkap oleh try/catch."""
    def __init__(self, value, node=None):
        self.value = value
        self.node = node
        super().__init__(str(value))
