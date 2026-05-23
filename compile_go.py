"""Feel → Go transpiler (M4-A + M4-B).

Emits a single self-contained Go file with the Feel runtime + translated
user program. Build with `go build` to get a native binary.

Scope of M4-A:
  - Literals (number/string with {interpolation}, bool, nothing)
  - let, define+taking, show
  - Binary + unary ops; logical ops
  - Lambda (fn x -> expr) with closures
  - Function call (direct identifier or via field access)
  - when/otherwise (stmt + expr forms)
  - Lists and maps (literals + indexing), FieldAccess on maps
  - Pipeline + for/repeat loops

Scope of M4-B (added in this version):
  - try/catch/throw via Go panic/recover
  - records: RecordDef (declared) + RecordLiteral (emitted as a map)
  - stdlib namespaces: string, list, map, json — accessed as feel_<mod>_mod
  - list.fold/list.map/list.filter accept Feel closures
  - feel_type_of recognizes records (returns the declared name)

Deferred to M4-C:
  - modules/import (cross-file)
  - HTTP runtime (route/respond/serve)
  - AI primitives (ai.*)
  - DB driver (db.*)
  - agent/tool keywords

Strategy: every Feel value is Go `any` (interface{}). Runtime helpers
handle dynamic dispatch.
"""

from parser import (
    parse, Program, LetStmt, DefineStmt, ShowStmt, WhenStmt, RepeatStmt, ForStmt,
    BinOp, UnaryOp, Call, CallExpr, FieldAccess, IndexAccess,
    Literal, Ident, ArrowExpr, Pipeline, Lambda, Block,
    MapLiteral, ListLiteral, RecordDef, RecordLiteral,
    TryStmt, ThrowStmt, CatchStep,
    RouteDecl, RespondExpr, ServeStmt, StaticDecl, ToolDecl, AgentDecl,
    ImportStmt,
)
from errors import FeelError


INDENT = '\t'  # Go convention: tabs


class GoEmitter:
    """Walks Feel AST and emits Go source."""

    def __init__(self, search_paths=None):
        self.depth = 0
        self.var_counter = 0
        self.scopes = [set()]
        self.uses_db = False
        self.uses_ws = False
        self.search_paths = search_paths or []
        self.loaded_modules = {}   # name -> emitted module init lines
        self.module_order = []      # ordered list of module names for emission

    def _ind(self):
        return INDENT * self.depth

    def _fresh(self, prefix='t'):
        self.var_counter += 1
        return f'{prefix}{self.var_counter}'

    def _scope_has(self, name):
        for s in reversed(self.scopes):
            if name in s:
                return True
        return False

    def _scope_add(self, name):
        self.scopes[-1].add(name)

    def _scope_push(self, names=()):
        self.scopes.append(set(names))

    def _scope_pop(self):
        self.scopes.pop()

    def _extract_path_params(self, pattern):
        import re as _re
        return _re.findall(r'\{(\w+)\}', pattern)

    def _emit_route(self, n):
        params = self._extract_path_params(n.path)
        if n.method == 'WS':
            self.uses_ws = True
        # Handler scope: request, body, query (magic vars) + path params + ws for WS routes
        scope_names = ['request', 'body', 'query'] + params
        if n.method == 'WS':
            scope_names = scope_names + ['ws']
        self._scope_push(scope_names)
        handler_expr = self._emit_expr(n.handler)
        self._scope_pop()
        bindings = ['request := scope["request"]; _ = request',
                    'body := scope["body"]; _ = body',
                    'query := scope["query"]; _ = query']
        for p in params:
            bindings.append(f'{_safe_name(p)} := scope["{_escape(p)}"]; _ = {_safe_name(p)}')
        if n.method == 'WS':
            bindings.append('ws := scope["ws"]; _ = ws')
        ind = self._ind()
        joined = ('\n' + ind + '\t\t').join(bindings)
        return [
            f'{ind}feel_register_route("{n.method}", "{_escape(n.path)}", func(scope map[string]any) any {{',
            f'{ind}\t\t{joined}',
            f'{ind}\t\treturn {handler_expr}',
            f'{ind}}})',
        ]

    def _emit_tool(self, n):
        self._scope_add(n.name)
        self._scope_push(n.params)
        body_expr = self._emit_expr(n.body)
        self._scope_pop()
        params_go = ', '.join(f'{_safe_name(p)} any' for p in n.params)
        params_list = ', '.join(f'"{_escape(p)}"' for p in n.params)
        ind = self._ind()
        return [
            f'{ind}{_safe_name(n.name)} := feel_make_tool("{_escape(n.name)}", "{_escape(n.description)}", []string{{{params_list}}}, any(func({params_go}) any {{ return {body_expr} }}))',
            f'{ind}_ = {_safe_name(n.name)}',
        ]

    def _emit_agent(self, n):
        self._scope_add(n.name)
        ind = self._ind()
        config = n.config or {}
        system = self._emit_expr(config['system']) if 'system' in config else 'any("")'
        tools = self._emit_expr(config['tools']) if 'tools' in config else 'any([]any{})'
        model = self._emit_expr(config['model']) if 'model' in config else 'any(nil)'
        return [
            f'{ind}{_safe_name(n.name)} := feel_make_agent("{_escape(n.name)}", {system}, {tools}, {model})',
            f'{ind}_ = {_safe_name(n.name)}',
        ]

    def emit_program(self, prog):
        body = []
        for stmt in prog.stmts:
            if stmt is None:
                continue
            body.extend(self._emit_stmt(stmt))
        # Modules are emitted via _emit_stmt(ImportStmt) — they show up inline
        # at first import site. That's fine because Go is single-pass for vars
        # in the same function scope.
        return '\n'.join(body)

    def _resolve_module(self, name):
        """Find ./<name>.feel under search_paths. Returns absolute path or None."""
        import os
        for base in self.search_paths:
            candidate = os.path.join(base, f'{name}.feel')
            if os.path.isfile(candidate):
                return os.path.abspath(candidate)
        return None

    def _emit_module_loader(self, mod_name):
        """Emit a Go closure that runs the module body and returns its top-level
        bindings as a map[string]any. Returned as a list of Go lines suitable
        for emission at depth `self.depth`.
        """
        if mod_name in self.loaded_modules:
            return []  # already emitted somewhere upstream

        path = self._resolve_module(mod_name)
        if path is None:
            raise FeelError.runtime(
                Literal(None),
                f"module '{mod_name}' not found",
                hint=f"searched in: {', '.join(self.search_paths) or '(no paths)'}",
            )

        with open(path, encoding='utf-8') as f:
            src = f.read()
        mod_tree = parse(src, filename=path)

        # Collect top-level let / define names so we can build the returned map.
        exported = []
        for st in mod_tree.stmts:
            if isinstance(st, LetStmt):
                exported.append(st.name)
            elif isinstance(st, DefineStmt):
                exported.append(st.name)
            elif isinstance(st, RecordDef):
                pass  # record schemas are global

        # Emit module body as an IIFE that returns the map.
        ind = self._ind()
        lines = [f'{ind}// --- imported module: {mod_name} ({path}) ---']
        lines.append(f'{ind}{_safe_name(mod_name)} := func() map[string]any {{')

        sub_emitter = GoEmitter(search_paths=self.search_paths)
        sub_emitter.depth = self.depth + 1
        sub_emitter.uses_db = self.uses_db
        # Inherit already-loaded modules so nested imports dedupe.
        sub_emitter.loaded_modules = self.loaded_modules
        for st in mod_tree.stmts:
            if st is None:
                continue
            lines.extend(sub_emitter._emit_stmt(st))
        # Anything the sub-emitter learned propagates up
        self.uses_db = self.uses_db or sub_emitter.uses_db

        # Return map of exported names
        inner_ind = INDENT * (self.depth + 1)
        lines.append(f'{inner_ind}return map[string]any{{')
        for name in exported:
            lines.append(f'{inner_ind}{INDENT}"{name}": any({_safe_name(name)}),')
        lines.append(f'{inner_ind}}}')
        lines.append(f'{ind}}}()')
        lines.append(f'{ind}_ = {_safe_name(mod_name)}')

        self.loaded_modules[mod_name] = True
        self.module_order.append(mod_name)
        return lines

    # ---------- Statements ----------

    def _emit_stmt(self, n):
        """Returns list of Go lines (indented from depth=0; main() adds outer)."""
        if isinstance(n, RecordDef):
            # Register the schema at runtime so validate.shape works in compiled mode.
            ind = self._ind()
            lines = [f'{ind}// record {n.name} {{ {", ".join(f"{k}: {v}" for k, v in n.fields.items())} }}']
            entries = ', '.join(f'"{k}": "{v}"' for k, v in n.fields.items())
            lines.append(f'{ind}feel_record_schemas["{n.name}"] = map[string]string{{{entries}}}')
            return lines
        if isinstance(n, ThrowStmt):
            return [f'{self._ind()}panic(feel_throw{{value: {self._emit_expr(n.expr)}}})']
        if isinstance(n, TryStmt):
            # Statement-level try: discard result
            return [f'{self._ind()}_ = {self._emit_expr(n)}']
        if isinstance(n, RouteDecl):
            return self._emit_route(n)
        if isinstance(n, ServeStmt):
            cors = 'true' if n.cors else 'false'
            cert = _go_string_lit(n.cert_file) if n.cert_file else '""'
            key = _go_string_lit(n.key_file) if n.key_file else '""'
            return [f'{self._ind()}feel_serve_http({n.port}, {cors}, {cert}, {key})']
        if isinstance(n, StaticDecl):
            prefix = _go_string_lit(n.url_prefix)
            fs_dir = _go_string_lit(n.fs_dir)
            return [f'{self._ind()}feel_mount_static({prefix}, {fs_dir})']
        if isinstance(n, ToolDecl):
            return self._emit_tool(n)
        if isinstance(n, AgentDecl):
            return self._emit_agent(n)
        if isinstance(n, ImportStmt):
            ind = self._ind()
            lines = self._emit_module_loader(n.name)
            if n.expose:
                for nm in n.expose:
                    self._scope_add(nm)
                    lines.append(
                        f'{ind}{_safe_name(nm)} := feel_field({_safe_name(n.name)}, "{nm}")'
                    )
                    lines.append(f'{ind}_ = {_safe_name(nm)}')
            else:
                self._scope_add(n.name)
            return lines
        if isinstance(n, LetStmt):
            value = self._emit_expr(n.value)
            self._scope_add(n.name)
            return [f'{self._ind()}{_safe_name(n.name)} := any({value})',
                    f'{self._ind()}_ = {_safe_name(n.name)}  // silence unused']
        if isinstance(n, DefineStmt):
            # Top-level Feel `define` → assign a Go closure to a variable
            params = ', '.join(f'{_safe_name(p)} any' for p in n.params)
            # Function name visible inside its own body for recursion
            self._scope_add(n.name)
            self._scope_push(n.params)
            body_expr = self._emit_expr(n.body)
            self._scope_pop()
            return [
                f'{self._ind()}{_safe_name(n.name)} := func({params}) any {{ return {body_expr} }}',
                f'{self._ind()}_ = {_safe_name(n.name)}',
            ]
        if isinstance(n, ShowStmt):
            return [f'{self._ind()}feel_show({self._emit_expr(n.expr)})']
        if isinstance(n, WhenStmt):
            # Statement form: emit if/else
            cond = self._emit_expr(n.cond)
            lines = [f'{self._ind()}if feel_truthy({cond}) {{']
            self.depth += 1
            lines.extend(self._emit_stmt(_as_stmt(n.then)))
            self.depth -= 1
            if n.otherwise is not None:
                lines.append(f'{self._ind()}}} else {{')
                self.depth += 1
                lines.extend(self._emit_stmt(_as_stmt(n.otherwise)))
                self.depth -= 1
            lines.append(f'{self._ind()}}}')
            return lines
        if isinstance(n, RepeatStmt):
            count = self._emit_expr(n.count)
            lines = [f'{self._ind()}for __i := 0; __i < int(feel_num({count})); __i++ {{']
            self.depth += 1
            lines.extend(self._emit_stmt(_as_stmt(n.body)))
            self.depth -= 1
            lines.append(f'{self._ind()}}}')
            return lines
        if isinstance(n, ForStmt):
            iterable = self._emit_expr(n.iterable)
            lines = [f'{self._ind()}for _, {_safe_name(n.var)} := range feel_iter({iterable}) {{']
            self.depth += 1
            lines.append(f'{self._ind()}_ = {_safe_name(n.var)}')
            lines.extend(self._emit_stmt(_as_stmt(n.body)))
            self.depth -= 1
            lines.append(f'{self._ind()}}}')
            return lines
        # Expression as statement
        return [f'{self._ind()}_ = {self._emit_expr(n)}']

    # ---------- Expressions ----------

    def _emit_expr(self, n):
        if n is None:
            return 'any(nil)'
        if isinstance(n, Literal):
            return self._literal(n.value)
        if isinstance(n, Ident):
            # If shadowed by a local binding, emit the variable.
            if self._scope_has(n.name):
                return _safe_name(n.name)
            # Stdlib namespace modules → emit the Go map.
            if n.name in STDLIB_MODULES:
                # queue is SQLite-backed → also pulls in the sqlite driver
                if n.name in ('db', 'queue'):
                    self.uses_db = True
                return f'feel_{n.name}_mod'
            # Builtin used as a value (e.g. in a pipeline) → emit the wrapper.
            if n.name == 'show':
                return 'feel_show_fn'
            if _is_builtin(n.name):
                return f'feel_{n.name}_fn'
            # Fallback: assume a Go-level identifier exists.
            return _safe_name(n.name)
        if isinstance(n, ArrowExpr):
            return self._emit_expr(n.expr)
        if isinstance(n, BinOp):
            return self._binop(n)
        if isinstance(n, UnaryOp):
            if n.op == 'not':
                return f'feel_not({self._emit_expr(n.expr)})'
            return f'feel_neg({self._emit_expr(n.expr)})'
        if isinstance(n, Call):
            args = ', '.join(self._emit_expr(a) for a in n.args)
            # Builtin name? Map to feel_<name>
            if _is_builtin(n.name):
                return f'feel_{n.name}({args})'
            # User function: route through feel_call so it works whether the
            # variable is typed func(any...) any or `any` (e.g. lambda value).
            return f'feel_call({_safe_name(n.name)}, []any{{{args}}})'
        if isinstance(n, CallExpr):
            callee = self._emit_expr(n.callee)
            args = ', '.join(self._emit_expr(a) for a in n.args)
            return f'feel_call({callee}, []any{{{args}}})'
        if isinstance(n, Lambda):
            params = ', '.join(f'{_safe_name(p)} any' for p in n.params)
            self._scope_push(n.params)
            body = self._emit_expr(n.body)
            self._scope_pop()
            return f'any(func({params}) any {{ return {body} }})'

        if isinstance(n, ThrowStmt):
            return f'func() any {{ panic(feel_throw{{value: {self._emit_expr(n.expr)}}}); return any(nil) }}()'

        if isinstance(n, TryStmt):
            # try BODY catch ERR -> HANDLER  →  IIFE with defer/recover.
            # Recovers feel_throw panics; non-throw panics propagate up.
            body = self._emit_expr(n.body)
            self._scope_push([n.err_name])
            handler = self._emit_expr(n.handler)
            self._scope_pop()
            err = _safe_name(n.err_name)
            return (
                'func() (result any) { '
                'defer func() { '
                'if r := recover(); r != nil { '
                f'ft, ok := r.(feel_throw); '
                f'if ok {{ {err} := ft.value; _ = {err}; result = {handler}; return }}; '
                'panic(r) '
                '} }(); '
                f'result = {body}; return '
                '}()'
            )

        if isinstance(n, RecordLiteral):
            entries = []
            entries.append(f'"__type__": any("{_escape(n.name)}")')
            for k, v in n.fields.items():
                entries.append(f'"{_escape(k)}": {self._emit_expr(v)}')
            return 'any(map[string]any{' + ', '.join(entries) + '})'

        if isinstance(n, RespondExpr):
            if n.body is None:
                return f'any(feel_response{{status: {n.status}}})'
            return f'any(feel_response{{status: {n.status}, body: {self._emit_expr(n.body)}}})'
        if isinstance(n, WhenStmt):
            # Expression form: ternary via IIFE
            cond = self._emit_expr(n.cond)
            then_e = self._emit_expr(n.then)
            else_e = self._emit_expr(n.otherwise) if n.otherwise else 'any(nil)'
            return f'func() any {{ if feel_truthy({cond}) {{ return {then_e} }}; return {else_e} }}()'
        if isinstance(n, Block):
            # Block expression: IIFE with proper statement separators.
            self._scope_push()
            lines = []
            stmts = n.stmts
            for s in stmts[:-1]:
                lines.append('; '.join(self._inline_stmt(s)))
            last = stmts[-1] if stmts else None
            stmt_types = (LetStmt, DefineStmt, ShowStmt, RepeatStmt, ForStmt,
                          RouteDecl, ServeStmt, StaticDecl, ToolDecl, AgentDecl)
            if last is None:
                last_expr = 'any(nil)'
            elif isinstance(last, stmt_types):
                lines.append('; '.join(self._inline_stmt(last)))
                last_expr = 'any(nil)'
            else:
                last_expr = self._emit_expr(last)
            self._scope_pop()
            body = '; '.join(lines)
            if body:
                body += '; '
            body += f'return {last_expr}'
            return f'func() any {{ {body} }}()'
        if isinstance(n, ListLiteral):
            items = ', '.join(self._emit_expr(i) for i in n.items)
            return f'any([]any{{{items}}})'
        if isinstance(n, MapLiteral):
            entries = ', '.join(
                f'"{_escape(self._eval_key(k))}": {self._emit_expr(v)}'
                for k, v in n.entries
            )
            return f'any(map[string]any{{{entries}}})'
        if isinstance(n, IndexAccess):
            obj = self._emit_expr(n.obj)
            idx = self._emit_expr(n.index)
            return f'feel_index({obj}, {idx})'
        if isinstance(n, FieldAccess):
            obj = self._emit_expr(n.obj)
            return f'feel_field({obj}, "{_escape(n.field)}")'
        if isinstance(n, Pipeline):
            # Translate steps as nested function applications
            expr = self._emit_expr(n.steps[0])
            for step in n.steps[1:]:
                fn = self._emit_expr(step)
                expr = f'feel_call({fn}, []any{{{expr}}})'
            return expr
        if isinstance(n, ShowStmt):
            # show as expr: print and return nil
            return f'func() any {{ feel_show({self._emit_expr(n.expr)}); return any(nil) }}()'
        raise FeelError.runtime(n, f'M4-A: unsupported node {type(n).__name__}',
                                hint='this construct is not yet supported by the Go transpiler')

    def _inline_stmt(self, n):
        """Like _emit_stmt but produces inline-able statement strings (no `\n`)."""
        if isinstance(n, LetStmt):
            value = self._emit_expr(n.value)
            self._scope_add(n.name)
            return [f'{_safe_name(n.name)} := any({value})', f'_ = {_safe_name(n.name)}']
        if isinstance(n, DefineStmt):
            self._scope_add(n.name)
            self._scope_push(n.params)
            body = self._emit_expr(n.body)
            self._scope_pop()
            params = ', '.join(f'{_safe_name(p)} any' for p in n.params)
            return [f'{_safe_name(n.name)} := func({params}) any {{ return {body} }}', f'_ = {_safe_name(n.name)}']
        if isinstance(n, ShowStmt):
            return [f'feel_show({self._emit_expr(n.expr)})']
        if isinstance(n, RepeatStmt):
            count = self._emit_expr(n.count)
            body_stmts = '; '.join(self._inline_stmt(_as_stmt(n.body)))
            return [f'for __i := 0; __i < int(feel_num({count})); __i++ {{ {body_stmts} }}']
        if isinstance(n, ForStmt):
            iterable = self._emit_expr(n.iterable)
            var = _safe_name(n.var)
            self._scope_push([n.var])
            body_stmts = '; '.join(self._inline_stmt(_as_stmt(n.body)))
            self._scope_pop()
            return [f'for _, {var} := range feel_iter({iterable}) {{ _ = {var}; {body_stmts} }}']
        return [f'_ = {self._emit_expr(n)}']

    def _eval_key(self, key_node):
        if isinstance(key_node, Literal):
            return str(key_node.value)
        return str(getattr(key_node, 'name', key_node))

    def _binop(self, n):
        op = n.op
        l = self._emit_expr(n.left)
        r = self._emit_expr(n.right)
        if op == '+': return f'feel_add({l}, {r})'
        if op == '-': return f'feel_sub({l}, {r})'
        if op == '*': return f'feel_mul({l}, {r})'
        if op == '/': return f'feel_div({l}, {r})'
        if op == '==': return f'feel_eq({l}, {r})'
        if op == '!=': return f'feel_ne({l}, {r})'
        if op == '<':  return f'feel_lt({l}, {r})'
        if op == '>':  return f'feel_gt({l}, {r})'
        if op == '<=': return f'feel_le({l}, {r})'
        if op == '>=': return f'feel_ge({l}, {r})'
        if op == 'and': return f'feel_and({l}, {r})'
        if op == 'or':  return f'feel_or({l}, {r})'
        return f'/* unknown op {op} */ any(nil)'

    def _literal(self, v):
        if v is None: return 'any(nil)'
        if v is True: return 'any(true)'
        if v is False: return 'any(false)'
        if isinstance(v, bool):  # belt + suspenders: bool subclasses int in Python
            return 'any(true)' if v else 'any(false)'
        if isinstance(v, int):
            return f'any(int64({v}))'
        if isinstance(v, float):
            return f'any(float64({v}))'
        if isinstance(v, str):
            return self._string_literal(v)
        return f'any({v!r})'

    def _string_literal(self, s):
        """Emit a Go expression that builds the string, handling {name} interpolation.

        Lexer encoded `\\{` and `\\}` as \\x00 / \\x01 so the {...} interpolation
        regex skips them. We restore literal braces before emitting Go.
        """
        import re as _re

        def _restore_braces(t):
            return t.replace('\x00', '{').replace('\x01', '}')

        parts = []
        last = 0
        has_interp = False
        for m in _re.finditer(r'\{([^}]+)\}', s):
            has_interp = True
            literal = s[last:m.start()]
            if literal:
                parts.append(('lit', _restore_braces(literal)))
            parts.append(('expr', m.group(1).strip()))
            last = m.end()
        if last < len(s):
            parts.append(('lit', _restore_braces(s[last:])))
        if not has_interp:
            return f'any({_go_string_lit(_restore_braces(s))})'
        joined = []
        for kind, val in parts:
            if kind == 'lit':
                joined.append(_go_string_lit(val))
            else:
                sub_tree = parse(val)
                if not sub_tree.stmts:
                    continue
                joined.append(f'feel_str({self._emit_expr(sub_tree.stmts[0])})')
        return f'any({" + ".join(joined)})'


# ---------- Helpers ----------

GO_RESERVED = {
    'break', 'case', 'chan', 'const', 'continue', 'default', 'defer',
    'else', 'fallthrough', 'for', 'func', 'go', 'goto', 'if', 'import',
    'interface', 'map', 'package', 'range', 'return', 'select', 'struct',
    'switch', 'type', 'var',
}

BUILTINS_GO = {
    'uppercase', 'lowercase', 'length', 'reverse', 'type_of', 'number',
    'int', 'float', 'is_int', 'is_float',
    'text', 'round', 'floor', 'abs', 'sum', 'max', 'min', 'first', 'last',
    'rest', 'push', 'join', 'split', 'contains',
}

STDLIB_MODULES = {'string', 'list', 'map', 'json', 'ai', 'db', 'security', 'crypto',
                  'auth', 'session', 'cache', 'time', 'math', 'file', 'mail',
                  'validate', 'queue', 'http', 'env'}


def _is_builtin(name):
    return name in BUILTINS_GO


def _safe_name(name):
    """Avoid Go reserved words and ensure valid identifier."""
    if name in GO_RESERVED:
        return f'_feel_{name}'
    return name


def _go_string_lit(s):
    """Return a Go string literal for s (use raw form if possible, else escape)."""
    if '`' not in s and '\n' not in s:
        return f'`{s}`'
    escaped = s.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n').replace('\t', '\\t')
    return f'"{escaped}"'


def _escape(s):
    return str(s).replace('\\', '\\\\').replace('"', '\\"')


def _as_stmt(node):
    """Wrap an expression as a statement for stmt-emit paths."""
    return node


# ---------- Runtime Go code ----------

RUNTIME_GO = r'''package main

import (
	"bytes"
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"hash"
	"io"
	"math"
	"mime"
	"mime/multipart"
	"net/http"
	"net/smtp"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"sync"
	"time"
)

// ---------- Runtime types ----------

type feel_throw struct{ value any }

type feel_route struct {
	method  string
	pattern string
	regex   *regexp.Regexp
	params  []string
	handler func(map[string]any) any
}

var feel_routes []feel_route
var feel_cors_enabled bool

type feel_response struct {
	status      int
	body        any
	contentType string
	headers     map[string]string
}

// ---------- Runtime helpers ----------

func feel_show(v any) {
	fmt.Println(feel_str(v))
}

func feel_str(v any) string {
	switch x := v.(type) {
	case nil:
		return "nothing"
	case bool:
		if x {
			return "true"
		}
		return "false"
	case int64:
		return fmt.Sprintf("%d", x)
	case int:
		return fmt.Sprintf("%d", x)
	case float64:
		if x == float64(int64(x)) && x > -1e15 && x < 1e15 {
			return fmt.Sprintf("%d", int64(x))
		}
		return fmt.Sprintf("%g", x)
	case string:
		return x
	case []any:
		parts := make([]string, len(x))
		for i, it := range x {
			parts[i] = feel_str(it)
		}
		return "[" + strings.Join(parts, ", ") + "]"
	case map[string]any:
		parts := make([]string, 0, len(x))
		for k, vv := range x {
			parts = append(parts, k+": "+feel_str(vv))
		}
		return "map { " + strings.Join(parts, ", ") + " }"
	default:
		return fmt.Sprintf("%v", v)
	}
}

func feel_num(v any) float64 {
	switch x := v.(type) {
	case float64:
		return x
	case int:
		return float64(x)
	case int64:
		return float64(x)
	case string:
		var f float64
		_, _ = fmt.Sscanf(x, "%g", &f)
		return f
	case bool:
		if x {
			return 1
		}
		return 0
	}
	return 0
}

func feel_truthy(v any) bool {
	switch x := v.(type) {
	case nil:
		return false
	case bool:
		return x
	case int64:
		return x != 0
	case int:
		return x != 0
	case float64:
		return x != 0
	case string:
		return x != ""
	case []any:
		return len(x) > 0
	case map[string]any:
		return len(x) > 0
	}
	return true
}

// internal helper — returns Go bool. Feel-visible builtin is feel_is_int_b below.
func feel_isInt(v any) bool {
	switch v.(type) {
	case int64, int:
		return true
	}
	return false
}

func feel_int64(v any) int64 {
	switch x := v.(type) {
	case int64:
		return x
	case int:
		return int64(x)
	case float64:
		return int64(x)
	case bool:
		if x {
			return 1
		}
		return 0
	case string:
		var i int64
		_, _ = fmt.Sscanf(x, "%d", &i)
		return i
	}
	return 0
}

func feel_add(a, b any) any {
	if sa, ok := a.(string); ok {
		return sa + feel_str(b)
	}
	if sb, ok := b.(string); ok {
		return feel_str(a) + sb
	}
	if la, ok := a.([]any); ok {
		if lb, ok := b.([]any); ok {
			out := make([]any, 0, len(la)+len(lb))
			out = append(out, la...)
			out = append(out, lb...)
			return any(out)
		}
	}
	if feel_isInt(a) && feel_isInt(b) {
		return any(feel_int64(a) + feel_int64(b))
	}
	return any(feel_num(a) + feel_num(b))
}

func feel_sub(a, b any) any {
	if feel_isInt(a) && feel_isInt(b) {
		return any(feel_int64(a) - feel_int64(b))
	}
	return any(feel_num(a) - feel_num(b))
}

func feel_mul(a, b any) any {
	if feel_isInt(a) && feel_isInt(b) {
		return any(feel_int64(a) * feel_int64(b))
	}
	return any(feel_num(a) * feel_num(b))
}

func feel_div(a, b any) any {
	bv := feel_num(b)
	if bv == 0 {
		panic("division by zero")
	}
	// Division always returns float to avoid integer truncation surprises.
	return any(feel_num(a) / bv)
}

func feel_eq(a, b any) any {
	if a == nil || b == nil {
		return any(a == b)
	}
	// Numeric cross-type equality: int64(5) == float64(5.0) == true.
	an, aok := a.(int64)
	bn, bok := b.(int64)
	if aok && bok {
		return any(an == bn)
	}
	af, afok := a.(float64)
	bf, bfok := b.(float64)
	if afok && bfok {
		return any(af == bf)
	}
	if aok && bfok {
		return any(float64(an) == bf)
	}
	if afok && bok {
		return any(af == float64(bn))
	}
	// Fall through: compare via stringified form (handles strings, bools, equal struct shapes).
	return any(feel_str(a) == feel_str(b) && fmt.Sprintf("%T", a) == fmt.Sprintf("%T", b))
}
func feel_ne(a, b any) any { return any(feel_eq(a, b) == any(false)) }
func feel_lt(a, b any) any { return any(feel_num(a) < feel_num(b)) }
func feel_gt(a, b any) any { return any(feel_num(a) > feel_num(b)) }
func feel_le(a, b any) any { return any(feel_num(a) <= feel_num(b)) }
func feel_ge(a, b any) any { return any(feel_num(a) >= feel_num(b)) }

func feel_and(a, b any) any {
	if !feel_truthy(a) {
		return a
	}
	return b
}
func feel_or(a, b any) any {
	if feel_truthy(a) {
		return a
	}
	return b
}
func feel_not(v any) any { return any(!feel_truthy(v)) }
func feel_neg(v any) any { return any(-feel_num(v)) }

func feel_iter(v any) []any {
	switch x := v.(type) {
	case []any:
		return x
	case string:
		out := make([]any, 0, len(x))
		for _, r := range x {
			out = append(out, string(r))
		}
		return out
	case map[string]any:
		out := make([]any, 0, len(x))
		for k := range x {
			out = append(out, k)
		}
		return out
	}
	return nil
}

func feel_index(obj, idx any) any {
	switch x := obj.(type) {
	case []any:
		i := int(feel_num(idx))
		if i < 0 || i >= len(x) {
			return any(nil)
		}
		return x[i]
	case map[string]any:
		return x[feel_str(idx)]
	case string:
		i := int(feel_num(idx))
		if i < 0 || i >= len(x) {
			return any("")
		}
		return any(string(x[i]))
	}
	return any(nil)
}

func feel_field(obj any, name string) any {
	if m, ok := obj.(map[string]any); ok {
		return m[name]
	}
	return any(nil)
}

func feel_call(fn any, args []any) any {
	// Tools and agents are stored as map[string]any with __fn__ holding the
	// real callable — unwrap and recurse.
	if m, ok := fn.(map[string]any); ok {
		if inner, ok := m["__fn__"]; ok {
			return feel_call(inner, args)
		}
	}
	// Reflection-light: support specific arities + variadic
	switch f := fn.(type) {
	case func() any:
		return f()
	case func(any) any:
		if len(args) >= 1 {
			return f(args[0])
		}
		return f(nil)
	case func(any, any) any:
		if len(args) >= 2 {
			return f(args[0], args[1])
		}
		return any(nil)
	case func(any, any, any) any:
		if len(args) >= 3 {
			return f(args[0], args[1], args[2])
		}
		return any(nil)
	case func(any, any, any, any) any:
		if len(args) >= 4 {
			return f(args[0], args[1], args[2], args[3])
		}
		return any(nil)
	case func(...any) any:
		return f(args...)
	}
	panic(fmt.Sprintf("feel_call: not callable: %T", fn))
}

// ---------- Builtins ----------

func feel_uppercase(v any) any { return any(strings.ToUpper(feel_str(v))) }
func feel_lowercase(v any) any { return any(strings.ToLower(feel_str(v))) }
func feel_length(v any) any {
	switch x := v.(type) {
	case string:
		return any(float64(len(x)))
	case []any:
		return any(float64(len(x)))
	case map[string]any:
		return any(float64(len(x)))
	}
	return any(float64(0))
}
func feel_reverse(v any) any {
	if s, ok := v.(string); ok {
		runes := []rune(s)
		for i, j := 0, len(runes)-1; i < j; i, j = i+1, j-1 {
			runes[i], runes[j] = runes[j], runes[i]
		}
		return any(string(runes))
	}
	if xs, ok := v.([]any); ok {
		out := make([]any, len(xs))
		for i, x := range xs {
			out[len(xs)-1-i] = x
		}
		return any(out)
	}
	return v
}
func feel_type_of(v any) any {
	switch x := v.(type) {
	case nil:
		return any("nothing")
	case bool:
		return any("boolean")
	case float64, int, int64:
		return any("number")
	case string:
		return any("text")
	case []any:
		return any("list")
	case map[string]any:
		if t, ok := x["__type__"]; ok {
			if s, ok := t.(string); ok {
				return any(s)
			}
		}
		return any("map")
	}
	return any("unknown")
}
func feel_number(v any) any { return any(feel_num(v)) }
func feel_int(v any) any    { return any(feel_int64(v)) }
func feel_float(v any) any  { return any(feel_num(v)) }
func feel_is_int(v any) any {
	_, ok1 := v.(int64)
	_, ok2 := v.(int)
	return any(ok1 || ok2)
}
func feel_is_float(v any) any {
	_, ok := v.(float64)
	return any(ok)
}
func feel_text(v any) any   { return any(feel_str(v)) }
func feel_round(v any) any  { return any(float64(int64(feel_num(v) + 0.5))) }
func feel_floor(v any) any  { return any(float64(int64(feel_num(v)))) }
func feel_abs(v any) any {
	x := feel_num(v)
	if x < 0 {
		return any(-x)
	}
	return any(x)
}
func feel_sum(v any) any {
	if xs, ok := v.([]any); ok {
		s := 0.0
		for _, x := range xs {
			s += feel_num(x)
		}
		return any(s)
	}
	return any(float64(0))
}
func feel_max(v any) any {
	if xs, ok := v.([]any); ok && len(xs) > 0 {
		m := feel_num(xs[0])
		for _, x := range xs[1:] {
			if feel_num(x) > m {
				m = feel_num(x)
			}
		}
		return any(m)
	}
	return any(float64(0))
}
func feel_min(v any) any {
	if xs, ok := v.([]any); ok && len(xs) > 0 {
		m := feel_num(xs[0])
		for _, x := range xs[1:] {
			if feel_num(x) < m {
				m = feel_num(x)
			}
		}
		return any(m)
	}
	return any(float64(0))
}
func feel_first(v any) any {
	if xs, ok := v.([]any); ok && len(xs) > 0 {
		return xs[0]
	}
	return any(nil)
}
func feel_last(v any) any {
	if xs, ok := v.([]any); ok && len(xs) > 0 {
		return xs[len(xs)-1]
	}
	return any(nil)
}
func feel_rest(v any) any {
	if xs, ok := v.([]any); ok && len(xs) > 0 {
		return any(xs[1:])
	}
	return any([]any{})
}
func feel_push(xs, item any) any {
	src, _ := xs.([]any)
	out := make([]any, 0, len(src)+1)
	out = append(out, src...)
	out = append(out, item)
	return any(out)
}
func feel_join(xs, sep any) any {
	if list, ok := xs.([]any); ok {
		parts := make([]string, len(list))
		for i, x := range list {
			parts[i] = feel_str(x)
		}
		return any(strings.Join(parts, feel_str(sep)))
	}
	return any("")
}
func feel_split(s, sep any) any {
	parts := strings.Split(feel_str(s), feel_str(sep))
	out := make([]any, len(parts))
	for i, p := range parts {
		out[i] = any(p)
	}
	return any(out)
}
func feel_contains(haystack, needle any) any {
	switch x := haystack.(type) {
	case string:
		return any(strings.Contains(x, feel_str(needle)))
	case []any:
		for _, it := range x {
			if feel_eq(it, needle) == any(true) {
				return any(true)
			}
		}
		return any(false)
	}
	return any(false)
}

// ---------- Builtins as values (for pipeline / closure use) ----------

var feel_show_fn = any(func(v any) any { feel_show(v); return v })
var feel_uppercase_fn = any(func(v any) any { return feel_uppercase(v) })
var feel_lowercase_fn = any(func(v any) any { return feel_lowercase(v) })
var feel_length_fn = any(func(v any) any { return feel_length(v) })
var feel_reverse_fn = any(func(v any) any { return feel_reverse(v) })
var feel_type_of_fn = any(func(v any) any { return feel_type_of(v) })
var feel_number_fn = any(func(v any) any { return feel_number(v) })
var feel_text_fn = any(func(v any) any { return feel_text(v) })
var feel_round_fn = any(func(v any) any { return feel_round(v) })
var feel_floor_fn = any(func(v any) any { return feel_floor(v) })
var feel_abs_fn = any(func(v any) any { return feel_abs(v) })
var feel_sum_fn = any(func(v any) any { return feel_sum(v) })
var feel_max_fn = any(func(v any) any { return feel_max(v) })
var feel_min_fn = any(func(v any) any { return feel_min(v) })
var feel_first_fn = any(func(v any) any { return feel_first(v) })
var feel_last_fn = any(func(v any) any { return feel_last(v) })
var feel_rest_fn = any(func(v any) any { return feel_rest(v) })

// ---------- Stdlib namespaces ----------
// Accessed in Feel as `string.trim(...)`, `list.fold(...)`, etc. Each module
// is a map[string]any; field access via feel_field returns a callable.

var feel_string_mod = map[string]any{
	"trim":        any(func(s any) any { return any(strings.TrimSpace(feel_str(s))) }),
	"trim_start":  any(func(s any) any { return any(strings.TrimLeft(feel_str(s), " \t\r\n")) }),
	"trim_end":    any(func(s any) any { return any(strings.TrimRight(feel_str(s), " \t\r\n")) }),
	"replace":     any(func(s, old, new any) any { return any(strings.ReplaceAll(feel_str(s), feel_str(old), feel_str(new))) }),
	"starts_with": any(func(s, prefix any) any { return any(strings.HasPrefix(feel_str(s), feel_str(prefix))) }),
	"ends_with":   any(func(s, suffix any) any { return any(strings.HasSuffix(feel_str(s), feel_str(suffix))) }),
	"contains":    any(func(s, sub any) any { return any(strings.Contains(feel_str(s), feel_str(sub))) }),
	"repeat":      any(func(s, n any) any { return any(strings.Repeat(feel_str(s), int(feel_num(n)))) }),
	"slice": any(func(args ...any) any {
		s := feel_str(args[0])
		start := int(feel_num(args[1]))
		if len(args) >= 3 {
			return any(s[start:int(feel_num(args[2]))])
		}
		return any(s[start:])
	}),
	"index_of": any(func(s, sub any) any { return any(float64(strings.Index(feel_str(s), feel_str(sub)))) }),
	"words":    any(func(s any) any { parts := strings.Fields(feel_str(s)); out := make([]any, len(parts)); for i, p := range parts { out[i] = any(p) }; return any(out) }),
	"lines":    any(func(s any) any { parts := strings.Split(feel_str(s), "\n"); out := make([]any, len(parts)); for i, p := range parts { out[i] = any(p) }; return any(out) }),
	"upper":    any(func(s any) any { return any(strings.ToUpper(feel_str(s))) }),
	"lower":    any(func(s any) any { return any(strings.ToLower(feel_str(s))) }),
}

var feel_list_mod = map[string]any{
	"range": any(func(args ...any) any {
		var start, end, step int
		if len(args) == 1 {
			start, end, step = 0, int(feel_num(args[0])), 1
		} else if len(args) == 2 {
			start, end, step = int(feel_num(args[0])), int(feel_num(args[1])), 1
		} else {
			start, end, step = int(feel_num(args[0])), int(feel_num(args[1])), int(feel_num(args[2]))
		}
		out := []any{}
		if step > 0 {
			for i := start; i < end; i += step { out = append(out, any(float64(i))) }
		} else if step < 0 {
			for i := start; i > end; i += step { out = append(out, any(float64(i))) }
		}
		return any(out)
	}),
	"reverse": any(func(xs any) any { return feel_reverse(xs) }),
	"take":    any(func(xs, n any) any { l, _ := xs.([]any); k := int(feel_num(n)); if k > len(l) { k = len(l) }; return any(l[:k]) }),
	"drop":    any(func(xs, n any) any { l, _ := xs.([]any); k := int(feel_num(n)); if k > len(l) { return any([]any{}) }; return any(l[k:]) }),
	"slice": any(func(args ...any) any {
		l, _ := args[0].([]any)
		start := int(feel_num(args[1]))
		if len(args) >= 3 {
			return any(l[start:int(feel_num(args[2]))])
		}
		return any(l[start:])
	}),
	"sort": any(func(xs any) any {
		l, _ := xs.([]any)
		out := make([]any, len(l))
		copy(out, l)
		sort.SliceStable(out, func(i, j int) bool {
			return feel_str(out[i]) < feel_str(out[j])
		})
		return any(out)
	}),
	"unique": any(func(xs any) any {
		l, _ := xs.([]any)
		seen := map[string]bool{}
		out := []any{}
		for _, v := range l {
			k := feel_str(v)
			if !seen[k] {
				seen[k] = true
				out = append(out, v)
			}
		}
		return any(out)
	}),
	"flatten": any(func(xs any) any {
		l, _ := xs.([]any)
		out := []any{}
		for _, v := range l {
			if sub, ok := v.([]any); ok {
				out = append(out, sub...)
			} else {
				out = append(out, v)
			}
		}
		return any(out)
	}),
	"count": any(func(xs, val any) any {
		l, _ := xs.([]any)
		n := 0
		for _, v := range l {
			if feel_eq(v, val) == any(true) { n++ }
		}
		return any(float64(n))
	}),
	"fold": any(func(xs, init, fn any) any {
		acc := init
		for _, it := range feel_iter(xs) {
			acc = feel_call(fn, []any{acc, it})
		}
		return acc
	}),
	"map": any(func(xs, fn any) any {
		out := []any{}
		for _, it := range feel_iter(xs) {
			out = append(out, feel_call(fn, []any{it}))
		}
		return any(out)
	}),
	"filter": any(func(xs, fn any) any {
		out := []any{}
		for _, it := range feel_iter(xs) {
			if feel_truthy(feel_call(fn, []any{it})) {
				out = append(out, it)
			}
		}
		return any(out)
	}),
}

var feel_map_mod = map[string]any{
	"get": any(func(args ...any) any {
		m, _ := args[0].(map[string]any)
		k := feel_str(args[1])
		if v, ok := m[k]; ok { return v }
		if len(args) >= 3 { return args[2] }
		return any(nil)
	}),
	"set": any(func(m, k, v any) any {
		src, _ := m.(map[string]any)
		out := make(map[string]any, len(src)+1)
		for kk, vv := range src { out[kk] = vv }
		out[feel_str(k)] = v
		return any(out)
	}),
	"has": any(func(m, k any) any {
		src, _ := m.(map[string]any)
		_, ok := src[feel_str(k)]
		return any(ok)
	}),
	"delete": any(func(m, k any) any {
		src, _ := m.(map[string]any)
		out := make(map[string]any, len(src))
		key := feel_str(k)
		for kk, vv := range src { if kk != key { out[kk] = vv } }
		return any(out)
	}),
	"keys": any(func(m any) any {
		src, _ := m.(map[string]any)
		out := []any{}
		for k := range src { if k != "__type__" { out = append(out, any(k)) } }
		return any(out)
	}),
	"values": any(func(m any) any {
		src, _ := m.(map[string]any)
		out := []any{}
		for k, v := range src { if k != "__type__" { out = append(out, v) } }
		return any(out)
	}),
	"entries": any(func(m any) any {
		src, _ := m.(map[string]any)
		out := []any{}
		for k, v := range src { if k != "__type__" { out = append(out, any([]any{any(k), v})) } }
		return any(out)
	}),
	"size": any(func(m any) any {
		src, _ := m.(map[string]any)
		n := 0
		for k := range src { if k != "__type__" { n++ } }
		return any(float64(n))
	}),
	"merge": any(func(a, b any) any {
		out := map[string]any{}
		if sa, ok := a.(map[string]any); ok { for k, v := range sa { out[k] = v } }
		if sb, ok := b.(map[string]any); ok { for k, v := range sb { out[k] = v } }
		return any(out)
	}),
}

var feel_json_mod = map[string]any{
	"encode": any(func(args ...any) any {
		clean := feel_to_jsonable(args[0])
		pretty := len(args) >= 2 && feel_truthy(args[1])
		var b []byte
		if pretty {
			b, _ = json.MarshalIndent(clean, "", "  ")
		} else {
			b, _ = json.Marshal(clean)
		}
		return any(string(b))
	}),
	"decode": any(func(s any) any {
		var out any
		if err := json.Unmarshal([]byte(feel_str(s)), &out); err != nil {
			panic(feel_throw{value: any("json decode: " + err.Error())})
		}
		return feel_from_jsonable(out)
	}),
}

func feel_to_jsonable(v any) any {
	switch x := v.(type) {
	case map[string]any:
		out := map[string]any{}
		for k, vv := range x { if k != "__type__" { out[k] = feel_to_jsonable(vv) } }
		return out
	case []any:
		out := make([]any, len(x))
		for i, vv := range x { out[i] = feel_to_jsonable(vv) }
		return out
	}
	return v
}

func feel_from_jsonable(v any) any {
	switch x := v.(type) {
	case map[string]any:
		out := map[string]any{}
		for k, vv := range x { out[k] = feel_from_jsonable(vv) }
		return any(out)
	case []any:
		out := make([]any, len(x))
		for i, vv := range x { out[i] = feel_from_jsonable(vv) }
		return any(out)
	case float64:
		// JSON has no int/float distinction; if the value is a whole number
		// safely representable as int64, return int64 so big IDs and counts
		// preserve precision through arithmetic.
		if x == float64(int64(x)) && x > -1e15 && x < 1e15 {
			return any(int64(x))
		}
		return v
	case string, bool, nil:
		return v
	}
	return v
}

// ---------- HTTP runtime ----------

func feel_compile_pattern(pattern string) (*regexp.Regexp, []string) {
	var params []string
	var sb strings.Builder
	sb.WriteString("^")
	i := 0
	for i < len(pattern) {
		c := pattern[i]
		if c == '{' {
			end := strings.IndexByte(pattern[i:], '}')
			if end == -1 {
				panic(feel_throw{value: any("unclosed { in pattern: " + pattern)})
			}
			name := pattern[i+1 : i+end]
			params = append(params, name)
			sb.WriteString("(?P<")
			sb.WriteString(name)
			sb.WriteString(">[^/]+)")
			i += end + 1
		} else {
			sb.WriteString(regexp.QuoteMeta(string(c)))
			i++
		}
	}
	sb.WriteString("$")
	return regexp.MustCompile(sb.String()), params
}

func feel_register_route(method, pattern string, handler func(map[string]any) any) {
	re, params := feel_compile_pattern(pattern)
	feel_routes = append(feel_routes, feel_route{
		method: strings.ToUpper(method), pattern: pattern, regex: re, params: params, handler: handler,
	})
}

func feel_resolve_route(method, path string) (*feel_route, map[string]string, []string) {
	pathMatched := false
	var methodsForPath []string
	method = strings.ToUpper(method)
	for i := range feel_routes {
		r := &feel_routes[i]
		m := r.regex.FindStringSubmatch(path)
		if m == nil {
			continue
		}
		pathMatched = true
		methodsForPath = append(methodsForPath, r.method)
		if r.method == method {
			captures := map[string]string{}
			for idx, name := range r.regex.SubexpNames() {
				if name != "" && idx < len(m) {
					captures[name] = m[idx]
				}
			}
			return r, captures, nil
		}
	}
	if pathMatched {
		return nil, nil, methodsForPath
	}
	return nil, nil, nil
}

func feel_response_from(v any) feel_response {
	if r, ok := v.(feel_response); ok {
		return r
	}
	return feel_response{status: 200, body: v}
}

func feel_encode_response(r feel_response) (int, string, []byte) {
	ct := r.contentType
	body := r.body
	if body == nil {
		if ct == "" {
			ct = "text/plain"
		}
		return r.status, ct, []byte{}
	}
	if b, ok := body.([]byte); ok {
		if ct == "" {
			ct = "application/octet-stream"
		}
		return r.status, ct, b
	}
	if s, ok := body.(string); ok {
		if ct == "" {
			ct = "text/plain; charset=utf-8"
		}
		return r.status, ct, []byte(s)
	}
	clean := feel_to_jsonable(body)
	data, err := json.Marshal(clean)
	if err != nil {
		return 500, "text/plain", []byte("json encode error: " + err.Error())
	}
	if ct == "" {
		ct = "application/json; charset=utf-8"
	}
	return r.status, ct, data
}

func feel_multipart_file(fh *multipart.FileHeader) map[string]any {
	// Eagerly read content into memory so handler doesn't need to manage streams.
	// 32MB-cap is enforced upstream by ParseMultipartForm.
	src, err := fh.Open()
	if err != nil {
		panic(feel_throw{value: any("multipart: open failed: " + err.Error())})
	}
	defer src.Close()
	content, err := io.ReadAll(src)
	if err != nil {
		panic(feel_throw{value: any("multipart: read failed: " + err.Error())})
	}
	ct := fh.Header.Get("Content-Type")
	if ct == "" {
		ct = "application/octet-stream"
	}
	saveTo := func(path any) any {
		p := feel_str(path)
		if dir := filepath.Dir(p); dir != "" && dir != "." {
			os.MkdirAll(dir, 0755)
		}
		if err := os.WriteFile(p, content, 0644); err != nil {
			panic(feel_throw{value: any("save_to: " + err.Error())})
		}
		abs, _ := filepath.Abs(p)
		return any(abs)
	}
	return map[string]any{
		"field_name":   any(""),
		"name":         any(fh.Filename),
		"size":         any(int64(len(content))),
		"content_type": any(ct),
		"content":      any(string(content)),
		"save_to":      any(saveTo),
	}
}

func feel_dispatch(w http.ResponseWriter, r *http.Request) {
	t0 := time.Now()
	method := r.Method
	path := r.URL.Path

	corsHeaders := map[string]string{}
	if feel_cors_enabled {
		corsHeaders["Access-Control-Allow-Origin"] = "*"
		corsHeaders["Access-Control-Allow-Methods"] = "GET, POST, PUT, PATCH, DELETE, HEAD, OPTIONS"
		corsHeaders["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Requested-With"
		corsHeaders["Access-Control-Max-Age"] = "86400"
	}

	// Preflight
	if feel_cors_enabled && method == "OPTIONS" {
		for k, v := range corsHeaders {
			w.Header().Set(k, v)
		}
		w.WriteHeader(204)
		fmt.Fprintf(os.Stderr, "OPTIONS %-40s -> 204  (%.1fms)\n", path, float64(time.Since(t0).Microseconds())/1000)
		return
	}

	// Panic mode → 503 immediately
	if feel_panic_flag {
		resp := feel_response{status: 503, body: map[string]any{
			"error":  any("service unavailable (panic mode)"),
			"reason": any(feel_panic_reason_text),
		}}
		status, ctype, body := feel_encode_response(resp)
		w.Header().Set("Content-Type", ctype)
		w.Header().Set("Content-Length", fmt.Sprintf("%d", len(body)))
		for k, v := range corsHeaders { w.Header().Set(k, v) }
		w.WriteHeader(status)
		w.Write(body)
		fmt.Fprintf(os.Stderr, "%-6s %-40s -> %d  (%.1fms) [panic]\n", method, path, status, float64(time.Since(t0).Microseconds())/1000)
		return
	}

	// WebSocket upgrade — handshake then call the WS handler.
	if method == "GET" && feel_is_ws_upgrade(r) {
		if wsRoute, wsCaps, _ := feel_resolve_route("WS", path); wsRoute != nil {
			feel_handle_ws_route(w, r, path, wsRoute, wsCaps, t0)
			return
		}
	}

	route, captures, methodsForPath := feel_resolve_route(method, path)

	var resp feel_response
	if route == nil && methodsForPath != nil {
		resp = feel_response{
			status: 405,
			body: map[string]any{"error": any("method not allowed"), "allowed": feel_strings_to_anys(methodsForPath)},
			headers: map[string]string{"Allow": strings.Join(methodsForPath, ", ")},
		}
	} else if route == nil {
		// No route matched. For GET/HEAD, try static mounts before 404.
		if method == "GET" || method == "HEAD" {
			if sresp, ok := feel_serve_static(path); ok {
				resp = sresp
			} else {
				resp = feel_response{status: 404, body: map[string]any{"error": any("not found"), "path": any(path)}}
			}
		} else {
			resp = feel_response{status: 404, body: map[string]any{"error": any("not found"), "path": any(path)}}
		}
	} else {
		// Build scope
		query := map[string]any{}
		for k, vs := range r.URL.Query() {
			if len(vs) == 1 {
				query[k] = any(vs[0])
			} else {
				items := make([]any, len(vs))
				for i, v := range vs { items[i] = any(v) }
				query[k] = any(items)
			}
		}
		headers := map[string]any{}
		for k, vs := range r.Header {
			if len(vs) > 0 {
				headers[strings.ToLower(k)] = any(vs[0])
			}
		}
		files := map[string]any{}
		form := map[string]any{}
		var bodyDecoded any
		ct := strings.ToLower(r.Header.Get("Content-Type"))

		if strings.Contains(ct, "multipart/form-data") {
			// 32 MiB in-memory cap; bigger files spill to disk per net/http default.
			if err := r.ParseMultipartForm(32 << 20); err == nil && r.MultipartForm != nil {
				for k, vs := range r.MultipartForm.Value {
					if len(vs) > 0 {
						form[k] = any(vs[0])
					}
				}
				for k, fhs := range r.MultipartForm.File {
					if len(fhs) == 0 {
						continue
					}
					fh := fhs[0]
					files[k] = any(feel_multipart_file(fh))
				}
				bodyDecoded = any(form)
			}
		} else if strings.Contains(ct, "application/x-www-form-urlencoded") {
			bodyRaw, _ := io.ReadAll(r.Body)
			values, err := url.ParseQuery(string(bodyRaw))
			if err == nil {
				m := map[string]any{}
				for k, vs := range values {
					if len(vs) == 1 {
						m[k] = any(vs[0])
					} else if len(vs) > 1 {
						list := make([]any, len(vs))
						for i, v := range vs {
							list[i] = any(v)
						}
						m[k] = any(list)
					}
				}
				bodyDecoded = any(m)
				form = m  // also expose via request.form for symmetry
			} else {
				bodyDecoded = any(string(bodyRaw))
			}
		} else {
			bodyRaw, _ := io.ReadAll(r.Body)
			if len(bodyRaw) > 0 {
				text := string(bodyRaw)
				if strings.Contains(ct, "application/json") || strings.HasPrefix(strings.TrimSpace(text), "{") || strings.HasPrefix(strings.TrimSpace(text), "[") {
					var decoded any
					if err := json.Unmarshal(bodyRaw, &decoded); err == nil {
						bodyDecoded = feel_from_jsonable(decoded)
					} else {
						bodyDecoded = any(text)
					}
				} else {
					bodyDecoded = any(text)
				}
			}
		}

		request := map[string]any{
			"method":  any(method),
			"path":    any(path),
			"query":   any(query),
			"headers": any(headers),
			"body":    bodyDecoded,
			"files":   any(files),
			"form":    any(form),
		}
		scope := map[string]any{
			"request": any(request),
			"body":    bodyDecoded,
			"query":   any(query),
		}
		for k, v := range captures {
			scope[k] = any(v)
		}

		func() {
			defer func() {
				if rec := recover(); rec != nil {
					if ft, ok := rec.(feel_throw); ok {
						resp = feel_response{status: 500, body: map[string]any{
							"error": any("unhandled throw"), "value": any(feel_str(ft.value)),
						}}
					} else {
						resp = feel_response{status: 500, body: map[string]any{
							"error": any("internal server error"), "detail": any(fmt.Sprint(rec)),
						}}
					}
				}
			}()
			raw := route.handler(scope)
			resp = feel_response_from(raw)
			// Lift session cookies attached by session.set / session.clear
			if bm, ok := resp.body.(map[string]any); ok {
				if rawCookies, ok := bm["__cookies__"]; ok {
					if list, ok := rawCookies.([]any); ok {
						if resp.headers == nil {
							resp.headers = map[string]string{}
						}
						for i, c := range list {
							resp.headers[fmt.Sprintf("Set-Cookie__%d", i)] = feel_str(c)
						}
						delete(bm, "__cookies__")
					}
				}
			}
		}()
	}

	status, ctype, bodyBytes := feel_encode_response(resp)
	w.Header().Set("Content-Type", ctype)
	w.Header().Set("Content-Length", fmt.Sprintf("%d", len(bodyBytes)))
	for k, v := range corsHeaders {
		w.Header().Set(k, v)
	}
	for k, v := range resp.headers {
		actual := k
		if strings.HasPrefix(k, "Set-Cookie__") {
			actual = "Set-Cookie"
			w.Header().Add(actual, v)
			continue
		}
		w.Header().Set(actual, v)
	}
	w.WriteHeader(status)
	w.Write(bodyBytes)
	fmt.Fprintf(os.Stderr, "%-6s %-40s -> %d  (%.1fms)\n", method, path, status, float64(time.Since(t0).Microseconds())/1000)
}

func feel_strings_to_anys(xs []string) []any {
	out := make([]any, len(xs))
	for i, s := range xs {
		out[i] = any(s)
	}
	return out
}

func feel_serve_http(port int, cors bool, certFile string, keyFile string) {
	feel_cors_enabled = cors
	addr := fmt.Sprintf(":%d", port)
	srv := &http.Server{Addr: addr, Handler: http.HandlerFunc(feel_dispatch)}
	corsNote := ""
	if cors {
		corsNote = " (CORS enabled)"
	}
	if certFile != "" && keyFile != "" {
		fmt.Fprintf(os.Stderr, "[feel] serving on https://localhost%s%s\n", addr, corsNote)
		if err := srv.ListenAndServeTLS(certFile, keyFile); err != nil && err != http.ErrServerClosed {
			panic(feel_throw{value: any(err.Error())})
		}
		return
	}
	fmt.Fprintf(os.Stderr, "[feel] serving on http://localhost%s%s\n", addr, corsNote)
	if err := srv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
		panic(feel_throw{value: any(err.Error())})
	}
}

// ---------- AI runtime ----------

func feel_ai_provider() string {
	if p := os.Getenv("FEEL_AI_PROVIDER"); p != "" {
		return strings.ToLower(p)
	}
	if os.Getenv("ANTHROPIC_API_KEY") != "" {
		return "claude"
	}
	return "mock"
}

func feel_ai_model() string {
	if m := os.Getenv("FEEL_AI_MODEL"); m != "" {
		return m
	}
	return "claude-sonnet-4-6"
}

func feel_claude_call(messages []any, system string, model string) string {
	apiKey := os.Getenv("ANTHROPIC_API_KEY")
	if apiKey == "" {
		panic(feel_throw{value: any("ANTHROPIC_API_KEY not set")})
	}
	if model == "" {
		model = feel_ai_model()
	}
	payload := map[string]any{
		"model":      model,
		"max_tokens": 1024,
		"messages":   messages,
	}
	if system != "" {
		payload["system"] = system
	}
	data, _ := json.Marshal(payload)
	req, _ := http.NewRequest("POST", "https://api.anthropic.com/v1/messages", bytes.NewReader(data))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("x-api-key", apiKey)
	req.Header.Set("anthropic-version", "2023-06-01")
	client := &http.Client{Timeout: 60 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		panic(feel_throw{value: any("Claude API error: " + err.Error())})
	}
	defer resp.Body.Close()
	bodyBytes, _ := io.ReadAll(resp.Body)
	if resp.StatusCode != 200 {
		panic(feel_throw{value: any(fmt.Sprintf("Claude API %d: %s", resp.StatusCode, string(bodyBytes)))})
	}
	var body map[string]any
	json.Unmarshal(bodyBytes, &body)
	if content, ok := body["content"].([]any); ok {
		for _, b := range content {
			if blk, ok := b.(map[string]any); ok {
				if blk["type"] == "text" {
					if t, ok := blk["text"].(string); ok {
						return t
					}
				}
			}
		}
	}
	return ""
}

func feel_ai_ask(prompt any) any {
	p := feel_ai_provider()
	if p == "mock" {
		snippet := feel_str(prompt)
		if len(snippet) > 80 {
			snippet = snippet[:80]
		}
		snippet = strings.ReplaceAll(snippet, "\n", " ")
		return any(fmt.Sprintf("[mock-ai] response to: '%s'", snippet))
	}
	return any(feel_claude_call([]any{
		map[string]any{"role": any("user"), "content": any(feel_str(prompt))},
	}, "", ""))
}

func feel_ai_summarize(text any) any {
	p := feel_ai_provider()
	if p == "mock" {
		s := feel_str(text)
		words := strings.Fields(s)
		head := words
		if len(head) > 8 {
			head = head[:8]
		}
		return any(fmt.Sprintf("[mock-summary] %d chars, starts: %s", len(s), strings.Join(head, " ")))
	}
	prompt := "Summarize the following text in 1-2 sentences:\n\n" + feel_str(text)
	return any(feel_claude_call([]any{
		map[string]any{"role": any("user"), "content": any(prompt)},
	}, "", ""))
}

func feel_ai_classify(text, options any) any {
	opts, _ := options.([]any)
	if len(opts) == 0 {
		panic(feel_throw{value: any("classify: options must be a non-empty list")})
	}
	p := feel_ai_provider()
	if p == "mock" {
		// Deterministic: hash-modulo
		s := feel_str(text)
		h := 0
		for _, c := range s {
			h = h*31 + int(c)
		}
		if h < 0 {
			h = -h
		}
		return opts[h%len(opts)]
	}
	optStrs := make([]string, len(opts))
	for i, o := range opts {
		optStrs[i] = `"` + feel_str(o) + `"`
	}
	prompt := fmt.Sprintf(
		"Classify the following text into exactly one of these categories: [%s].\n"+
			"Respond with ONLY the category name, no explanation.\n\n"+
			"Text: %s", strings.Join(optStrs, ", "), feel_str(text))
	raw := feel_claude_call([]any{
		map[string]any{"role": any("user"), "content": any(prompt)},
	}, "", "")
	answer := strings.Trim(strings.TrimSpace(raw), `"'`)
	lower := strings.ToLower(answer)
	for _, o := range opts {
		os := strings.ToLower(feel_str(o))
		if strings.HasPrefix(lower, os) || strings.Contains(lower, os) {
			return o
		}
	}
	return any(answer)
}

func feel_ai_chat(messages any, args ...any) any {
	msgs, _ := messages.([]any)
	system := ""
	if len(args) >= 1 {
		system = feel_str(args[0])
	}
	p := feel_ai_provider()
	if p == "mock" {
		// Find last user message
		last := ""
		for _, m := range msgs {
			if mm, ok := m.(map[string]any); ok {
				if mm["role"] == "user" || mm["role"] == any("user") {
					last = feel_str(mm["content"])
				}
			}
		}
		if len(last) > 60 {
			last = last[:60]
		}
		return any(fmt.Sprintf("[mock-chat] last user said: '%s'", last))
	}
	return any(feel_claude_call(msgs, system, ""))
}

var feel_ai_mod = map[string]any{
	"ask":       any(feel_ai_ask),
	"summarize": any(feel_ai_summarize),
	"classify":  any(feel_ai_classify),
	"chat":      any(func(args ...any) any { if len(args) == 0 { return any(nil) }; return feel_ai_chat(args[0], args[1:]...) }),
	"provider":  any(func() any { return any(feel_ai_provider()) }),
}

// ---------- Crypto primitives ----------

func feel_pbkdf2_sha256(password, salt []byte, iterations, keyLen int) []byte {
	h := func() hash.Hash { return sha256.New() }
	hashLen := h().Size()
	numBlocks := (keyLen + hashLen - 1) / hashLen
	out := make([]byte, 0, numBlocks*hashLen)
	buf := make([]byte, 4)
	for block := 1; block <= numBlocks; block++ {
		binary.BigEndian.PutUint32(buf, uint32(block))
		u := hmac.New(h, password)
		u.Write(salt)
		u.Write(buf)
		currentU := u.Sum(nil)
		result := make([]byte, hashLen)
		copy(result, currentU)
		for i := 1; i < iterations; i++ {
			u = hmac.New(h, password)
			u.Write(currentU)
			currentU = u.Sum(nil)
			for j := range result {
				result[j] ^= currentU[j]
			}
		}
		out = append(out, result...)
	}
	return out[:keyLen]
}

func feel_crypto_hash_password(args ...any) any {
	password := feel_str(args[0])
	iters := 100000
	if len(args) >= 2 {
		iters = int(feel_num(args[1]))
	}
	salt := make([]byte, 16)
	if _, err := rand.Read(salt); err != nil {
		panic(feel_throw{value: any("crypto.hash_password: rand failed: " + err.Error())})
	}
	derived := feel_pbkdf2_sha256([]byte(password), salt, iters, 32)
	return any(fmt.Sprintf("pbkdf2_sha256$%d$%s$%s",
		iters,
		base64.StdEncoding.EncodeToString(salt),
		base64.StdEncoding.EncodeToString(derived)))
}

func feel_crypto_verify_password(password, hashed any) any {
	parts := strings.Split(feel_str(hashed), "$")
	if len(parts) != 4 {
		return any(false)
	}
	if parts[0] != "pbkdf2_sha256" {
		return any(false)
	}
	var iters int
	if _, err := fmt.Sscanf(parts[1], "%d", &iters); err != nil {
		return any(false)
	}
	salt, err := base64.StdEncoding.DecodeString(parts[2])
	if err != nil {
		return any(false)
	}
	expected, err := base64.StdEncoding.DecodeString(parts[3])
	if err != nil {
		return any(false)
	}
	derived := feel_pbkdf2_sha256([]byte(feel_str(password)), salt, iters, len(expected))
	return any(hmac.Equal(derived, expected))
}

func feel_b64url_encode(data []byte) string {
	return base64.URLEncoding.WithPadding(base64.NoPadding).EncodeToString(data)
}

func feel_b64url_decode(s string) ([]byte, error) {
	return base64.URLEncoding.WithPadding(base64.NoPadding).DecodeString(s)
}

func feel_crypto_jwt_sign(payload, secret any) any {
	pm, ok := payload.(map[string]any)
	if !ok {
		panic(feel_throw{value: any("jwt_sign: payload must be a map")})
	}
	headerBytes, _ := json.Marshal(map[string]any{"alg": "HS256", "typ": "JWT"})
	// Compact + sorted-keys JSON for byte-stable signing across implementations.
	payloadBytes, err := json.Marshal(feel_to_jsonable(pm))
	if err != nil {
		panic(feel_throw{value: any("jwt_sign: encode error: " + err.Error())})
	}
	hEnc := feel_b64url_encode(headerBytes)
	pEnc := feel_b64url_encode(payloadBytes)
	signingInput := hEnc + "." + pEnc
	mac := hmac.New(sha256.New, []byte(feel_str(secret)))
	mac.Write([]byte(signingInput))
	sig := mac.Sum(nil)
	return any(signingInput + "." + feel_b64url_encode(sig))
}

func feel_crypto_jwt_verify(token, secret any) any {
	parts := strings.Split(feel_str(token), ".")
	if len(parts) != 3 {
		return any(nil)
	}
	signingInput := parts[0] + "." + parts[1]
	mac := hmac.New(sha256.New, []byte(feel_str(secret)))
	mac.Write([]byte(signingInput))
	expected := mac.Sum(nil)
	actual, err := feel_b64url_decode(parts[2])
	if err != nil || !hmac.Equal(expected, actual) {
		return any(nil)
	}
	payloadBytes, err := feel_b64url_decode(parts[1])
	if err != nil {
		return any(nil)
	}
	var decoded any
	if err := json.Unmarshal(payloadBytes, &decoded); err != nil {
		return any(nil)
	}
	return feel_from_jsonable(decoded)
}

func feel_crypto_hmac_sha256(key, message any) any {
	mac := hmac.New(sha256.New, []byte(feel_str(key)))
	mac.Write([]byte(feel_str(message)))
	return any(hex.EncodeToString(mac.Sum(nil)))
}

func feel_crypto_random_bytes(n any) any {
	count := int(feel_num(n))
	if count <= 0 {
		return any("")
	}
	buf := make([]byte, count)
	if _, err := rand.Read(buf); err != nil {
		panic(feel_throw{value: any("crypto.random_bytes: " + err.Error())})
	}
	return any(hex.EncodeToString(buf))
}

func feel_crypto_random_token(args ...any) any {
	count := 32
	if len(args) >= 1 {
		count = int(feel_num(args[0]))
	}
	if count <= 0 {
		return any("")
	}
	buf := make([]byte, count)
	if _, err := rand.Read(buf); err != nil {
		panic(feel_throw{value: any("crypto.random_token: " + err.Error())})
	}
	return any(feel_b64url_encode(buf))
}

func feel_crypto_base64_encode(data any) any {
	var raw []byte
	if b, ok := data.([]byte); ok {
		raw = b
	} else {
		raw = []byte(feel_str(data))
	}
	return any(base64.StdEncoding.EncodeToString(raw))
}

func feel_crypto_base64_decode(s any) any {
	b, err := base64.StdEncoding.DecodeString(feel_str(s))
	if err != nil {
		panic(feel_throw{value: any("base64_decode: " + err.Error())})
	}
	return any(string(b))
}

var feel_crypto_mod = map[string]any{
	"hash_password":   any(feel_crypto_hash_password),
	"verify_password": any(feel_crypto_verify_password),
	"jwt_sign":        any(feel_crypto_jwt_sign),
	"jwt_verify":      any(feel_crypto_jwt_verify),
	"hmac_sha256":     any(feel_crypto_hmac_sha256),
	"random_bytes":    any(feel_crypto_random_bytes),
	"random_token":    any(feel_crypto_random_token),
	"base64_encode":   any(feel_crypto_base64_encode),
	"base64_decode":   any(feel_crypto_base64_decode),
}

// ---------- Security primitives ----------

type feel_closable interface{ Close() error }

var feel_sec_lock sync.Mutex
var feel_panic_flag bool
var feel_panic_reason_text string
var feel_kill_switches []any
var feel_rate_buckets = map[string][]float64{}
var feel_failed_attempts = map[string][]float64{}
var feel_audit_path = ""

func feel_now_seconds() float64 {
	return float64(time.Now().UnixNano()) / 1e9
}

func feel_security_rate_limit(args ...any) any {
	feel_sec_lock.Lock()
	defer feel_sec_lock.Unlock()
	if len(args) < 3 {
		return any(true)
	}
	key := feel_str(args[0])
	maxReq := int(feel_num(args[1]))
	window := feel_num(args[2])
	now := feel_now_seconds()
	cutoff := now - window
	bucket := feel_rate_buckets[key]
	start := 0
	for start < len(bucket) && bucket[start] < cutoff {
		start++
	}
	bucket = bucket[start:]
	if len(bucket) >= maxReq {
		feel_rate_buckets[key] = bucket
		return any(false)
	}
	bucket = append(bucket, now)
	feel_rate_buckets[key] = bucket
	return any(true)
}

func feel_security_report_failed(args ...any) any {
	feel_sec_lock.Lock()
	defer feel_sec_lock.Unlock()
	if len(args) < 3 {
		return any(false)
	}
	key := feel_str(args[0])
	maxFail := int(feel_num(args[1]))
	window := feel_num(args[2])
	now := feel_now_seconds()
	cutoff := now - window
	bucket := feel_failed_attempts[key]
	start := 0
	for start < len(bucket) && bucket[start] < cutoff {
		start++
	}
	bucket = bucket[start:]
	bucket = append(bucket, now)
	feel_failed_attempts[key] = bucket
	return any(len(bucket) >= maxFail)
}

func feel_security_panic(reason any) any {
	feel_sec_lock.Lock()
	defer feel_sec_lock.Unlock()
	if feel_panic_flag {
		return any(false)
	}
	feel_panic_flag = true
	feel_panic_reason_text = feel_str(reason)
	for _, conn := range feel_kill_switches {
		if c, ok := conn.(feel_closable); ok {
			_ = c.Close()
		}
	}
	feel_kill_switches = nil
	feel_audit_event(map[string]any{
		"type": "PANIC", "reason": feel_panic_reason_text,
	})
	fmt.Fprintf(os.Stderr, "[SECURITY] PANIC MODE ACTIVE: %s\n", feel_panic_reason_text)
	return any(true)
}

func feel_security_is_panic_mode() any { return any(feel_panic_flag) }
func feel_security_panic_reason() any  { return any(feel_panic_reason_text) }

func feel_security_kill_switch(conn any) any {
	feel_sec_lock.Lock()
	defer feel_sec_lock.Unlock()
	feel_kill_switches = append(feel_kill_switches, conn)
	return any(true)
}

func feel_security_audit(event any) any {
	feel_audit_event(event)
	return any(true)
}

func feel_security_set_audit_log(args ...any) any {
	feel_sec_lock.Lock()
	defer feel_sec_lock.Unlock()
	if len(args) >= 1 && args[0] != nil {
		feel_audit_path = feel_str(args[0])
	} else {
		feel_audit_path = ""
	}
	return any(true)
}

func feel_security_reset() any {
	feel_sec_lock.Lock()
	defer feel_sec_lock.Unlock()
	feel_panic_flag = false
	feel_panic_reason_text = ""
	feel_rate_buckets = map[string][]float64{}
	feel_failed_attempts = map[string][]float64{}
	feel_kill_switches = nil
	return any(true)
}

func feel_audit_event(event any) {
	var line string
	if m, ok := event.(map[string]any); ok {
		entry := map[string]any{}
		for k, v := range m { entry[k] = v }
		if _, ok := entry["time"]; !ok {
			entry["time"] = feel_now_seconds()
		}
		data, _ := json.Marshal(feel_to_jsonable(entry))
		line = string(data)
	} else {
		line = fmt.Sprintf("%.3f: %s", feel_now_seconds(), feel_str(event))
	}
	if feel_audit_path != "" {
		f, err := os.OpenFile(feel_audit_path, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0644)
		if err == nil {
			fmt.Fprintln(f, line)
			f.Close()
			return
		}
	}
	fmt.Fprintf(os.Stderr, "[audit] %s\n", line)
}

var feel_security_mod = map[string]any{
	"rate_limit":    any(feel_security_rate_limit),
	"report_failed": any(feel_security_report_failed),
	"panic":         any(feel_security_panic),
	"is_panic_mode": any(feel_security_is_panic_mode),
	"panic_reason":  any(feel_security_panic_reason),
	"kill_switch":   any(feel_security_kill_switch),
	"audit":         any(feel_security_audit),
	"set_audit_log": any(feel_security_set_audit_log),
	"reset":         any(feel_security_reset),
}

// ---------- time / math / file modules ----------

var feel_time_mod = map[string]any{
	"now":     any(func() any { return any(float64(time.Now().Unix())) }),
	"now_ms":  any(func() any { return any(float64(time.Now().UnixMilli())) }),
	"sleep":   any(func(ms any) any { time.Sleep(time.Duration(feel_num(ms)) * time.Millisecond); return any(nil) }),
	"format": any(func(args ...any) any {
		var t time.Time
		if len(args) >= 1 && args[0] != nil {
			t = time.Unix(int64(feel_num(args[0])), 0)
		} else {
			t = time.Now()
		}
		layout := "2006-01-02 15:04:05"
		if len(args) >= 2 {
			// Convert Python-style strftime-ish to Go layout. Common cases only.
			py := feel_str(args[1])
			repl := strings.NewReplacer(
				"%Y", "2006", "%m", "01", "%d", "02",
				"%H", "15", "%M", "04", "%S", "05",
			)
			layout = repl.Replace(py)
		}
		return any(t.Format(layout))
	}),
	"iso_now": any(func() any { return any(time.Now().Format(time.RFC3339)) }),
}

var feel_math_mod = map[string]any{
	"pi":            any(math.Pi),
	"e":             any(math.E),
	"sqrt":          any(func(x any) any { return any(math.Sqrt(feel_num(x))) }),
	"pow":           any(func(b, e any) any { return any(math.Pow(feel_num(b), feel_num(e))) }),
	"log":           any(func(args ...any) any {
		if len(args) >= 2 {
			return any(math.Log(feel_num(args[0])) / math.Log(feel_num(args[1])))
		}
		return any(math.Log(feel_num(args[0])))
	}),
	"sin":           any(func(x any) any { return any(math.Sin(feel_num(x))) }),
	"cos":           any(func(x any) any { return any(math.Cos(feel_num(x))) }),
	"tan":           any(func(x any) any { return any(math.Tan(feel_num(x))) }),
	"ceil":          any(func(x any) any { return any(math.Ceil(feel_num(x))) }),
	"floor":         any(func(x any) any { return any(math.Floor(feel_num(x))) }),
	"round":         any(func(args ...any) any {
		x := feel_num(args[0])
		digits := 0
		if len(args) >= 2 {
			digits = int(feel_num(args[1]))
		}
		mult := math.Pow(10, float64(digits))
		return any(math.Round(x*mult) / mult)
	}),
	"random":        any(func() any { return any(feel_rand_float()) }),
	"random_int":    any(func(lo, hi any) any { return any(float64(feel_rand_int(int(feel_num(lo)), int(feel_num(hi))))) }),
	"random_choice": any(func(items any) any {
		l, _ := items.([]any)
		if len(l) == 0 { return any(nil) }
		return l[feel_rand_int(0, len(l)-1)]
	}),
}

func feel_rand_float() float64 {
	var b [8]byte
	if _, err := rand.Read(b[:]); err != nil {
		return 0
	}
	u := uint64(b[0]) | uint64(b[1])<<8 | uint64(b[2])<<16 | uint64(b[3])<<24 |
		uint64(b[4])<<32 | uint64(b[5])<<40 | uint64(b[6])<<48 | uint64(b[7])<<56
	return float64(u>>11) / float64(uint64(1)<<53)
}

func feel_rand_int(lo, hi int) int {
	if hi < lo { lo, hi = hi, lo }
	span := hi - lo + 1
	return lo + int(feel_rand_float()*float64(span))
}

var feel_file_mod = map[string]any{
	"read":     any(func(path any) any {
		data, err := os.ReadFile(feel_str(path))
		if err != nil { panic(feel_throw{value: any("file.read: " + err.Error())}) }
		return any(string(data))
	}),
	"write":    any(func(path, content any) any {
		err := os.WriteFile(feel_str(path), []byte(feel_str(content)), 0644)
		if err != nil { panic(feel_throw{value: any("file.write: " + err.Error())}) }
		return any(true)
	}),
	"append":   any(func(path, content any) any {
		f, err := os.OpenFile(feel_str(path), os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0644)
		if err != nil { panic(feel_throw{value: any("file.append: " + err.Error())}) }
		defer f.Close()
		f.WriteString(feel_str(content))
		return any(true)
	}),
	"exists":   any(func(path any) any { _, err := os.Stat(feel_str(path)); return any(err == nil) }),
	"is_file":  any(func(path any) any { info, err := os.Stat(feel_str(path)); return any(err == nil && !info.IsDir()) }),
	"is_dir":   any(func(path any) any { info, err := os.Stat(feel_str(path)); return any(err == nil && info.IsDir()) }),
	"list_dir": any(func(path any) any {
		entries, err := os.ReadDir(feel_str(path))
		if err != nil { panic(feel_throw{value: any("file.list_dir: " + err.Error())}) }
		out := make([]any, len(entries))
		for i, e := range entries { out[i] = any(e.Name()) }
		return any(out)
	}),
	"delete":   any(func(path any) any { err := os.Remove(feel_str(path)); return any(err == nil) }),
	"basename": any(func(path any) any { return any(filepath.Base(feel_str(path))) }),
	"dirname":  any(func(path any) any { return any(filepath.Dir(feel_str(path))) }),
	"join":     any(func(parts ...any) any {
		strs := make([]string, len(parts))
		for i, p := range parts { strs[i] = feel_str(p) }
		return any(filepath.Join(strs...))
	}),
}

// ---------- Validate (record schemas) ----------

var feel_record_schemas = map[string]map[string]string{}

func feel_check_type(v any, declared string, depth int) bool {
	if depth > 8 {
		return true
	}
	switch declared {
	case "text":
		_, ok := v.(string); return ok
	case "number":
		if _, ok := v.(float64); ok { return true }
		if _, ok := v.(int); ok { return true }
		if _, ok := v.(int64); ok { return true }
		return false
	case "boolean":
		_, ok := v.(bool); return ok
	case "list":
		_, ok := v.([]any); return ok
	case "map":
		_, ok := v.(map[string]any); return ok
	case "nothing":
		return v == nil
	}
	// Nested record reference
	if sub, ok := feel_record_schemas[declared]; ok {
		m, ok := v.(map[string]any)
		if !ok { return false }
		for f, t := range sub {
			vv, present := m[f]
			if !present || !feel_check_type(vv, t, depth+1) { return false }
		}
		return true
	}
	return true
}

func feel_validate_shape(value, recordName any) any {
	name := feel_str(recordName)
	schema, ok := feel_record_schemas[name]
	if !ok {
		panic(feel_throw{value: any("validate.shape: unknown record '" + name + "'")})
	}
	m, ok := value.(map[string]any)
	if !ok {
		panic(feel_throw{value: any(fmt.Sprintf("validate.shape: expected map, got %T", value))})
	}
	var errs []string
	for field, typeName := range schema {
		v, present := m[field]
		if !present {
			errs = append(errs, "missing field '" + field + "'")
			continue
		}
		if !feel_check_type(v, typeName, 0) {
			errs = append(errs, "field '" + field + "' must be " + typeName)
		}
	}
	if len(errs) > 0 {
		panic(feel_throw{value: any("validation failed: " + strings.Join(errs, "; "))})
	}
	return value
}

func feel_validate_is_valid(value, recordName any) any {
	defer func() { _ = recover() }()
	result := any(false)
	func() {
		defer func() {
			if r := recover(); r != nil {
				result = any(false)
			}
		}()
		feel_validate_shape(value, recordName)
		result = any(true)
	}()
	return result
}

func feel_validate_errors_for(value, recordName any) any {
	out := []any{}
	defer func() {
		if r := recover(); r != nil {
			if ft, ok := r.(feel_throw); ok {
				msg := feel_str(ft.value)
				if strings.Contains(msg, "validation failed: ") {
					list := strings.Split(strings.SplitN(msg, "validation failed: ", 2)[1], "; ")
					for _, e := range list {
						out = append(out, any(e))
					}
				} else {
					out = append(out, any(msg))
				}
			}
		}
	}()
	feel_validate_shape(value, recordName)
	return any(out)
}

var feel_validate_mod = map[string]any{
	"shape":      any(feel_validate_shape),
	"is_valid":   any(feel_validate_is_valid),
	"errors_for": any(feel_validate_errors_for),
}

// ---------- Queue moved to RUNTIME_GO_DB_BODY (needs feel_db_*) ----------
/*
func feel_queue_enqueue_legacy(qconn, name, payload any) any {
	data, _ := json.Marshal(feel_to_jsonable(payload))
	feel_db_exec(qconn, any("INSERT INTO jobs (queue_name, payload) VALUES (?, ?)"),
		any([]any{any(feel_str(name)), any(string(data))}))
	return feel_db_last_id(qconn)
}

func feel_queue_pop(qconn, name any) any {
	feel_db_begin(qconn)
	defer func() {
		if r := recover(); r != nil {
			func() { defer func() { recover() }(); feel_db_rollback(qconn) }()
			panic(r)
		}
	}()
	row := feel_db_query_one(qconn,
		any("SELECT id, payload, attempts FROM jobs WHERE queue_name = ? AND status = 'pending' ORDER BY id ASC LIMIT 1"),
		any([]any{any(feel_str(name))}))
	if row == nil {
		feel_db_commit(qconn)
		return any(nil)
	}
	r, _ := row.(map[string]any)
	id := r["id"]
	feel_db_exec(qconn,
		any("UPDATE jobs SET status = 'running', leased_at = CURRENT_TIMESTAMP, attempts = attempts + 1 WHERE id = ?"),
		any([]any{id}))
	feel_db_commit(qconn)

	var decoded any
	if raw, ok := r["payload"].(string); ok {
		_ = json.Unmarshal([]byte(raw), &decoded)
		decoded = feel_from_jsonable(decoded)
	}
	return any(map[string]any{
		"id":       id,
		"payload":  decoded,
		"attempts": any(feel_num(r["attempts"]) + 1),
	})
}

func feel_queue_complete(qconn, jobID any) any {
	feel_db_exec(qconn, any("DELETE FROM jobs WHERE id = ?"), any([]any{any(int64(feel_num(jobID)))}))
	return any(true)
}

func feel_queue_fail(args ...any) any {
	if len(args) < 3 {
		panic(feel_throw{value: any("queue.fail: need (qconn, job_id, error [, max_attempts])")})
	}
	qconn := args[0]
	jobID := int64(feel_num(args[1]))
	errMsg := feel_str(args[2])
	maxAttempts := 3
	if len(args) >= 4 {
		maxAttempts = int(feel_num(args[3]))
	}
	row := feel_db_query_one(qconn, any("SELECT attempts FROM jobs WHERE id = ?"), any([]any{any(jobID)}))
	if row == nil {
		return any(false)
	}
	r, _ := row.(map[string]any)
	if int(feel_num(r["attempts"])) >= maxAttempts {
		feel_db_exec(qconn, any("UPDATE jobs SET status = 'failed', last_error = ? WHERE id = ?"),
			any([]any{any(errMsg), any(jobID)}))
	} else {
		feel_db_exec(qconn, any("UPDATE jobs SET status = 'pending', last_error = ?, leased_at = NULL WHERE id = ?"),
			any([]any{any(errMsg), any(jobID)}))
	}
	return any(true)
}

func feel_queue_pending(qconn, name any) any {
	row := feel_db_query_one(qconn,
		any("SELECT COUNT(*) AS n FROM jobs WHERE queue_name = ? AND status = 'pending'"),
		any([]any{any(feel_str(name))}))
	if row == nil { return any(float64(0)) }
	r, _ := row.(map[string]any)
	return r["n"]
}

func feel_queue_process_once(qconn, name, handler any) any {
	job := feel_queue_pop(qconn, name)
	if job == nil {
		return any(false)
	}
	jm, _ := job.(map[string]any)
	jobID := jm["id"]
	payload := jm["payload"]
	func() {
		defer func() {
			if r := recover(); r != nil {
				msg := fmt.Sprintf("%v", r)
				if ft, ok := r.(feel_throw); ok { msg = feel_str(ft.value) }
				feel_queue_fail(qconn, jobID, any(msg))
			}
		}()
		feel_call(handler, []any{payload})
		feel_queue_complete(qconn, jobID)
	}()
	return any(true)
}

func feel_queue_work(args ...any) any {
	if len(args) < 3 {
		panic(feel_throw{value: any("queue.work: need (qconn, name, handler [, poll_ms])")})
	}
	qconn := args[0]
	name := args[1]
	handler := args[2]
	pollMs := 500
	if len(args) >= 4 {
		pollMs = int(feel_num(args[3]))
	}
	fmt.Fprintf(os.Stderr, "[queue] worker started: queue=%q, poll=%dms\n", feel_str(name), pollMs)
	for {
		processed := feel_queue_process_once(qconn, name, handler)
		if processed != any(true) {
			time.Sleep(time.Duration(pollMs) * time.Millisecond)
		}
	}
}

var feel_queue_mod_placeholder = map[string]any{}
*/
// queue functions moved into RUNTIME_GO_DB_BODY below; feel_queue_mod redefined there.

// ---------- Mail (mock + SMTP) ----------

var feel_mail_lock sync.Mutex
var feel_mail_sent []any

func feel_mail_provider() string {
	if p := os.Getenv("FEEL_MAIL_PROVIDER"); p != "" {
		return strings.ToLower(p)
	}
	return "mock"
}

func feel_mail_send(message any) any {
	msg, ok := message.(map[string]any)
	if !ok {
		panic(feel_throw{value: any("mail.send: expected a map")})
	}
	to, _ := msg["to"].(string)
	subject, _ := msg["subject"].(string)
	body, _ := msg["body"].(string)
	from, _ := msg["from"].(string)
	if to == "" {
		panic(feel_throw{value: any("mail.send: missing 'to'")})
	}
	if subject == "" {
		panic(feel_throw{value: any("mail.send: missing 'subject'")})
	}
	if body == "" {
		panic(feel_throw{value: any("mail.send: missing 'body'")})
	}
	if from == "" {
		from = os.Getenv("FEEL_MAIL_FROM")
		if from == "" { from = "noreply@feel.local" }
	}

	p := feel_mail_provider()
	if p == "mock" {
		feel_mail_lock.Lock()
		feel_mail_sent = append(feel_mail_sent, map[string]any{
			"to": any(to), "subject": any(subject), "body": any(body), "from": any(from),
		})
		feel_mail_lock.Unlock()
		return any(true)
	}
	if p == "smtp" {
		host := os.Getenv("FEEL_SMTP_HOST")
		if host == "" {
			panic(feel_throw{value: any("mail.send: FEEL_SMTP_HOST not set")})
		}
		port := os.Getenv("FEEL_SMTP_PORT")
		if port == "" { port = "587" }
		user := os.Getenv("FEEL_SMTP_USER")
		pw := os.Getenv("FEEL_SMTP_PASS")
		addr := host + ":" + port
		msg := []byte("From: " + from + "\r\n" +
			"To: " + to + "\r\n" +
			"Subject: " + subject + "\r\n" +
			"Content-Type: text/plain; charset=utf-8\r\n" +
			"\r\n" + body)
		var auth smtp.Auth
		if user != "" {
			auth = smtp.PlainAuth("", user, pw, host)
		}
		if err := smtp.SendMail(addr, auth, from, []string{to}, msg); err != nil {
			panic(feel_throw{value: any("mail.send SMTP error: " + err.Error())})
		}
		return any(true)
	}
	panic(feel_throw{value: any("mail.send: unknown provider " + p)})
}

func feel_mail_sent_list() any {
	feel_mail_lock.Lock()
	defer feel_mail_lock.Unlock()
	out := make([]any, len(feel_mail_sent))
	copy(out, feel_mail_sent)
	return any(out)
}

func feel_mail_clear_sent() any {
	feel_mail_lock.Lock()
	feel_mail_sent = nil
	feel_mail_lock.Unlock()
	return any(true)
}

var feel_mail_mod = map[string]any{
	"send":       any(feel_mail_send),
	"provider":   any(func() any { return any(feel_mail_provider()) }),
	"sent":       any(feel_mail_sent_list),
	"clear_sent": any(feel_mail_clear_sent),
}

// ---------- Auth helpers (M4-E follow-up) ----------

func feel_auth_extract_bearer(request any) any {
	req, ok := request.(map[string]any)
	if !ok {
		return any(nil)
	}
	headers, ok := req["headers"].(map[string]any)
	if !ok {
		return any(nil)
	}
	raw, _ := headers["authorization"].(string)
	if raw == "" {
		return any(nil)
	}
	if strings.HasPrefix(raw, "Bearer ") {
		return any(raw[len("Bearer "):])
	}
	return any(nil)
}

func feel_auth_require_jwt(request, secret any) any {
	tok := feel_auth_extract_bearer(request)
	if tok == nil {
		panic(feel_throw{value: any("auth: missing Authorization Bearer token")})
	}
	payload := feel_crypto_jwt_verify(tok, secret)
	if payload == nil {
		panic(feel_throw{value: any("auth: invalid or expired token")})
	}
	return payload
}

func feel_auth_optional_jwt(request, secret any) any {
	tok := feel_auth_extract_bearer(request)
	if tok == nil {
		return any(nil)
	}
	return feel_crypto_jwt_verify(tok, secret)
}

var feel_auth_mod = map[string]any{
	"extract_bearer": any(feel_auth_extract_bearer),
	"require_jwt":    any(feel_auth_require_jwt),
	"optional_jwt":   any(feel_auth_optional_jwt),
}

// ---------- Session helpers (HMAC-signed cookies) ----------

func feel_sign_cookie_value(value, secret any) string {
	v := feel_str(value)
	sig := feel_str(feel_crypto_hmac_sha256(secret, any(v)))
	return v + "." + sig
}

func feel_verify_cookie_value(signed, secret any) any {
	s, ok := signed.(string)
	if !ok {
		return any(nil)
	}
	dot := strings.LastIndex(s, ".")
	if dot < 0 {
		return any(nil)
	}
	val := s[:dot]
	sig := s[dot+1:]
	expected := feel_str(feel_crypto_hmac_sha256(secret, any(val)))
	if expected != sig {
		return any(nil)
	}
	return any(val)
}

func feel_session_set(args ...any) any {
	if len(args) < 4 {
		panic(feel_throw{value: any("session.set: need (response, key, value, secret [, max_age])")})
	}
	resp, _ := args[0].(map[string]any)
	if resp == nil {
		resp = map[string]any{"status": any(200), "body": args[0]}
	}
	key := feel_str(args[1])
	value := args[2]
	secret := args[3]
	maxAge := 86400
	if len(args) >= 5 {
		maxAge = int(feel_num(args[4]))
	}
	signed := feel_sign_cookie_value(value, secret)
	cookie := fmt.Sprintf("%s=%s; Path=/; Max-Age=%d; HttpOnly; SameSite=Lax",
		key, signed, maxAge)
	cookies, _ := resp["__cookies__"].([]any)
	cookies = append(cookies, any(cookie))
	resp["__cookies__"] = any(cookies)
	return any(resp)
}

func feel_session_get(request, key, secret any) any {
	req, ok := request.(map[string]any)
	if !ok {
		return any(nil)
	}
	headers, ok := req["headers"].(map[string]any)
	if !ok {
		return any(nil)
	}
	raw, _ := headers["cookie"].(string)
	if raw == "" {
		return any(nil)
	}
	target := feel_str(key)
	for _, part := range strings.Split(raw, ";") {
		part = strings.TrimSpace(part)
		eq := strings.IndexByte(part, '=')
		if eq < 0 {
			continue
		}
		if part[:eq] == target {
			return feel_verify_cookie_value(any(strings.TrimSpace(part[eq+1:])), secret)
		}
	}
	return any(nil)
}

func feel_session_clear(response, key any) any {
	resp, _ := response.(map[string]any)
	if resp == nil {
		resp = map[string]any{"status": any(200), "body": response}
	}
	cookie := fmt.Sprintf("%s=; Path=/; Max-Age=0; HttpOnly", feel_str(key))
	cookies, _ := resp["__cookies__"].([]any)
	cookies = append(cookies, any(cookie))
	resp["__cookies__"] = any(cookies)
	return any(resp)
}

var feel_session_mod = map[string]any{
	"set":   any(feel_session_set),
	"get":   any(feel_session_get),
	"clear": any(feel_session_clear),
}

// ---------- Cache (in-memory with TTL) ----------

type feel_cache_entry struct {
	value     any
	expiresAt float64 // 0 = no expiry
}

var feel_cache_lock sync.RWMutex
var feel_cache_store = map[string]feel_cache_entry{}

func feel_cache_set(args ...any) any {
	if len(args) < 2 {
		panic(feel_throw{value: any("cache.set: need (key, value [, ttl_seconds])")})
	}
	key := feel_str(args[0])
	val := args[1]
	exp := 0.0
	if len(args) >= 3 && args[2] != nil {
		exp = feel_now_seconds() + feel_num(args[2])
	}
	feel_cache_lock.Lock()
	feel_cache_store[key] = feel_cache_entry{value: val, expiresAt: exp}
	feel_cache_lock.Unlock()
	return val
}

func feel_cache_get(key any) any {
	k := feel_str(key)
	feel_cache_lock.RLock()
	entry, ok := feel_cache_store[k]
	feel_cache_lock.RUnlock()
	if !ok {
		return any(nil)
	}
	if entry.expiresAt > 0 && entry.expiresAt <= feel_now_seconds() {
		feel_cache_lock.Lock()
		delete(feel_cache_store, k)
		feel_cache_lock.Unlock()
		return any(nil)
	}
	return entry.value
}

func feel_cache_has(key any) any {
	cached := feel_cache_get(key)
	return any(cached != nil)
}

func feel_cache_delete(key any) any {
	feel_cache_lock.Lock()
	delete(feel_cache_store, feel_str(key))
	feel_cache_lock.Unlock()
	return any(true)
}

func feel_cache_clear() any {
	feel_cache_lock.Lock()
	feel_cache_store = map[string]feel_cache_entry{}
	feel_cache_lock.Unlock()
	return any(true)
}

func feel_cache_size() any {
	feel_cache_lock.Lock()
	defer feel_cache_lock.Unlock()
	now := feel_now_seconds()
	dead := []string{}
	for k, e := range feel_cache_store {
		if e.expiresAt > 0 && e.expiresAt <= now {
			dead = append(dead, k)
		}
	}
	for _, k := range dead {
		delete(feel_cache_store, k)
	}
	return any(float64(len(feel_cache_store)))
}

func feel_cache_get_or_compute(key, ttl, producer any) any {
	cached := feel_cache_get(key)
	if cached != nil {
		return cached
	}
	value := feel_call(producer, nil)
	feel_cache_set(key, value, ttl)
	return value
}

var feel_cache_mod = map[string]any{
	"set":            any(feel_cache_set),
	"get":            any(feel_cache_get),
	"get_or_compute": any(feel_cache_get_or_compute),
	"has":            any(feel_cache_has),
	"delete":         any(feel_cache_delete),
	"clear":          any(feel_cache_clear),
	"size":           any(feel_cache_size),
}

// ---------- Agent / tool ----------

func feel_make_tool(name, description string, params []string, fn any) any {
	pany := make([]any, len(params))
	for i, p := range params { pany[i] = any(p) }
	return any(map[string]any{
		"__type__":    any("tool"),
		"__fn__":      fn,
		"name":        any(name),
		"description": any(description),
		"parameters":  any(pany),
	})
}

func feel_make_agent(name string, system any, tools any, model any) any {
	toolList, _ := tools.([]any)
	if toolList == nil { toolList = []any{} }
	a := map[string]any{
		"__type__": any("agent"),
		"name":     any(name),
		"system":   system,
		"tools":    any(toolList),
		"model":    model,
	}
	a["chat"] = any(func(message any) any {
		return feel_agent_chat(a, message)
	})
	return any(a)
}

func feel_agent_chat(agent map[string]any, message any) any {
	system := feel_str(agent["system"])
	toolList, _ := agent["tools"].([]any)
	msgs := []any{
		map[string]any{"role": any("user"), "content": any(feel_str(message))},
	}
	p := feel_ai_provider()
	if p == "mock" {
		return any(fmt.Sprintf("[mock-agent] %d tools available. User said: '%s'", len(toolList), feel_str(message)))
	}
	// Claude tool-use loop
	toolSchemas := []any{}
	for _, t := range toolList {
		if tm, ok := t.(map[string]any); ok {
			params, _ := tm["parameters"].([]any)
			props := map[string]any{}
			required := []any{}
			for _, p := range params {
				ps := feel_str(p)
				props[ps] = map[string]any{"type": any("string"), "description": any("parameter " + ps)}
				required = append(required, any(ps))
			}
			toolSchemas = append(toolSchemas, map[string]any{
				"name":        tm["name"],
				"description": tm["description"],
				"input_schema": map[string]any{
					"type": any("object"), "properties": any(props), "required": any(required),
				},
			})
		}
	}
	for iter := 0; iter < 8; iter++ {
		payload := map[string]any{
			"model":      feel_ai_model(),
			"max_tokens": 1024,
			"messages":   msgs,
		}
		if system != "" { payload["system"] = system }
		if len(toolSchemas) > 0 { payload["tools"] = toolSchemas }
		data, _ := json.Marshal(payload)
		apiKey := os.Getenv("ANTHROPIC_API_KEY")
		req, _ := http.NewRequest("POST", "https://api.anthropic.com/v1/messages", bytes.NewReader(data))
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("x-api-key", apiKey)
		req.Header.Set("anthropic-version", "2023-06-01")
		client := &http.Client{Timeout: 60 * time.Second}
		resp, err := client.Do(req)
		if err != nil {
			panic(feel_throw{value: any("Claude API error: " + err.Error())})
		}
		bodyBytes, _ := io.ReadAll(resp.Body)
		resp.Body.Close()
		if resp.StatusCode != 200 {
			panic(feel_throw{value: any(fmt.Sprintf("Claude API %d: %s", resp.StatusCode, string(bodyBytes)))})
		}
		var body map[string]any
		json.Unmarshal(bodyBytes, &body)
		stop, _ := body["stop_reason"].(string)
		content, _ := body["content"].([]any)
		if stop != "tool_use" {
			parts := []string{}
			for _, b := range content {
				if blk, ok := b.(map[string]any); ok {
					if blk["type"] == "text" {
						parts = append(parts, feel_str(blk["text"]))
					}
				}
			}
			return any(strings.TrimSpace(strings.Join(parts, "\n")))
		}
		msgs = append(msgs, map[string]any{"role": any("assistant"), "content": any(content)})
		toolResults := []any{}
		for _, b := range content {
			blk, ok := b.(map[string]any)
			if !ok || blk["type"] != "tool_use" { continue }
			name := feel_str(blk["name"])
			input, _ := blk["input"].(map[string]any)
			useID := feel_str(blk["id"])
			var result any
			func() {
				defer func() {
					if rec := recover(); rec != nil {
						result = any(fmt.Sprintf("tool error: %v", rec))
					}
				}()
				for _, t := range toolList {
					tm, _ := t.(map[string]any)
					if tm == nil { continue }
					if feel_str(tm["name"]) == name {
						params, _ := tm["parameters"].([]any)
						args := []any{}
						for _, p := range params {
							args = append(args, input[feel_str(p)])
						}
						fn := tm["__fn__"]
						result = feel_call(fn, args)
						return
					}
				}
				result = any("unknown tool: " + name)
			}()
			toolResults = append(toolResults, map[string]any{
				"type": any("tool_result"), "tool_use_id": any(useID), "content": any(feel_str(result)),
			})
		}
		msgs = append(msgs, map[string]any{"role": any("user"), "content": any(toolResults)})
	}
	panic(feel_throw{value: any("agent chat: exceeded 8 tool-use iterations")})
}

// ---------- Static file serving ----------

type feel_static_mount struct {
	urlPrefix string
	fsDir     string
}

var feel_static_mounts []feel_static_mount

func feel_mount_static(urlPrefix, fsDir string) {
	if !strings.HasPrefix(urlPrefix, "/") {
		urlPrefix = "/" + urlPrefix
	}
	if urlPrefix != "/" {
		urlPrefix = strings.TrimRight(urlPrefix, "/")
	}
	feel_static_mounts = append(feel_static_mounts, feel_static_mount{urlPrefix: urlPrefix, fsDir: fsDir})
	// Longer prefixes win — sort desc by prefix length.
	sort.Slice(feel_static_mounts, func(i, j int) bool {
		return len(feel_static_mounts[i].urlPrefix) > len(feel_static_mounts[j].urlPrefix)
	})
}

func feel_static_match(path string) (mount feel_static_mount, sub string, ok bool) {
	for _, m := range feel_static_mounts {
		if m.urlPrefix == "/" || path == m.urlPrefix || strings.HasPrefix(path, m.urlPrefix+"/") {
			sub = strings.TrimPrefix(path, m.urlPrefix)
			sub = strings.TrimPrefix(sub, "/")
			return m, sub, true
		}
	}
	return feel_static_mount{}, "", false
}

func feel_serve_static(path string) (feel_response, bool) {
	mount, sub, ok := feel_static_match(path)
	if !ok {
		return feel_response{}, false
	}
	fsDirAbs, err := filepath.Abs(mount.fsDir)
	if err != nil {
		return feel_response{status: 500, body: map[string]any{"error": any("static: abs failed")}}, true
	}
	target := filepath.Join(fsDirAbs, sub)
	targetAbs, err := filepath.Abs(target)
	if err != nil || !(targetAbs == fsDirAbs || strings.HasPrefix(targetAbs, fsDirAbs+string(os.PathSeparator))) {
		return feel_response{status: 403, body: map[string]any{"error": any("forbidden")}}, true
	}
	info, err := os.Stat(targetAbs)
	if err != nil {
		return feel_response{}, false  // fall through to 404
	}
	if info.IsDir() {
		idx := filepath.Join(targetAbs, "index.html")
		if i2, err := os.Stat(idx); err == nil && !i2.IsDir() {
			targetAbs = idx
		} else {
			return feel_response{}, false
		}
	}
	data, err := os.ReadFile(targetAbs)
	if err != nil {
		return feel_response{status: 500, body: map[string]any{"error": any("static read: " + err.Error())}}, true
	}
	ct := mime.TypeByExtension(filepath.Ext(targetAbs))
	if ct == "" {
		ct = "application/octet-stream"
	}
	return feel_response{status: 200, body: any(data), contentType: ct}, true
}

// Silence unused-import warnings if a feature isn't exercised in user code.
var _ = url.Parse
var _ = bytes.NewReader
var _ = feel_mount_static

// ---------- WebSocket hooks (replaced by RUNTIME_GO_WS_BODY when used) ----------
var feel_ws_is_upgrade_fn = func(r *http.Request) bool { return false }
var feel_ws_handle_fn = func(w http.ResponseWriter, r *http.Request, path string, route *feel_route, captures map[string]string, t0 time.Time) {
}

func feel_is_ws_upgrade(r *http.Request) bool { return feel_ws_is_upgrade_fn(r) }
func feel_handle_ws_route(w http.ResponseWriter, r *http.Request, path string, route *feel_route, captures map[string]string, t0 time.Time) {
	feel_ws_handle_fn(w, r, path, route, captures, t0)
}
'''


RUNTIME_GO_DB_IMPORTS = (
    '"database/sql"\n'
    '\t_ "modernc.org/sqlite"\n'
    '\t_ "github.com/go-sql-driver/mysql"\n'
    '\t_ "github.com/lib/pq"\n'
)

RUNTIME_GO_DB_BODY = r'''
// ---------- db runtime (M4-D + MySQL/Postgres) ----------
// Pure-Go SQLite via modernc.org/sqlite (no CGO).
// MySQL via go-sql-driver/mysql, Postgres via lib/pq.

var feel_db_last_ids sync.Map     // *sql.DB -> int64
var feel_db_drivers sync.Map      // *sql.DB -> driverName ("sqlite"|"mysql"|"postgres")

func feel_db_unwrap(conn any) *sql.DB {
	if db, ok := conn.(*sql.DB); ok {
		return db
	}
	panic(feel_throw{value: any("db: not a connection")})
}

func feel_db_driver_of(db *sql.DB) string {
	if v, ok := feel_db_drivers.Load(db); ok {
		return v.(string)
	}
	return "sqlite"
}

// Translate ? placeholders to $1, $2, ... for Postgres. Skip inside quoted strings.
func feel_translate_params(driver, sqlText string) string {
	if driver != "postgres" {
		return sqlText
	}
	out := make([]byte, 0, len(sqlText))
	inSingle, inDouble := false, false
	idx := 1
	for i := 0; i < len(sqlText); i++ {
		c := sqlText[i]
		if c == '\'' && !inDouble {
			inSingle = !inSingle
		} else if c == '"' && !inSingle {
			inDouble = !inDouble
		}
		if c == '?' && !inSingle && !inDouble {
			out = append(out, []byte(fmt.Sprintf("$%d", idx))...)
			idx++
		} else {
			out = append(out, c)
		}
	}
	return string(out)
}

func feel_mysql_url_to_dsn(rawURL string) string {
	u, err := url.Parse(rawURL)
	if err != nil {
		panic(feel_throw{value: any("invalid mysql URL: " + err.Error())})
	}
	user := u.User.Username()
	pass, _ := u.User.Password()
	host := u.Host
	if host == "" { host = "localhost:3306" }
	dbname := strings.TrimPrefix(u.Path, "/")
	q := u.RawQuery
	extra := ""
	if q != "" { extra = "&" + q }
	return fmt.Sprintf("%s:%s@tcp(%s)/%s?parseTime=true&loc=Local%s", user, pass, host, dbname, extra)
}

func feel_db_params(raw any) []any {
	if raw == nil {
		return nil
	}
	if l, ok := raw.([]any); ok {
		out := make([]any, len(l))
		for i, v := range l {
			switch x := v.(type) {
			case float64:
				if x == float64(int64(x)) {
					out[i] = int64(x)
				} else {
					out[i] = x
				}
			default:
				out[i] = v
			}
		}
		return out
	}
	panic(feel_throw{value: any("db params must be a list")})
}

func feel_db_normalize(v any) any {
	switch x := v.(type) {
	case nil:
		return any(nil)
	case int64:
		return any(float64(x))
	case []byte:
		return any(string(x))
	}
	return v
}

func feel_db_connect(args ...any) any {
	path := feel_str(args[0])
	var driverName, dsn string

	switch {
	case strings.HasPrefix(path, "mysql://"):
		driverName = "mysql"
		dsn = feel_mysql_url_to_dsn(path)
	case strings.HasPrefix(path, "postgres://"), strings.HasPrefix(path, "postgresql://"):
		driverName = "postgres"
		dsn = path
	case strings.HasPrefix(path, "sqlite:///"):
		driverName = "sqlite"
		dsn = path[len("sqlite:///"):]
	default:
		driverName = "sqlite"
		dsn = path
	}

	db, err := sql.Open(driverName, dsn)
	if err != nil {
		panic(feel_throw{value: any("db.connect: " + err.Error())})
	}
	if err := db.Ping(); err != nil {
		panic(feel_throw{value: any("db.connect ping: " + err.Error())})
	}
	feel_db_drivers.Store(db, driverName)
	return any(db)
}

func feel_db_exec(args ...any) any {
	db := feel_db_unwrap(args[0])
	sqlText := feel_translate_params(feel_db_driver_of(db), feel_str(args[1]))
	var params []any
	if len(args) >= 3 {
		params = feel_db_params(args[2])
	}
	result, err := db.Exec(sqlText, params...)
	if err != nil {
		panic(feel_throw{value: any("db.exec: " + err.Error())})
	}
	if id, err := result.LastInsertId(); err == nil {
		feel_db_last_ids.Store(db, id)
	}
	affected, _ := result.RowsAffected()
	return any(float64(affected))
}

func feel_db_query(args ...any) any {
	db := feel_db_unwrap(args[0])
	sqlText := feel_translate_params(feel_db_driver_of(db), feel_str(args[1]))
	var params []any
	if len(args) >= 3 {
		params = feel_db_params(args[2])
	}
	rows, err := db.Query(sqlText, params...)
	if err != nil {
		panic(feel_throw{value: any("db.query: " + err.Error())})
	}
	defer rows.Close()
	cols, _ := rows.Columns()
	out := []any{}
	for rows.Next() {
		vals := make([]any, len(cols))
		ptrs := make([]any, len(cols))
		for i := range vals {
			ptrs[i] = &vals[i]
		}
		if err := rows.Scan(ptrs...); err != nil {
			panic(feel_throw{value: any("db.query scan: " + err.Error())})
		}
		row := map[string]any{}
		for i, c := range cols {
			row[c] = feel_db_normalize(vals[i])
		}
		out = append(out, any(row))
	}
	return any(out)
}

func feel_db_query_one(args ...any) any {
	list, _ := feel_db_query(args...).([]any)
	if len(list) > 0 {
		return list[0]
	}
	return any(nil)
}

func feel_db_close(conn any) any {
	if db, ok := conn.(*sql.DB); ok {
		db.Close()
		feel_db_last_ids.Delete(db)
	}
	return any(true)
}

func feel_db_last_id(conn any) any {
	db := feel_db_unwrap(conn)
	if v, ok := feel_db_last_ids.Load(db); ok {
		return any(float64(v.(int64)))
	}
	return any(float64(0))
}

func feel_db_begin(conn any) any {
	feel_db_exec(conn, any("BEGIN"))
	return any(true)
}

func feel_db_commit(conn any) any {
	feel_db_exec(conn, any("COMMIT"))
	return any(true)
}

func feel_db_rollback(conn any) any {
	feel_db_exec(conn, any("ROLLBACK"))
	return any(true)
}

func feel_db_transaction(conn, fn any) any {
	feel_db_begin(conn)
	var result any
	committed := false
	defer func() {
		if r := recover(); r != nil {
			if !committed {
				func() {
					defer func() { recover() }()
					feel_db_rollback(conn)
				}()
			}
			panic(r)
		}
	}()
	result = feel_call(fn, []any{conn})
	feel_db_commit(conn)
	committed = true
	return result
}

// ---------- ORM-lite query builder ----------

func feel_db_find(conn, table any) any {
	return any(map[string]any{
		"__qtype__": any("query"),
		"conn":      conn,
		"table":     any(feel_str(table)),
		"wheres":    any([]any{}),
		"order":     any(nil),
		"limit":     any(float64(-1)),
		"offset":    any(float64(-1)),
	})
}

func feel_is_query(v any) bool {
	m, ok := v.(map[string]any)
	if !ok { return false }
	qt, _ := m["__qtype__"].(string)
	return qt == "query"
}

func feel_q_add_where(q, col, op, val any) any {
	qm, _ := q.(map[string]any)
	wheres, _ := qm["wheres"].([]any)
	wheres = append(wheres, any([]any{col, op, val}))
	qm["wheres"] = any(wheres)
	return q
}

func feel_q_set_order(q, col, dir any) any {
	qm, _ := q.(map[string]any)
	d := strings.ToUpper(feel_str(dir))
	if d != "DESC" { d = "ASC" }
	qm["order"] = any([]any{any(feel_str(col)), any(d)})
	return q
}

func feel_db_where(args ...any) any {
	if len(args) >= 1 && feel_is_query(args[0]) {
		return feel_q_add_where(args[0], args[1], args[2], args[3])
	}
	if len(args) != 3 {
		panic(feel_throw{value: any("where: expected (col, op, val)")})
	}
	col, op, val := args[0], args[1], args[2]
	return any(func(q any) any { return feel_q_add_where(q, col, op, val) })
}

func feel_db_order_by(args ...any) any {
	if len(args) >= 1 && feel_is_query(args[0]) {
		col := args[1]
		dir := any("ASC")
		if len(args) >= 3 { dir = args[2] }
		return feel_q_set_order(args[0], col, dir)
	}
	col := args[0]
	dir := any("ASC")
	if len(args) >= 2 { dir = args[1] }
	return any(func(q any) any { return feel_q_set_order(q, col, dir) })
}

func feel_db_take(args ...any) any {
	if len(args) >= 1 && feel_is_query(args[0]) {
		qm, _ := args[0].(map[string]any)
		qm["limit"] = any(float64(int(feel_num(args[1]))))
		return args[0]
	}
	n := int(feel_num(args[0]))
	return any(func(q any) any {
		qm, _ := q.(map[string]any)
		qm["limit"] = any(float64(n))
		return q
	})
}

func feel_db_offset(args ...any) any {
	if len(args) >= 1 && feel_is_query(args[0]) {
		qm, _ := args[0].(map[string]any)
		qm["offset"] = any(float64(int(feel_num(args[1]))))
		return args[0]
	}
	n := int(feel_num(args[0]))
	return any(func(q any) any {
		qm, _ := q.(map[string]any)
		qm["offset"] = any(float64(n))
		return q
	})
}

func feel_db_build_sql(qm map[string]any) (string, []any) {
	table := feel_str(qm["table"])
	sql := "SELECT * FROM " + table
	var params []any
	wheres, _ := qm["wheres"].([]any)
	if len(wheres) > 0 {
		parts := []string{}
		for _, w := range wheres {
			t, _ := w.([]any)
			col := feel_str(t[0])
			op := strings.ToUpper(feel_str(t[1]))
			val := t[2]
			if val == nil && op == "IS" {
				parts = append(parts, col+" IS NULL")
			} else if val == nil && (op == "IS NOT" || op == "IS_NOT") {
				parts = append(parts, col+" IS NOT NULL")
			} else {
				parts = append(parts, col+" "+op+" ?")
				params = append(params, val)
			}
		}
		sql += " WHERE " + strings.Join(parts, " AND ")
	}
	if order, ok := qm["order"].([]any); ok && order != nil && len(order) >= 2 {
		sql += " ORDER BY " + feel_str(order[0]) + " " + feel_str(order[1])
	}
	if limit, ok := qm["limit"].(float64); ok && limit >= 0 {
		sql += fmt.Sprintf(" LIMIT %d", int(limit))
	}
	if off, ok := qm["offset"].(float64); ok && off >= 0 {
		sql += fmt.Sprintf(" OFFSET %d", int(off))
	}
	return sql, params
}

func feel_db_all(q any) any {
	if !feel_is_query(q) { panic(feel_throw{value: any("all: expected a query")}) }
	qm, _ := q.(map[string]any)
	sql, params := feel_db_build_sql(qm)
	return feel_db_query(qm["conn"], any(sql), any(params))
}

func feel_db_first(q any) any {
	if !feel_is_query(q) { panic(feel_throw{value: any("first: expected a query")}) }
	qm, _ := q.(map[string]any)
	// Clone shallow: set limit=1 on a copy
	clone := map[string]any{}
	for k, v := range qm { clone[k] = v }
	clone["limit"] = any(float64(1))
	sql, params := feel_db_build_sql(clone)
	rows, _ := feel_db_query(qm["conn"], any(sql), any(params)).([]any)
	if len(rows) > 0 { return rows[0] }
	return any(nil)
}

func feel_db_count(q any) any {
	if !feel_is_query(q) { panic(feel_throw{value: any("count: expected a query")}) }
	qm, _ := q.(map[string]any)
	sql := "SELECT COUNT(*) AS n FROM " + feel_str(qm["table"])
	var params []any
	wheres, _ := qm["wheres"].([]any)
	if len(wheres) > 0 {
		parts := []string{}
		for _, w := range wheres {
			t, _ := w.([]any)
			col := feel_str(t[0]); op := strings.ToUpper(feel_str(t[1])); val := t[2]
			if val == nil && op == "IS" {
				parts = append(parts, col+" IS NULL")
			} else if val == nil && (op == "IS NOT" || op == "IS_NOT") {
				parts = append(parts, col+" IS NOT NULL")
			} else {
				parts = append(parts, col+" "+op+" ?")
				params = append(params, val)
			}
		}
		sql += " WHERE " + strings.Join(parts, " AND ")
	}
	row := feel_db_query_one(qm["conn"], any(sql), any(params))
	if row == nil { return any(float64(0)) }
	r, _ := row.(map[string]any)
	return r["n"]
}

func feel_db_paginate(args ...any) any {
	var qm map[string]any
	var page, perPage int
	if len(args) >= 3 && feel_is_query(args[0]) {
		qm, _ = args[0].(map[string]any)
		page = int(feel_num(args[1]))
		perPage = int(feel_num(args[2]))
		return feel_db_paginate_impl(qm, page, perPage)
	}
	if len(args) == 2 {
		page = int(feel_num(args[0]))
		perPage = int(feel_num(args[1]))
		return any(func(q any) any {
			qm2, _ := q.(map[string]any)
			return feel_db_paginate_impl(qm2, page, perPage)
		})
	}
	panic(feel_throw{value: any("paginate: expected (page, per_page) or (query, page, per_page)")})
}

func feel_db_paginate_impl(qm map[string]any, page, perPage int) any {
	if page < 1 { page = 1 }
	if perPage < 1 { perPage = 1 }
	// Count
	clone := map[string]any{}
	for k, v := range qm { clone[k] = v }
	clone["limit"] = any(float64(-1))
	clone["offset"] = any(float64(-1))
	total := int(feel_num(feel_db_count(any(clone))))
	// Page
	clone["limit"] = any(float64(perPage))
	clone["offset"] = any(float64((page - 1) * perPage))
	sql, params := feel_db_build_sql(clone)
	items := feel_db_query(qm["conn"], any(sql), any(params))
	totalPages := 0
	if perPage > 0 { totalPages = (total + perPage - 1) / perPage }
	return any(map[string]any{
		"items":       items,
		"total":       any(float64(total)),
		"page":        any(float64(page)),
		"per_page":    any(float64(perPage)),
		"total_pages": any(float64(totalPages)),
	})
}

func feel_db_touch(args ...any) any {
	conn := args[0]; table := feel_str(args[1]); id := args[2]
	col := "updated_at"
	if len(args) >= 4 { col = feel_str(args[3]) }
	return feel_db_exec(conn, any("UPDATE "+table+" SET "+col+" = CURRENT_TIMESTAMP WHERE id = ?"), any([]any{id}))
}

func feel_db_soft_delete(args ...any) any {
	conn := args[0]; table := feel_str(args[1]); id := args[2]
	col := "deleted_at"
	if len(args) >= 4 { col = feel_str(args[3]) }
	return feel_db_exec(conn, any("UPDATE "+table+" SET "+col+" = CURRENT_TIMESTAMP WHERE id = ?"), any([]any{id}))
}

func feel_db_restore(args ...any) any {
	conn := args[0]; table := feel_str(args[1]); id := args[2]
	col := "deleted_at"
	if len(args) >= 4 { col = feel_str(args[3]) }
	return feel_db_exec(conn, any("UPDATE "+table+" SET "+col+" = NULL WHERE id = ?"), any([]any{id}))
}

var feel_db_mod = map[string]any{
	"connect":     any(feel_db_connect),
	"exec":        any(feel_db_exec),
	"query":       any(feel_db_query),
	"query_one":   any(feel_db_query_one),
	"close":       any(feel_db_close),
	"last_id":     any(feel_db_last_id),
	"begin":       any(feel_db_begin),
	"commit":      any(feel_db_commit),
	"rollback":    any(feel_db_rollback),
	"transaction": any(feel_db_transaction),
	"find":        any(feel_db_find),
	"where":       any(feel_db_where),
	"order_by":    any(feel_db_order_by),
	"take":        any(feel_db_take),
	"offset":      any(feel_db_offset),
	"all":         any(feel_db_all),
	"first":       any(feel_db_first),
	"count":       any(feel_db_count),
	"paginate":    any(feel_db_paginate),
	"touch":       any(feel_db_touch),
	"soft_delete": any(feel_db_soft_delete),
	"restore":     any(feel_db_restore),
}

// ---------- Queue (SQLite-backed jobs) — defined here so it sits in the conditional db block ----------

func feel_queue_connect(path any) any {
	conn := feel_db_connect(path)
	feel_db_exec(conn, any(`CREATE TABLE IF NOT EXISTS jobs (
		id INTEGER PRIMARY KEY AUTOINCREMENT,
		queue_name TEXT NOT NULL,
		payload TEXT NOT NULL,
		status TEXT NOT NULL DEFAULT 'pending',
		attempts INTEGER NOT NULL DEFAULT 0,
		last_error TEXT,
		leased_at TEXT,
		created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
	)`))
	feel_db_exec(conn, any("CREATE INDEX IF NOT EXISTS idx_jobs_queue_status ON jobs(queue_name, status)"))
	return conn
}

func feel_queue_enqueue(qconn, name, payload any) any {
	data, _ := json.Marshal(feel_to_jsonable(payload))
	feel_db_exec(qconn, any("INSERT INTO jobs (queue_name, payload) VALUES (?, ?)"),
		any([]any{any(feel_str(name)), any(string(data))}))
	return feel_db_last_id(qconn)
}

func feel_queue_pop(qconn, name any) any {
	feel_db_begin(qconn)
	defer func() {
		if r := recover(); r != nil {
			func() { defer func() { recover() }(); feel_db_rollback(qconn) }()
			panic(r)
		}
	}()
	row := feel_db_query_one(qconn,
		any("SELECT id, payload, attempts FROM jobs WHERE queue_name = ? AND status = 'pending' ORDER BY id ASC LIMIT 1"),
		any([]any{any(feel_str(name))}))
	if row == nil {
		feel_db_commit(qconn)
		return any(nil)
	}
	r, _ := row.(map[string]any)
	id := r["id"]
	feel_db_exec(qconn,
		any("UPDATE jobs SET status = 'running', leased_at = CURRENT_TIMESTAMP, attempts = attempts + 1 WHERE id = ?"),
		any([]any{id}))
	feel_db_commit(qconn)
	var decoded any
	if raw, ok := r["payload"].(string); ok {
		_ = json.Unmarshal([]byte(raw), &decoded)
		decoded = feel_from_jsonable(decoded)
	}
	return any(map[string]any{
		"id":       id,
		"payload":  decoded,
		"attempts": any(feel_num(r["attempts"]) + 1),
	})
}

func feel_queue_complete(qconn, jobID any) any {
	feel_db_exec(qconn, any("DELETE FROM jobs WHERE id = ?"), any([]any{any(int64(feel_num(jobID)))}))
	return any(true)
}

func feel_queue_fail(args ...any) any {
	if len(args) < 3 {
		panic(feel_throw{value: any("queue.fail: need (qconn, job_id, error [, max_attempts])")})
	}
	qconn := args[0]
	jobID := int64(feel_num(args[1]))
	errMsg := feel_str(args[2])
	maxAttempts := 3
	if len(args) >= 4 {
		maxAttempts = int(feel_num(args[3]))
	}
	row := feel_db_query_one(qconn, any("SELECT attempts FROM jobs WHERE id = ?"), any([]any{any(jobID)}))
	if row == nil {
		return any(false)
	}
	r, _ := row.(map[string]any)
	if int(feel_num(r["attempts"])) >= maxAttempts {
		feel_db_exec(qconn, any("UPDATE jobs SET status = 'failed', last_error = ? WHERE id = ?"),
			any([]any{any(errMsg), any(jobID)}))
	} else {
		feel_db_exec(qconn, any("UPDATE jobs SET status = 'pending', last_error = ?, leased_at = NULL WHERE id = ?"),
			any([]any{any(errMsg), any(jobID)}))
	}
	return any(true)
}

func feel_queue_pending(qconn, name any) any {
	row := feel_db_query_one(qconn,
		any("SELECT COUNT(*) AS n FROM jobs WHERE queue_name = ? AND status = 'pending'"),
		any([]any{any(feel_str(name))}))
	if row == nil { return any(float64(0)) }
	r, _ := row.(map[string]any)
	return r["n"]
}

func feel_queue_process_once(qconn, name, handler any) any {
	job := feel_queue_pop(qconn, name)
	if job == nil {
		return any(false)
	}
	jm, _ := job.(map[string]any)
	jobID := jm["id"]
	payload := jm["payload"]
	func() {
		defer func() {
			if r := recover(); r != nil {
				msg := fmt.Sprintf("%v", r)
				if ft, ok := r.(feel_throw); ok { msg = feel_str(ft.value) }
				feel_queue_fail(qconn, jobID, any(msg))
			}
		}()
		feel_call(handler, []any{payload})
		feel_queue_complete(qconn, jobID)
	}()
	return any(true)
}

func feel_queue_work(args ...any) any {
	if len(args) < 3 {
		panic(feel_throw{value: any("queue.work: need (qconn, name, handler [, poll_ms])")})
	}
	qconn := args[0]
	name := args[1]
	handler := args[2]
	pollMs := 500
	if len(args) >= 4 {
		pollMs = int(feel_num(args[3]))
	}
	fmt.Fprintf(os.Stderr, "[queue] worker started: queue=%q, poll=%dms\n", feel_str(name), pollMs)
	for {
		processed := feel_queue_process_once(qconn, name, handler)
		if processed != any(true) {
			time.Sleep(time.Duration(pollMs) * time.Millisecond)
		}
	}
}

var feel_queue_mod = map[string]any{
	"connect":      any(feel_queue_connect),
	"enqueue":      any(feel_queue_enqueue),
	"pop":          any(feel_queue_pop),
	"complete":     any(feel_queue_complete),
	"fail":         any(feel_queue_fail),
	"pending":      any(feel_queue_pending),
	"process_once": any(feel_queue_process_once),
	"work":         any(feel_queue_work),
}
'''  # end RUNTIME_GO_DB_BODY


# Always-included HTTP client (outbound). Appended to the main runtime.
RUNTIME_GO_HTTP_CLIENT = r'''
// ---------- HTTP client (outbound) ----------

var feel_http_client = &http.Client{Timeout: 30 * time.Second}

func feel_http_encode_body(body any, headers map[string]string) ([]byte, map[string]string) {
	if body == nil {
		return nil, headers
	}
	switch v := body.(type) {
	case string:
		return []byte(v), headers
	case []byte:
		return v, headers
	case map[string]any, []any:
		data, _ := json.Marshal(v)
		hasCT := false
		for k := range headers {
			if strings.EqualFold(k, "Content-Type") {
				hasCT = true
				break
			}
		}
		if !hasCT {
			headers["Content-Type"] = "application/json"
		}
		return data, headers
	default:
		panic(feel_throw{value: any("http: body must be string, map, or list")})
	}
}

func feel_http_normalize_headers(h any) map[string]string {
	out := map[string]string{}
	if h == nil {
		return out
	}
	m, ok := h.(map[string]any)
	if !ok {
		panic(feel_throw{value: any("http: headers must be a map")})
	}
	for k, v := range m {
		out[k] = feel_str(v)
	}
	return out
}

func feel_http_do(method string, url string, body any, headers any, timeout any) any {
	hdrs := feel_http_normalize_headers(headers)
	data, hdrs := feel_http_encode_body(body, hdrs)

	var bodyReader io.Reader
	if data != nil {
		bodyReader = bytes.NewReader(data)
	}
	req, err := http.NewRequest(strings.ToUpper(method), url, bodyReader)
	if err != nil {
		panic(feel_throw{value: any("http." + strings.ToLower(method) + ": " + err.Error())})
	}
	for k, v := range hdrs {
		req.Header.Set(k, v)
	}

	client := feel_http_client
	if timeout != nil {
		t := feel_num(timeout)
		client = &http.Client{Timeout: time.Duration(t * float64(time.Second))}
	}
	resp, err := client.Do(req)
	if err != nil {
		panic(feel_throw{value: any("http." + strings.ToLower(method) + ": " + err.Error())})
	}
	defer resp.Body.Close()

	raw, _ := io.ReadAll(resp.Body)
	respHeaders := map[string]any{}
	for k, vs := range resp.Header {
		if len(vs) > 0 {
			respHeaders[strings.ToLower(k)] = any(vs[0])
		}
	}

	ct, _ := respHeaders["content-type"].(string)
	var bodyVal any = string(raw)
	if strings.Contains(strings.ToLower(ct), "application/json") && len(raw) > 0 {
		var parsed any
		if err := json.Unmarshal(raw, &parsed); err == nil {
			bodyVal = parsed
		}
	}

	return any(map[string]any{
		"status":  any(int64(resp.StatusCode)),
		"body":    any(bodyVal),
		"headers": any(respHeaders),
		"ok":      any(resp.StatusCode >= 200 && resp.StatusCode < 300),
	})
}

func feel_http_get(args ...any) any {
	if len(args) == 0 {
		panic(feel_throw{value: any("http.get: url required")})
	}
	url := feel_str(args[0])
	var headers any
	var timeout any
	if len(args) >= 2 {
		headers = args[1]
	}
	if len(args) >= 3 {
		timeout = args[2]
	}
	return feel_http_do("GET", url, nil, headers, timeout)
}

func feel_http_post(args ...any) any {
	if len(args) == 0 {
		panic(feel_throw{value: any("http.post: url required")})
	}
	url := feel_str(args[0])
	var body, headers, timeout any
	if len(args) >= 2 {
		body = args[1]
	}
	if len(args) >= 3 {
		headers = args[2]
	}
	if len(args) >= 4 {
		timeout = args[3]
	}
	return feel_http_do("POST", url, body, headers, timeout)
}

func feel_http_put(args ...any) any {
	if len(args) == 0 {
		panic(feel_throw{value: any("http.put: url required")})
	}
	url := feel_str(args[0])
	var body, headers, timeout any
	if len(args) >= 2 {
		body = args[1]
	}
	if len(args) >= 3 {
		headers = args[2]
	}
	if len(args) >= 4 {
		timeout = args[3]
	}
	return feel_http_do("PUT", url, body, headers, timeout)
}

func feel_http_delete_(args ...any) any {
	if len(args) == 0 {
		panic(feel_throw{value: any("http.delete: url required")})
	}
	url := feel_str(args[0])
	var headers, timeout any
	if len(args) >= 2 {
		headers = args[1]
	}
	if len(args) >= 3 {
		timeout = args[2]
	}
	return feel_http_do("DELETE", url, nil, headers, timeout)
}

func feel_http_request(method, url, opts any) any {
	m := feel_str(method)
	u := feel_str(url)
	var body, headers, timeout any
	if opts != nil {
		om, ok := opts.(map[string]any)
		if !ok {
			panic(feel_throw{value: any("http.request: opts must be a map")})
		}
		body = om["body"]
		headers = om["headers"]
		timeout = om["timeout"]
	}
	return feel_http_do(m, u, body, headers, timeout)
}

func feel_http_get_json(args ...any) any {
	if len(args) == 0 {
		panic(feel_throw{value: any("http.get_json: url required")})
	}
	url := feel_str(args[0])
	hdrs := map[string]any{}
	if len(args) >= 2 && args[1] != nil {
		if m, ok := args[1].(map[string]any); ok {
			for k, v := range m {
				hdrs[k] = v
			}
		}
	}
	hasAccept := false
	for k := range hdrs {
		if strings.EqualFold(k, "Accept") {
			hasAccept = true
			break
		}
	}
	if !hasAccept {
		hdrs["Accept"] = any("application/json")
	}
	var timeout any
	if len(args) >= 3 {
		timeout = args[2]
	}
	resp := feel_http_do("GET", url, nil, any(hdrs), timeout).(map[string]any)
	if !resp["ok"].(bool) {
		panic(feel_throw{value: any(fmt.Sprintf("http.get_json: status %v", resp["status"]))})
	}
	body := resp["body"]
	switch body.(type) {
	case map[string]any, []any:
		return body
	}
	// Try parse as JSON if server didn't set content-type
	if s, ok := body.(string); ok {
		var parsed any
		if err := json.Unmarshal([]byte(s), &parsed); err == nil {
			return any(parsed)
		}
	}
	panic(feel_throw{value: any("http.get_json: response is not valid JSON")})
}

var feel_http_mod = map[string]any{
	"get":      any(feel_http_get),
	"post":     any(feel_http_post),
	"put":      any(feel_http_put),
	"delete":   any(feel_http_delete_),
	"request":  any(feel_http_request),
	"get_json": any(feel_http_get_json),
}

// ---------- env module (process env + .env file) ----------

var (
	feel_env_lock    sync.Mutex
	feel_env_loaded  bool
	feel_env_dotenv  = map[string]string{}
)

func feel_env_parse_dotenv(text string) map[string]string {
	out := map[string]string{}
	for _, raw := range strings.Split(text, "\n") {
		line := strings.TrimSpace(raw)
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		idx := strings.Index(line, "=")
		if idx < 0 {
			continue
		}
		key := strings.TrimSpace(line[:idx])
		value := strings.TrimSpace(line[idx+1:])
		// Strip surrounding quotes
		if len(value) >= 2 {
			f, l := value[0], value[len(value)-1]
			if (f == '"' || f == '\'') && f == l {
				value = value[1 : len(value)-1]
			}
		}
		out[key] = value
	}
	return out
}

func feel_env_autoload() {
	feel_env_lock.Lock()
	defer feel_env_lock.Unlock()
	if feel_env_loaded {
		return
	}
	feel_env_loaded = true
	cwd, err := os.Getwd()
	if err != nil {
		return
	}
	path := filepath.Join(cwd, ".env")
	if data, err := os.ReadFile(path); err == nil {
		for k, v := range feel_env_parse_dotenv(string(data)) {
			feel_env_dotenv[k] = v
		}
	}
}

func feel_env_get(args ...any) any {
	if len(args) == 0 {
		return any(nil)
	}
	feel_env_autoload()
	name := feel_str(args[0])
	if v, ok := os.LookupEnv(name); ok {
		return any(v)
	}
	feel_env_lock.Lock()
	v, ok := feel_env_dotenv[name]
	feel_env_lock.Unlock()
	if ok {
		return any(v)
	}
	if len(args) >= 2 {
		return args[1]
	}
	return any(nil)
}

func feel_env_has(name any) any {
	feel_env_autoload()
	n := feel_str(name)
	if _, ok := os.LookupEnv(n); ok {
		return any(true)
	}
	feel_env_lock.Lock()
	_, ok := feel_env_dotenv[n]
	feel_env_lock.Unlock()
	return any(ok)
}

func feel_env_set(name, value any) any {
	os.Setenv(feel_str(name), feel_str(value))
	return any(true)
}

func feel_env_load(args ...any) any {
	path := ".env"
	if len(args) >= 1 && args[0] != nil {
		path = feel_str(args[0])
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return any(false)
	}
	parsed := feel_env_parse_dotenv(string(data))
	feel_env_lock.Lock()
	defer feel_env_lock.Unlock()
	for k, v := range parsed {
		feel_env_dotenv[k] = v
	}
	feel_env_loaded = true
	return any(true)
}

func feel_env_all() any {
	feel_env_autoload()
	out := map[string]any{}
	feel_env_lock.Lock()
	for k, v := range feel_env_dotenv {
		out[k] = any(v)
	}
	feel_env_lock.Unlock()
	for _, e := range os.Environ() {
		if idx := strings.Index(e, "="); idx > 0 {
			out[e[:idx]] = any(e[idx+1:])
		}
	}
	return any(out)
}

var feel_env_mod = map[string]any{
	"get":  any(feel_env_get),
	"has":  any(feel_env_has),
	"set":  any(feel_env_set),
	"load": any(feel_env_load),
	"all":  any(feel_env_all),
}
'''


GO_MOD_TEMPLATE = '''module feel-app

go 1.21
'''

GO_MOD_TEMPLATE_WITH_SQLITE = '''module feel-app

go 1.21

require (
	modernc.org/sqlite v1.34.4
	github.com/go-sql-driver/mysql v1.8.1
	github.com/lib/pq v1.10.9
)
'''

GO_MOD_WS_REQUIRE = 'require github.com/gorilla/websocket v1.5.3\n'


# Body appended when uses_ws=True. Overrides the stub funcs from main runtime.
RUNTIME_GO_WS_BODY = r'''

// ---------- WebSocket (RFC 6455, via gorilla/websocket) ----------

var feel_ws_upgrader = websocket.Upgrader{
	CheckOrigin: func(r *http.Request) bool { return true },
}

// Override the stubs above. Go allows package-level re-declaration via name
// reuse if we wrap them — instead we just shadow at call site via these.
func feel_is_ws_upgrade_real(r *http.Request) bool {
	conn := strings.ToLower(r.Header.Get("Connection"))
	up := strings.ToLower(r.Header.Get("Upgrade"))
	return strings.Contains(conn, "upgrade") && up == "websocket"
}

func feel_handle_ws_route_real(w http.ResponseWriter, r *http.Request, path string, route *feel_route, captures map[string]string, t0 time.Time) {
	conn, err := feel_ws_upgrader.Upgrade(w, r, nil)
	if err != nil {
		fmt.Fprintf(os.Stderr, "WS     %-40s -> upgrade failed: %v\n", path, err)
		return
	}
	defer conn.Close()

	wsID := fmt.Sprintf("ws-%d", time.Now().UnixNano())
	wsMap := map[string]any{
		"id":   any(wsID),
		"path": any(path),
		"send": any(func(msg any) any {
			var data []byte
			mt := websocket.TextMessage
			switch v := msg.(type) {
			case string:
				data = []byte(v)
			case []byte:
				data = v
				mt = websocket.BinaryMessage
			default:
				data = []byte(feel_str(v))
			}
			if err := conn.WriteMessage(mt, data); err != nil {
				return any(false)
			}
			return any(true)
		}),
		"receive": any(func() any {
			mt, data, err := conn.ReadMessage()
			if err != nil {
				return any(nil)
			}
			if mt == websocket.BinaryMessage {
				return any(string(data))  // return as string for simplicity
			}
			return any(string(data))
		}),
		"close": any(func() any {
			conn.WriteMessage(websocket.CloseMessage, websocket.FormatCloseMessage(websocket.CloseNormalClosure, ""))
			return any(true)
		}),
	}

	// Build scope (same shape as HTTP route, plus ws).
	query := map[string]any{}
	for k, vs := range r.URL.Query() {
		if len(vs) == 1 {
			query[k] = any(vs[0])
		} else {
			items := make([]any, len(vs))
			for i, v := range vs { items[i] = any(v) }
			query[k] = any(items)
		}
	}
	headers := map[string]any{}
	for k, vs := range r.Header {
		if len(vs) > 0 { headers[strings.ToLower(k)] = any(vs[0]) }
	}
	request := map[string]any{
		"method":  any("WS"),
		"path":    any(path),
		"query":   any(query),
		"headers": any(headers),
		"body":    any(nil),
		"files":   any(map[string]any{}),
		"form":    any(map[string]any{}),
	}
	scope := map[string]any{
		"request": any(request),
		"body":    any(nil),
		"query":   any(query),
		"ws":      any(wsMap),
	}
	for k, v := range captures {
		scope[k] = any(v)
	}

	func() {
		defer func() {
			if rec := recover(); rec != nil {
				fmt.Fprintf(os.Stderr, "WS     %-40s -> handler panic: %v\n", path, rec)
			}
		}()
		_ = route.handler(scope)
	}()

	fmt.Fprintf(os.Stderr, "WS     %-40s -> 101  (handler returned, conn closed)\n", path)
}

// Replace the no-op stubs with the real implementations via init.
func init() {
	feel_ws_is_upgrade_fn = feel_is_ws_upgrade_real
	feel_ws_handle_fn = feel_handle_ws_route_real
}

'''


def compile_to_go(source, filename='<input>', search_paths=None):
    """Return (Go source, uses_db) for the given Feel source.

    search_paths: directories to look for imported modules. Defaults to the
    directory of `filename` (when filename is a path) plus the cwd.
    """
    import os
    if search_paths is None:
        search_paths = []
        if filename and filename != '<input>' and os.path.exists(filename):
            search_paths.append(os.path.dirname(os.path.abspath(filename)))
        search_paths.append(os.getcwd())

    tree = parse(source, filename=filename)
    emitter = GoEmitter(search_paths=search_paths)
    emitter.depth = 1  # inside main()
    body = emitter.emit_program(tree)

    runtime = RUNTIME_GO
    if emitter.uses_db:
        # Inject sqlite imports into the import block.
        runtime = runtime.replace(
            '"time"\n)',
            '"time"\n\t' + RUNTIME_GO_DB_IMPORTS + ')',
            1,
        )
        # Append db helpers + module map BEFORE the final closing ''' (raw string).
        # We split on the trailing var _ = ... lines and re-attach.
        marker = "var _ = bytes.NewReader"
        if marker in runtime:
            head, tail = runtime.rsplit(marker, 1)
            runtime = head + marker + tail + RUNTIME_GO_DB_BODY
        else:
            runtime = runtime + RUNTIME_GO_DB_BODY

    # Always-included HTTP client runtime (no extra Go imports needed).
    runtime = runtime + RUNTIME_GO_HTTP_CLIENT

    if emitter.uses_ws:
        # Inject gorilla/websocket import.
        runtime = runtime.replace(
            '"net/http"',
            '"net/http"\n\t"github.com/gorilla/websocket"',
            1,
        )
        runtime = runtime + RUNTIME_GO_WS_BODY

    go_src = runtime + '\nfunc main() {\n' + body + '\n}\n'
    return go_src, emitter.uses_db, emitter.uses_ws


def build_feel(feel_path, out_path=None, keep_go=False):
    """Compile a Feel file → Go → native binary via `go build`.

    If the program uses `db.*`, a go.mod is generated requiring
    modernc.org/sqlite (pure-Go driver) and `go mod tidy` is run before
    `go build`. First build with sqlite downloads ~5MB to the module cache;
    subsequent builds are fast.

    Returns (ok: bool, message: str).
    """
    import os
    import subprocess
    import tempfile

    with open(feel_path, encoding='utf-8') as f:
        src = f.read()
    go_src, uses_db, uses_ws = compile_to_go(src, filename=feel_path)

    # Output paths
    base = os.path.splitext(os.path.basename(feel_path))[0]
    if out_path is None:
        out_path = base + ('.exe' if os.name == 'nt' else '')

    tmpdir = tempfile.mkdtemp(prefix='feel_build_')
    go_file = os.path.join(tmpdir, 'main.go')
    with open(go_file, 'w', encoding='utf-8') as f:
        f.write(go_src)

    # Write a go.mod (module-aware build is required for external deps)
    gomod_path = os.path.join(tmpdir, 'go.mod')
    with open(gomod_path, 'w', encoding='utf-8') as f:
        content = GO_MOD_TEMPLATE_WITH_SQLITE if uses_db else GO_MOD_TEMPLATE
        if uses_ws:
            content = content + GO_MOD_WS_REQUIRE
        f.write(content)

    abs_out = os.path.abspath(out_path)
    try:
        if uses_db or uses_ws:
            tidy = subprocess.run(
                ['go', 'mod', 'tidy'],
                cwd=tmpdir,
                capture_output=True,
                text=True,
                timeout=180,
            )
            if tidy.returncode != 0:
                return False, f"go mod tidy failed:\n{tidy.stderr}\n\n-- workdir: {tmpdir} --"
        result = subprocess.run(
            ['go', 'build', '-o', abs_out, '.'],
            cwd=tmpdir,
            capture_output=True,
            text=True,
            timeout=300,
        )
    except FileNotFoundError:
        return False, "go toolchain not found in PATH (install Go from https://go.dev)"
    except subprocess.TimeoutExpired:
        return False, "go build timed out (>5min)"

    if keep_go:
        kept = os.path.splitext(out_path)[0] + '.go'
        with open(kept, 'w', encoding='utf-8') as f:
            f.write(go_src)

    if result.returncode != 0:
        return False, f"go build failed:\n{result.stderr}\n\n-- generated Go saved at {go_file} --"

    if not keep_go:
        try:
            os.remove(go_file)
            os.remove(gomod_path)
            sumf = os.path.join(tmpdir, 'go.sum')
            if os.path.exists(sumf):
                os.remove(sumf)
            os.rmdir(tmpdir)
        except OSError:
            pass

    return True, f"built {out_path}"
