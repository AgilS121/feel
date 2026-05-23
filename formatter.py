"""feelfmt — canonical formatter for Feel source.

Strict, no options. The point is that AI tools (and humans) get a single
predictable shape for every program. This is part of the AI-predictability
pillar.

Algorithm:
  1. Parse source to AST (drops comments).
  2. Scan original source for comment lines + blank lines (line-level).
  3. Walk AST and emit canonical form, line by line.
  4. Re-interleave preserved comments/blanks at their original line positions.

Limitations (v1):
  - Comments inside multi-line expressions may be relocated.
  - End-of-line inline comments after code on the same line are dropped.
    (Use a comment-only line above instead — that's the canonical form.)
"""

from parser import (
    parse, Program, LetStmt, DefineStmt, RecordDef, ShowStmt, WhenStmt,
    RepeatStmt, ForStmt, Pipeline, BinOp, UnaryOp, Call, CallExpr,
    FieldAccess, IndexAccess, RecordLiteral, MapLiteral, ListLiteral,
    Ident, Literal, ArrowExpr, TryStmt, ThrowStmt, CatchStep, ImportStmt,
    AssertStmt, Block, Lambda, RouteDecl, RespondExpr, ServeStmt,
    ToolDecl, AgentDecl,
)


INDENT = '  '   # two spaces per level


class Formatter:
    def __init__(self):
        self.depth = 0

    def _ind(self):
        return INDENT * self.depth

    def format_program(self, prog):
        """Returns list of (start_line, formatted_text) for each statement."""
        out = []
        for stmt in prog.stmts:
            if stmt is None:
                continue
            start = getattr(stmt, 'line', 0) or 0
            text = self.format_stmt(stmt)
            out.append((start, text))
        return out

    # ---------- Statements ----------

    def format_stmt(self, n):
        if isinstance(n, LetStmt):
            return f'let {n.name} = {self.expr(n.value)}'
        if isinstance(n, DefineStmt):
            params = f' taking {", ".join(n.params)}' if n.params else ''
            return f'define {n.name}{params} -> {self.expr(n.body)}'
        if isinstance(n, RecordDef):
            fields = ', '.join(f'{k}: {v}' for k, v in n.fields.items())
            return f'record {n.name} {{ {fields} }}'
        if isinstance(n, ShowStmt):
            return f'show -> {self.expr(n.expr)}'
        if isinstance(n, WhenStmt):
            return self._format_when(n)
        if isinstance(n, RepeatStmt):
            return f'repeat {self.expr(n.count)} times -> {self.expr(n.body)}'
        if isinstance(n, ForStmt):
            return f'for {n.var} in {self.expr(n.iterable)} -> {self.expr(n.body)}'
        if isinstance(n, TryStmt):
            return f'try {self.expr(n.body)} catch {n.err_name} -> {self.expr(n.handler)}'
        if isinstance(n, ThrowStmt):
            return f'throw {self.expr(n.expr)}'
        if isinstance(n, ImportStmt):
            if n.expose:
                return f'import {n.name} expose {", ".join(n.expose)}'
            return f'import {n.name}'
        if isinstance(n, AssertStmt):
            msg = f', {self.expr(n.message)}' if n.message else ''
            return f'assert {self.expr(n.cond)}{msg}'
        if isinstance(n, RouteDecl):
            return f'route {n.method} {self._string_lit(n.path)} -> {self.expr(n.handler)}'
        if isinstance(n, ServeStmt):
            parts = [f'serve on {n.port}']
            if n.cors:
                parts.append('cors')
            if n.cert_file and n.key_file:
                parts.append(f'tls {self._string_lit(n.cert_file)} {self._string_lit(n.key_file)}')
            return ' '.join(parts)
        if isinstance(n, ToolDecl):
            params = f' taking {", ".join(n.params)}' if n.params else ''
            return f'tool {n.name} {self._string_lit(n.description)}{params} -> {self.expr(n.body)}'
        if isinstance(n, AgentDecl):
            return self._format_agent(n)
        # Expression-as-statement
        return self.expr(n)

    def _format_when(self, n):
        head = f'when {self.expr(n.cond)} -> {self.expr(n.then)}'
        if n.otherwise is not None:
            head += f' otherwise -> {self.expr(n.otherwise)}'
        return head

    def _format_agent(self, n):
        if not n.config:
            return f'agent {n.name} {{}}'
        if len(n.config) == 1:
            k, v = next(iter(n.config.items()))
            return f'agent {n.name} {{ {k}: {self.expr(v)} }}'
        self.depth += 1
        ind = self._ind()
        lines = [f'agent {n.name} {{']
        for k, v in n.config.items():
            v_text = self.expr(v)
            v_lines = v_text.split('\n')
            v_lines[0] = f'{ind}{k}: {v_lines[0]}'
            v_lines[-1] = v_lines[-1] + ','
            lines.extend(v_lines)
        self.depth -= 1
        lines.append(f'{self._ind()}}}')
        return '\n'.join(lines)

    # ---------- Expressions ----------

    def expr(self, n):
        if n is None:
            return 'nothing'
        if isinstance(n, Literal):
            return self._literal(n.value)
        if isinstance(n, Ident):
            return n.name
        if isinstance(n, ArrowExpr):
            return self.expr(n.expr)
        if isinstance(n, BinOp):
            return f'{self.expr(n.left)} {n.op} {self.expr(n.right)}'
        if isinstance(n, UnaryOp):
            if n.op == 'not':
                return f'not {self.expr(n.expr)}'
            return f'-{self.expr(n.expr)}'
        if isinstance(n, Pipeline):
            return ' | '.join(self._pipeline_step(s) for s in n.steps)
        if isinstance(n, Call):
            args = ', '.join(self.expr(a) for a in n.args)
            return f'{n.name}({args})'
        if isinstance(n, CallExpr):
            args = ', '.join(self.expr(a) for a in n.args)
            return f'{self.expr(n.callee)}({args})'
        if isinstance(n, FieldAccess):
            return f'{self.expr(n.obj)}.{n.field}'
        if isinstance(n, IndexAccess):
            return f'{self.expr(n.obj)}[{self.expr(n.index)}]'
        if isinstance(n, RecordLiteral):
            fields = ', '.join(f'{k}: {self.expr(v)}' for k, v in n.fields.items())
            if not fields:
                return f'{n.name} {{}}'
            return f'{n.name} {{ {fields} }}'
        if isinstance(n, MapLiteral):
            if not n.entries:
                return 'map {}'
            entries = ', '.join(
                f'{self._map_key(k)}: {self.expr(v)}' for k, v in n.entries)
            single_line = f'map {{ {entries} }}'
            if len(single_line) <= 80 and '\n' not in single_line:
                return single_line
            # Multi-line: trailing commas (canonical, AI-predictable)
            self.depth += 1
            ind = self._ind()
            lines = ['map {']
            for k, v in n.entries:
                v_text = self.expr(v)
                # If value itself is multi-line, only first line gets this indent
                v_lines = v_text.split('\n')
                v_lines[0] = f'{ind}{self._map_key(k)}: {v_lines[0]}'
                # Append trailing comma to last line of value
                v_lines[-1] = v_lines[-1] + ','
                lines.extend(v_lines)
            self.depth -= 1
            lines.append(f'{self._ind()}}}')
            return '\n'.join(lines)
        if isinstance(n, ListLiteral):
            items = [self.expr(i) for i in n.items]
            single_line = f'[{", ".join(items)}]'
            if len(single_line) <= 80 and '\n' not in single_line:
                return single_line
            self.depth += 1
            ind = self._ind()
            lines = ['[']
            for it in items:
                it_lines = it.split('\n')
                it_lines[0] = ind + it_lines[0]
                it_lines[-1] = it_lines[-1] + ','
                lines.extend(it_lines)
            self.depth -= 1
            lines.append(f'{self._ind()}]')
            return '\n'.join(lines)
        if isinstance(n, Block):
            return self._format_block(n)
        if isinstance(n, Lambda):
            params = ', '.join(n.params) if n.params else ''
            head = f'fn {params}' if params else 'fn'
            return f'{head} -> {self.expr(n.body)}'
        if isinstance(n, ShowStmt):
            return f'show -> {self.expr(n.expr)}'
        if isinstance(n, WhenStmt):
            return self._format_when(n)
        if isinstance(n, TryStmt):
            return f'try {self.expr(n.body)} catch {n.err_name} -> {self.expr(n.handler)}'
        if isinstance(n, ThrowStmt):
            return f'throw {self.expr(n.expr)}'
        if isinstance(n, RespondExpr):
            if n.body is None:
                return f'respond {n.status}'
            if n.status == 200:
                return f'respond {self.expr(n.body)}'
            return f'respond {n.status} {self.expr(n.body)}'
        return f'<?{type(n).__name__}?>'

    def _pipeline_step(self, n):
        if isinstance(n, CatchStep):
            return f'catch -> {self.expr(n.handler)}'
        return self.expr(n)

    def _format_block(self, n):
        if not n.stmts:
            return 'do {}'
        self.depth += 1
        ind = self._ind()
        lines = ['do {']
        for s in n.stmts:
            sub = self.format_stmt(s)
            # Sub-statement may be multi-line (e.g. nested do); only the FIRST
            # line gets this block's indent — inner lines already have absolute
            # indents from their own format passes.
            sub_lines = sub.split('\n')
            if sub_lines:
                sub_lines[0] = ind + sub_lines[0]
            lines.extend(sub_lines)
        self.depth -= 1
        lines.append(f'{self._ind()}}}')
        return '\n'.join(lines)

    def _map_key(self, key_node):
        # key_node is a Literal carrying the key string
        if isinstance(key_node, Literal):
            k = key_node.value
            if isinstance(k, str) and k.isidentifier():
                return k
            return self._string_lit(k)
        return self.expr(key_node)

    def _literal(self, v):
        if v is None: return 'nothing'
        if v is True: return 'true'
        if v is False: return 'false'
        if isinstance(v, str): return self._string_lit(v)
        if isinstance(v, float):
            if v == int(v): return str(int(v))
            return str(v)
        return str(v)

    def _string_lit(self, s):
        """Re-emit a string literal with escape sequences for special chars.

        Lexer turned `\\{` into \\x00 etc; we restore the visible form here so
        formatted output round-trips back to the same AST."""
        escaped = (
            s.replace('\\', '\\\\')
             .replace('"', '\\"')
             .replace('\n', '\\n')
             .replace('\t', '\\t')
             .replace('\r', '\\r')
             .replace('\x00', '\\{')
             .replace('\x01', '\\}')
        )
        return f'"{escaped}"'


def format_source(source, filename='<input>'):
    """Format a Feel source string. Returns formatted source (idempotent)."""
    tree = parse(source, filename=filename)
    formatter = Formatter()

    # Collect line-level trivia (comment-only lines, blank lines)
    src_lines = source.split('\n')
    # Strip trailing empty final line caused by terminal newline
    if src_lines and src_lines[-1] == '':
        src_lines = src_lines[:-1]

    line_kinds = {}  # 1-based line -> ('comment', text) | ('blank',)
    for i, raw in enumerate(src_lines, start=1):
        stripped = raw.strip()
        if stripped.startswith('--'):
            line_kinds[i] = ('comment', stripped)
        elif stripped == '':
            line_kinds[i] = ('blank',)

    # Format statements with their starting lines
    formatted_stmts = formatter.format_program(tree)
    # Sort by line just in case
    formatted_stmts.sort(key=lambda p: p[0])

    out_lines = []
    cursor = 1  # next source line to consider for trivia injection

    last_was_blank = False
    for stmt_line, stmt_text in formatted_stmts:
        # Emit trivia for lines before this statement's start
        while cursor < stmt_line:
            kind = line_kinds.get(cursor)
            if kind is None:
                cursor += 1
                continue
            if kind[0] == 'comment':
                out_lines.append(kind[1])
                last_was_blank = False
            elif kind[0] == 'blank':
                # Collapse consecutive blanks to one
                if out_lines and not last_was_blank:
                    out_lines.append('')
                    last_was_blank = True
            cursor += 1
        # Emit the statement (may span multiple lines)
        for sub in stmt_text.split('\n'):
            out_lines.append(sub)
        last_was_blank = False
        cursor = stmt_line + 1

    # Emit any trailing comments after last stmt
    while cursor <= len(src_lines):
        kind = line_kinds.get(cursor)
        if kind and kind[0] == 'comment':
            out_lines.append(kind[1])
            last_was_blank = False
        elif kind and kind[0] == 'blank' and not last_was_blank:
            out_lines.append('')
            last_was_blank = True
        cursor += 1

    # Final cleanup: collapse runs of blank lines to max 1, ensure trailing newline
    result = []
    prev_blank = False
    for ln in out_lines:
        if ln == '':
            if prev_blank:
                continue
            prev_blank = True
        else:
            prev_blank = False
        result.append(ln)
    # Strip leading/trailing blank
    while result and result[0] == '':
        result.pop(0)
    while result and result[-1] == '':
        result.pop()
    return '\n'.join(result) + '\n'


def format_file(path, write=False, check=False):
    """Format a file. Returns (ok, output_or_diff)."""
    with open(path, encoding='utf-8') as f:
        src = f.read()
    formatted = format_source(src, filename=path)
    if check:
        return (src == formatted), formatted
    if write:
        with open(path, 'w', encoding='utf-8') as f:
            f.write(formatted)
        return True, formatted
    return True, formatted
