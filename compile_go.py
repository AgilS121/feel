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
)
from errors import FeelError


INDENT = '\t'  # Go convention: tabs


class GoEmitter:
    """Walks Feel AST and emits Go source."""

    def __init__(self):
        self.depth = 0
        self.var_counter = 0
        self.scopes = [set()]   # stack of sets of bound names

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

    def emit_program(self, prog):
        body = []
        for stmt in prog.stmts:
            if stmt is None:
                continue
            body.extend(self._emit_stmt(stmt))
        return '\n'.join(body)

    # ---------- Statements ----------

    def _emit_stmt(self, n):
        """Returns list of Go lines (indented from depth=0; main() adds outer)."""
        if isinstance(n, RecordDef):
            # Records are dynamic maps in Go — no constructor needed, RecordLiteral
            # emits the literal directly. Just leave a marker comment.
            return [f'{self._ind()}// record {n.name} {{ {", ".join(f"{k}: {v}" for k, v in n.fields.items())} }}']
        if isinstance(n, ThrowStmt):
            return [f'{self._ind()}panic(feel_throw{{value: {self._emit_expr(n.expr)}}})']
        if isinstance(n, TryStmt):
            # Statement-level try: discard result
            return [f'{self._ind()}_ = {self._emit_expr(n)}']
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
        if isinstance(n, WhenStmt):
            # Expression form: ternary via IIFE
            cond = self._emit_expr(n.cond)
            then_e = self._emit_expr(n.then)
            else_e = self._emit_expr(n.otherwise) if n.otherwise else 'any(nil)'
            return f'func() any {{ if feel_truthy({cond}) {{ return {then_e} }}; return {else_e} }}()'
        if isinstance(n, Block):
            # Block expression: IIFE
            lines = []
            for s in n.stmts[:-1]:
                lines.append('  ' + ' '.join(self._inline_stmt(s)))
            last = n.stmts[-1] if n.stmts else None
            if last is None:
                last_expr = 'any(nil)'
            elif isinstance(last, (LetStmt, DefineStmt, ShowStmt, WhenStmt, RepeatStmt, ForStmt)):
                # If last is a stmt, run it and return nil
                lines.append('  ' + ' '.join(self._inline_stmt(last)))
                last_expr = 'any(nil)'
            else:
                last_expr = self._emit_expr(last)
            body = '; '.join(lines) + ('; ' if lines else '') + f'return {last_expr}'
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
        """Like _emit_stmt but for use inside an IIFE body (single statement, no leading indent)."""
        if isinstance(n, LetStmt):
            return [f'{_safe_name(n.name)} := any({self._emit_expr(n.value)})', f'_ = {_safe_name(n.name)}']
        if isinstance(n, DefineStmt):
            params = ', '.join(f'{_safe_name(p)} any' for p in n.params)
            body = self._emit_expr(n.body)
            return [f'{_safe_name(n.name)} := func({params}) any {{ return {body} }}', f'_ = {_safe_name(n.name)}']
        if isinstance(n, ShowStmt):
            return [f'feel_show({self._emit_expr(n.expr)})']
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
        if isinstance(v, (int, float)):
            return f'any(float64({v}))'
        if isinstance(v, str):
            return self._string_literal(v)
        return f'any({v!r})'

    def _string_literal(self, s):
        """Emit a Go expression that builds the string, handling {name} interpolation."""
        # Split into literal parts + interpolation parts
        import re as _re
        parts = []
        last = 0
        has_interp = False
        for m in _re.finditer(r'\{([^}]+)\}', s):
            has_interp = True
            literal = s[last:m.start()]
            if literal:
                parts.append(('lit', literal))
            parts.append(('expr', m.group(1).strip()))
            last = m.end()
        if last < len(s):
            parts.append(('lit', s[last:]))
        if not has_interp:
            return f'any({_go_string_lit(s)})'
        # Use feel_fmt(parts...) which joins with feel_str
        joined = []
        for kind, val in parts:
            if kind == 'lit':
                joined.append(_go_string_lit(val))
            else:
                # Parse `val` as a Feel expression
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
    'text', 'round', 'floor', 'abs', 'sum', 'max', 'min', 'first', 'last',
    'rest', 'push', 'join', 'split', 'contains',
}

STDLIB_MODULES = {'string', 'list', 'map', 'json'}


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
	"encoding/json"
	"fmt"
	"sort"
	"strings"
)

// ---------- Runtime types ----------

type feel_throw struct{ value any }

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
	case float64:
		if x == float64(int64(x)) {
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
	return any(feel_num(a) + feel_num(b))
}

func feel_sub(a, b any) any { return any(feel_num(a) - feel_num(b)) }
func feel_mul(a, b any) any { return any(feel_num(a) * feel_num(b)) }
func feel_div(a, b any) any {
	bv := feel_num(b)
	if bv == 0 {
		panic("division by zero")
	}
	return any(feel_num(a) / bv)
}

func feel_eq(a, b any) any {
	if a == nil || b == nil {
		return any(a == b)
	}
	return any(feel_str(a) == feel_str(b) && fmt.Sprintf("%T", a) == fmt.Sprintf("%T", b)) // best effort
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
	case float64, string, bool, nil:
		return v
	}
	return v
}
'''


def compile_to_go(source, filename='<input>'):
    """Return Go source string for the given Feel source."""
    tree = parse(source, filename=filename)
    emitter = GoEmitter()
    emitter.depth = 1  # inside main()
    body = emitter.emit_program(tree)

    return RUNTIME_GO + '\nfunc main() {\n' + body + '\n}\n'


def build_feel(feel_path, out_path=None, keep_go=False):
    """Compile a Feel file → Go → native binary via `go build`.

    Returns (ok: bool, message: str).
    """
    import os
    import subprocess
    import tempfile

    with open(feel_path, encoding='utf-8') as f:
        src = f.read()
    go_src = compile_to_go(src, filename=feel_path)

    # Output paths
    base = os.path.splitext(os.path.basename(feel_path))[0]
    if out_path is None:
        out_path = base + ('.exe' if os.name == 'nt' else '')

    # Write Go to a temp dir as main.go (Go requires main package files)
    tmpdir = tempfile.mkdtemp(prefix='feel_build_')
    go_file = os.path.join(tmpdir, 'main.go')
    with open(go_file, 'w', encoding='utf-8') as f:
        f.write(go_src)

    abs_out = os.path.abspath(out_path)
    try:
        result = subprocess.run(
            ['go', 'build', '-o', abs_out, 'main.go'],
            cwd=tmpdir,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except FileNotFoundError:
        return False, "go toolchain not found in PATH (install Go from https://go.dev)"
    except subprocess.TimeoutExpired:
        return False, "go build timed out (>120s)"

    if keep_go:
        kept = os.path.splitext(out_path)[0] + '.go'
        with open(kept, 'w', encoding='utf-8') as f:
            f.write(go_src)

    if result.returncode != 0:
        return False, f"go build failed:\n{result.stderr}\n\n-- generated Go saved at {go_file} --"

    # Cleanup tmpdir only if not keep
    if not keep_go:
        try:
            os.remove(go_file)
            os.rmdir(tmpdir)
        except OSError:
            pass

    return True, f"built {out_path}"
