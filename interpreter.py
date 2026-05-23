import re
import os
import sys

from parser import parse
from parser import (Program, LetStmt, DefineStmt, RecordDef, ShowStmt,
                    WhenStmt, RepeatStmt, ForStmt, Pipeline, BinOp, UnaryOp,
                    Call, CallExpr, FieldAccess, IndexAccess, RecordLiteral, MapLiteral,
                    ListLiteral, Ident, Literal, ArrowExpr,
                    TryStmt, ThrowStmt, CatchStep, ImportStmt, AssertStmt,
                    Block, Lambda, RouteDecl, RespondExpr, ServeStmt, StaticDecl,
                    ToolDecl, AgentDecl)
from errors import FeelError, FeelThrow


class FeelRecord:
    def __init__(self, type_name, fields):
        self.type_name = type_name
        self.fields = fields

    def __repr__(self):
        parts = ', '.join(f'{k}: {feel_str(v)}' for k, v in self.fields.items())
        return f'{self.type_name} {{ {parts} }}'


class FeelModule:
    """Namespace object hasil dari 'import name'."""
    def __init__(self, name, env):
        self.name = name
        self.env = env

    def __repr__(self):
        return f'<module {self.name}>'


class FeelFunction:
    def __init__(self, name, params, body, closure):
        self.name = name
        self.params = params
        self.body = body
        self.closure = closure

    def __repr__(self):
        return f'<function {self.name}>'


class FeelTool(FeelFunction):
    """A function with a description, intended to be exposed to AI agents."""

    def __init__(self, name, description, params, body, closure):
        super().__init__(name, params, body, closure)
        self.description = description

    def __repr__(self):
        return f'<tool {self.name}: {self.description!r}>'


class FeelAgent:
    """Declarative agent — system prompt + tools + model + .chat() with tool-use loop."""

    def __init__(self, name, system='', tools=None, model=None):
        self.name = name
        self.system = system
        self.tools = list(tools) if tools else []
        self.model = model

    def chat(self, message):
        """Chat with the agent. If the LLM requests a tool, execute it and continue."""
        from stdlib.ai_mod import chat_with_tools
        messages = [{'role': 'user', 'content': str(message)}]
        tool_schemas = [_tool_to_schema(t) for t in self.tools]

        def executor(name, args):
            for t in self.tools:
                if getattr(t, 'name', None) == name:
                    return _execute_feel_tool(t, args)
            raise RuntimeError(f"unknown tool: {name!r}")

        return chat_with_tools(
            messages,
            system=self.system if self.system else None,
            tools=tool_schemas if tool_schemas else None,
            model=self.model,
            tool_executor=executor,
        )

    def __repr__(self):
        tnames = [t.name for t in self.tools if hasattr(t, 'name')]
        return f'<agent {self.name}: tools={tnames}>'


def _tool_to_schema(tool):
    """Convert a FeelTool to Claude's tool_use schema."""
    if not isinstance(tool, FeelTool):
        return None
    properties = {}
    for p in tool.params:
        properties[p] = {'type': 'string', 'description': f'parameter {p}'}
    return {
        'name': tool.name,
        'description': tool.description,
        'input_schema': {
            'type': 'object',
            'properties': properties,
            'required': list(tool.params),
        }
    }


def _execute_feel_tool(tool, args_dict):
    """Run a FeelTool with args provided by the LLM (dict matching parameter names)."""
    # Map dict args to positional in declared order
    args = [args_dict.get(p) for p in tool.params]
    local = Environment(tool.closure)
    for p, v in zip(tool.params, args):
        local.set(p, v)
    sub = Interpreter.__new__(Interpreter)
    sub.env = local
    sub.filename = '<tool>'
    sub.source = None
    sub.search_paths = []
    sub.record_types = {}
    return sub.eval_expr(tool.body)


class Environment:
    def __init__(self, parent=None):
        self.vars = {}
        self.parent = parent

    def get(self, name):
        if name in self.vars:
            return self.vars[name]
        if self.parent:
            return self.parent.get(name)
        raise KeyError(name)

    def has(self, name):
        if name in self.vars: return True
        if self.parent: return self.parent.has(name)
        return False

    def set(self, name, value):
        self.vars[name] = value

    def assign(self, name, value):
        if name in self.vars:
            self.vars[name] = value
        elif self.parent:
            self.parent.assign(name, value)
        else:
            self.vars[name] = value


def feel_str(val):
    if val is None: return 'nothing'
    if val is True: return 'true'
    if val is False: return 'false'
    if isinstance(val, float) and val == int(val): return str(int(val))
    if isinstance(val, list): return '[' + ', '.join(feel_str(v) for v in val) + ']'
    if isinstance(val, dict):
        parts = ', '.join(f'{feel_str(k)}: {feel_str(v)}' for k, v in val.items())
        return 'map { ' + parts + ' }'
    return str(val)


def _suggest(name, candidates):
    """Levenshtein-ish suggestion sederhana."""
    if not candidates: return None
    name_l = name.lower()
    best = None
    best_score = 0
    for c in candidates:
        c_l = c.lower()
        # quick: prefix match score
        shared = 0
        for a, b in zip(name_l, c_l):
            if a == b: shared += 1
            else: break
        if shared >= max(2, len(name_l) // 2) and shared > best_score:
            best = c
            best_score = shared
    return best


# Builtins inti — string, list, math operations
def _to_number(x):
    if isinstance(x, (int, float)): return x
    s = str(x)
    return float(s) if '.' in s else int(s)


BUILTINS = {
    'uppercase': lambda s: s.upper() if isinstance(s, str) else s,
    'lowercase': lambda s: s.lower() if isinstance(s, str) else s,
    'length':    lambda x: len(x),
    'reverse':   lambda x: x[::-1] if isinstance(x, (str, list)) else x,
    'type_of':   lambda x: _type_of(x),
    'number':    _to_number,
    'int':       lambda x: int(x) if not isinstance(x, bool) else (1 if x else 0),
    'float':     lambda x: float(x) if not isinstance(x, bool) else (1.0 if x else 0.0),
    'is_int':    lambda x: isinstance(x, int) and not isinstance(x, bool),
    'is_float':  lambda x: isinstance(x, float),
    'text':      lambda x: feel_str(x),
    'round':     lambda x: round(x),
    'floor':     lambda x: int(x),
    'abs':       lambda x: abs(x),
    'sum':       lambda x: sum(x),
    'max':       lambda x: max(x),
    'min':       lambda x: min(x),
    'first':     lambda x: x[0] if x else None,
    'last':      lambda x: x[-1] if x else None,
    'rest':      lambda x: x[1:] if isinstance(x, (str, list)) else x,
    'push':      lambda x, item: x + [item],
    'join':      lambda x, sep='': sep.join(feel_str(v) for v in x),
    'split':     lambda s, sep=' ': s.split(sep),
    'contains':  lambda x, item: item in x,
}


def _type_of(x):
    if x is None: return 'nothing'
    if x is True or x is False: return 'boolean'
    if isinstance(x, bool): return 'boolean'
    if isinstance(x, int) or isinstance(x, float): return 'number'
    if isinstance(x, str): return 'text'
    if isinstance(x, list): return 'list'
    if isinstance(x, dict): return 'map'
    if isinstance(x, FeelRecord): return x.type_name
    if isinstance(x, FeelFunction): return 'function'
    if isinstance(x, FeelModule): return 'module'
    return type(x).__name__


class Interpreter:
    # Untuk module loader: kelas-level cache supaya satu module hanya di-load sekali
    _module_cache = {}
    _module_loading = set()

    def __init__(self, env=None, filename='<input>', source=None, search_paths=None):
        self.env = env or Environment()
        self.filename = filename
        self.source = source
        self.search_paths = search_paths or [os.getcwd()]
        self.record_types = {}
        self._setup_builtins()

    def _setup_builtins(self):
        for name, fn in BUILTINS.items():
            self.env.set(name, fn)
        self.env.set('show', lambda x: (print(feel_str(x)), x)[1])
        # stdlib lazy import
        from stdlib import install_into
        install_into(self.env)

    def _interpolate(self, s):
        """Replace {expr} di string. `\\{` and `\\}` produce literal braces.

        The lexer already turned escaped braces into \\x00 / \\x01 placeholders so
        the interpolation regex skips them. After substitution we restore the
        literal characters.
        """
        def replace(m):
            expr_src = m.group(1).strip()
            sub = parse(expr_src, filename=self.filename)
            if not sub.stmts:
                return ''
            val = self.eval_stmt(sub.stmts[0])
            return feel_str(val)
        result = re.sub(r'\{([^}]+)\}', replace, s)
        return result.replace('\x00', '{').replace('\x01', '}')

    def run(self, source):
        tree = parse(source, filename=self.filename)
        result = None
        for stmt in tree.stmts:
            if stmt is None: continue
            result = self.eval_stmt(stmt)
        return result

    def eval_stmt(self, node):
        if isinstance(node, LetStmt):
            val = self.eval_expr(node.value)
            self.env.set(node.name, val)
            return val

        if isinstance(node, DefineStmt):
            fn = FeelFunction(node.name, node.params, node.body, self.env)
            self.env.set(node.name, fn)
            return fn

        if isinstance(node, RecordDef):
            self.record_types[node.name] = node.fields
            def make_constructor(rname):
                def constructor(**kwargs):
                    return FeelRecord(rname, kwargs)
                return constructor
            self.env.set(node.name, make_constructor(node.name))
            return None

        if isinstance(node, ShowStmt):
            val = self.eval_expr(node.expr)
            print(feel_str(val))
            return val

        if isinstance(node, WhenStmt):
            cond = self.eval_expr(node.cond)
            if cond:
                return self.eval_expr(node.then)
            elif node.otherwise:
                return self.eval_expr(node.otherwise)
            return None

        if isinstance(node, RepeatStmt):
            count = self.eval_expr(node.count)
            if not isinstance(count, (int, float)):
                raise FeelError.type_error(node, f"'repeat' expects a number, got {_type_of(count)}",
                                           filename=self.filename, source=self.source)
            result = None
            for _ in range(int(count)):
                result = self.eval_expr(node.body)
            return result

        if isinstance(node, ForStmt):
            iterable = self.eval_expr(node.iterable)
            if not isinstance(iterable, (list, str, dict)):
                raise FeelError.type_error(node, f"'for' expects list/text/map, got {_type_of(iterable)}",
                                           filename=self.filename, source=self.source)
            result = None
            items = list(iterable.keys()) if isinstance(iterable, dict) else iterable
            for item in items:
                local = Environment(self.env)
                local.set(node.var, item)
                old_env = self.env
                self.env = local
                try:
                    result = self.eval_expr(node.body)
                finally:
                    self.env = old_env
            return result

        if isinstance(node, TryStmt):
            try:
                return self.eval_expr(node.body)
            except FeelThrow as ft:
                local = Environment(self.env)
                local.set(node.err_name, ft.value)
                old_env = self.env
                self.env = local
                try:
                    return self.eval_expr(node.handler)
                finally:
                    self.env = old_env
            except FeelError as fe:
                # juga tangkap FeelError sebagai catchable (text message)
                local = Environment(self.env)
                local.set(node.err_name, fe.raw_message)
                old_env = self.env
                self.env = local
                try:
                    return self.eval_expr(node.handler)
                finally:
                    self.env = old_env

        if isinstance(node, ThrowStmt):
            val = self.eval_expr(node.expr)
            raise FeelThrow(val, node=node)

        if isinstance(node, ImportStmt):
            module = self._load_module(node)
            if node.expose is not None:
                # expose list: bring nama-nama ke scope sekarang
                for nm in node.expose:
                    if not module.env.has(nm):
                        raise FeelError.runtime(
                            node, f"module '{node.name}' has no '{nm}'",
                            hint=f"check the contents of {node.name}.feel",
                            filename=self.filename, source=self.source)
                    self.env.set(nm, module.env.get(nm))
            else:
                # import as namespace — bind to last path segment so
                # `import auth/service` is usable as `service.funcname`
                bind_name = node.name.split('/')[-1]
                self.env.set(bind_name, module)
            return module

        if isinstance(node, RouteDecl):
            self._register_route(node)
            return None

        if isinstance(node, ServeStmt):
            from runtime.http import serve
            serve(port=node.port, cors=node.cors,
                  cert_file=node.cert_file, key_file=node.key_file)
            return None

        if isinstance(node, StaticDecl):
            from runtime.router import global_registry
            global_registry().mount_static(node.url_prefix, node.fs_dir)
            return None

        if isinstance(node, ToolDecl):
            tool = FeelTool(node.name, node.description, node.params, node.body, self.env)
            self.env.set(node.name, tool)
            return tool

        if isinstance(node, AgentDecl):
            config = {k: self.eval_expr(v) for k, v in node.config.items()}
            agent = FeelAgent(
                name=node.name,
                system=config.get('system', ''),
                tools=config.get('tools', []),
                model=config.get('model', None),
            )
            self.env.set(node.name, agent)
            return agent

        if isinstance(node, AssertStmt):
            cond = self.eval_expr(node.cond)
            if not cond:
                msg = "assertion failed"
                if node.message:
                    msg = feel_str(self.eval_expr(node.message))
                raise FeelError.runtime(node, msg,
                                        hint="assert expression must evaluate to true",
                                        filename=self.filename, source=self.source)
            return True

        # Expression as statement
        return self.eval_expr(node)

    def eval_expr(self, node):
        if isinstance(node, Literal):
            val = node.value
            if isinstance(val, str):
                return self._interpolate(val)
            return val

        if isinstance(node, Ident):
            if not self.env.has(node.name):
                # collect kandidat nama untuk saran typo
                names = []
                env = self.env
                while env:
                    names.extend(env.vars.keys())
                    env = env.parent
                similar = _suggest(node.name, names)
                raise FeelError.name_error(node, node.name,
                                           filename=self.filename, source=self.source,
                                           similar=similar)
            return self.env.get(node.name)

        if isinstance(node, ArrowExpr):
            return self.eval_expr(node.expr)

        if isinstance(node, ShowStmt):
            val = self.eval_expr(node.expr)
            print(feel_str(val))
            return val

        if isinstance(node, WhenStmt):
            cond = self.eval_expr(node.cond)
            if cond:
                return self.eval_expr(node.then)
            elif node.otherwise:
                return self.eval_expr(node.otherwise)
            return None

        if isinstance(node, BinOp):
            l = self.eval_expr(node.left)
            r = self.eval_expr(node.right)
            op = node.op
            try:
                if op == '+': return l + r
                if op == '-': return l - r
                if op == '*': return l * r
                if op == '/':
                    if r == 0:
                        raise FeelError.runtime(node, "division by zero",
                                                hint="check the divisor before dividing",
                                                filename=self.filename, source=self.source)
                    return l / r
                if op == '==': return l == r
                if op == '!=': return l != r
                if op == '>':  return l > r
                if op == '<':  return l < r
                if op == '>=': return l >= r
                if op == '<=': return l <= r
                if op == 'and': return l and r
                if op == 'or':  return l or r
            except FeelError:
                raise
            except TypeError as e:
                raise FeelError.type_error(node,
                    f"operator '{op}' cannot be applied to {_type_of(l)} and {_type_of(r)}",
                    filename=self.filename, source=self.source)

        if isinstance(node, UnaryOp):
            val = self.eval_expr(node.expr)
            if node.op == 'not': return not val
            if node.op == '-':   return -val

        if isinstance(node, Pipeline):
            value = self.eval_expr(node.steps[0])
            i = 1
            while i < len(node.steps):
                step = node.steps[i]
                if isinstance(step, CatchStep):
                    # catch step di pipeline: wrap sisa ekspresi sebelumnya dengan try
                    # tapi pipeline sudah linear — catch hanya berlaku kalau exception belum terjadi
                    # logic: kalau sebelumnya throw, value akan ditangkap di sini
                    # untuk simple: catch dalam pipeline hanya berlaku via try-around — gunakan handler kalau ada error
                    # implementasi: kita sudah lewati (nilai aman); kalau mau effective, pakai try di luar
                    # untuk membuat ini berguna: wrap eksekusi step BERIKUTNYA dengan try
                    i += 1
                    continue
                # cek apakah step BERIKUTNYA adalah catch — kalau ya, wrap step ini dengan try
                next_step = node.steps[i + 1] if i + 1 < len(node.steps) else None
                if isinstance(next_step, CatchStep):
                    try:
                        fn = self.eval_expr(step)
                        value = self._call_fn(fn, [value], node=step)
                    except (FeelThrow, FeelError):
                        value = self.eval_expr(next_step.handler)
                    i += 2  # skip catch
                else:
                    fn = self.eval_expr(step)
                    value = self._call_fn(fn, [value], node=step)
                    i += 1
            return value

        if isinstance(node, CallExpr):
            fn = self.eval_expr(node.callee)
            args = [self.eval_expr(a) for a in node.args]
            return self._call_fn(fn, args, node=node)

        if isinstance(node, Call):
            if not self.env.has(node.name):
                names = []
                env = self.env
                while env:
                    names.extend(env.vars.keys())
                    env = env.parent
                similar = _suggest(node.name, names)
                raise FeelError.name_error(node, node.name,
                                           filename=self.filename, source=self.source,
                                           similar=similar)
            fn = self.env.get(node.name)
            args = [self.eval_expr(a) for a in node.args]
            return self._call_fn(fn, args, node=node)

        if isinstance(node, FieldAccess):
            obj = self.eval_expr(node.obj)
            if isinstance(obj, FeelRecord):
                if node.field in obj.fields:
                    return obj.fields[node.field]
                raise FeelError.runtime(node,
                    f"record '{obj.type_name}' has no field '{node.field}'",
                    hint=f"available fields: {', '.join(obj.fields.keys())}",
                    filename=self.filename, source=self.source)
            if isinstance(obj, FeelModule):
                if obj.env.has(node.field):
                    return obj.env.get(node.field)
                raise FeelError.runtime(node,
                    f"module '{obj.name}' has no '{node.field}'",
                    filename=self.filename, source=self.source)
            if isinstance(obj, FeelTool):
                if node.field == 'name':        return obj.name
                if node.field == 'description': return obj.description
                if node.field == 'parameters':  return list(obj.params)
                raise FeelError.runtime(node,
                    f"tool '{obj.name}' has no field '{node.field}'",
                    hint="available fields: name, description, parameters",
                    filename=self.filename, source=self.source)
            if isinstance(obj, FeelAgent):
                if node.field == 'name':   return obj.name
                if node.field == 'system': return obj.system
                if node.field == 'tools':  return list(obj.tools)
                if node.field == 'model':  return obj.model
                if node.field == 'chat':
                    # Return bound method as Python callable (works with CallExpr)
                    return obj.chat
                raise FeelError.runtime(node,
                    f"agent '{obj.name}' has no field '{node.field}'",
                    hint="available fields: name, system, tools, model, chat",
                    filename=self.filename, source=self.source)
            if isinstance(obj, dict):
                return obj.get(node.field)
            raise FeelError.type_error(node,
                f"cannot access field '.{node.field}' on {_type_of(obj)}",
                filename=self.filename, source=self.source)

        if isinstance(node, IndexAccess):
            obj = self.eval_expr(node.obj)
            idx = self.eval_expr(node.index)
            if isinstance(obj, dict):
                return obj.get(idx)
            if isinstance(obj, (list, str)):
                try:
                    return obj[int(idx)]
                except (ValueError, TypeError, IndexError) as e:
                    raise FeelError.runtime(node, f"invalid index: {e}",
                                            filename=self.filename, source=self.source)
            raise FeelError.type_error(node,
                f"cannot index into {_type_of(obj)}",
                filename=self.filename, source=self.source)

        if isinstance(node, RecordLiteral):
            fields = {k: self.eval_expr(v) for k, v in node.fields.items()}
            return FeelRecord(node.name, fields)

        if isinstance(node, MapLiteral):
            result = {}
            for k_node, v_node in node.entries:
                k = self.eval_expr(k_node)
                v = self.eval_expr(v_node)
                result[k] = v
            return result

        if isinstance(node, ListLiteral):
            return [self.eval_expr(i) for i in node.items]

        if isinstance(node, Block):
            # New scope, run all stmts, return last value
            local = Environment(self.env)
            old_env = self.env
            self.env = local
            try:
                result = None
                for stmt in node.stmts:
                    result = self.eval_stmt(stmt)
                return result
            finally:
                self.env = old_env

        if isinstance(node, Lambda):
            # Anonymous function — closure captures current env
            return FeelFunction('<lambda>', node.params, node.body, self.env)

        if isinstance(node, RespondExpr):
            from runtime.http import FeelResponse
            body = self.eval_expr(node.body) if node.body is not None else None
            # Convert FeelRecord to dict for JSON encoding
            if isinstance(body, FeelRecord):
                body = dict(body.fields)
            return FeelResponse(status=node.status, body=body)

        if isinstance(node, RouteDecl):
            self._register_route(node)
            return None

        if isinstance(node, ServeStmt):
            from runtime.http import serve
            serve(port=node.port, cors=node.cors,
                  cert_file=node.cert_file, key_file=node.key_file)
            return None

        if isinstance(node, StaticDecl):
            from runtime.router import global_registry
            global_registry().mount_static(node.url_prefix, node.fs_dir)
            return None

        if isinstance(node, ThrowStmt):
            val = self.eval_expr(node.expr)
            raise FeelThrow(val, node=node)

        if isinstance(node, TryStmt):
            return self.eval_stmt(node)

        raise FeelError.runtime(node, f"node not yet handled: {type(node).__name__}",
                                filename=self.filename, source=self.source)

    def _call_fn(self, fn, args, node=None):
        if callable(fn) and not isinstance(fn, FeelFunction):
            try:
                return fn(*args)
            except FeelError:
                raise
            except FeelThrow:
                raise
            except TypeError as e:
                # coba lagi dengan signature 1-arg (legacy)
                if len(args) >= 1:
                    try:
                        return fn(args[0])
                    except Exception as e2:
                        raise FeelError.type_error(node or Literal(None),
                            f"function call failed: {e2}",
                            filename=self.filename, source=self.source)
                raise FeelError.type_error(node or Literal(None),
                    f"function call failed: {e}",
                    filename=self.filename, source=self.source)
            except Exception as e:
                raise FeelError.runtime(node or Literal(None),
                    f"error while calling function: {e}",
                    filename=self.filename, source=self.source)

        if isinstance(fn, FeelFunction):
            local = Environment(fn.closure)
            for param, arg in zip(fn.params, args):
                local.set(param, arg)
            old_env = self.env
            self.env = local
            try:
                result = self.eval_expr(fn.body)
            finally:
                self.env = old_env
            return result

        raise FeelError.type_error(node or Literal(None),
            f"'{feel_str(fn)}' is not callable",
            filename=self.filename, source=self.source)

    def _register_route(self, route_node):
        """Register a Feel route handler with the global registry."""
        from runtime.router import global_registry
        handler_ast = route_node.handler
        closure_env = self.env
        filename = self.filename
        source = self.source

        def py_handler(feel_request):
            # Build request as a Feel map so the handler can do request.headers,
            # request.query["k"], request.body, etc.
            request_map = {
                'method':  feel_request.method,
                'path':    feel_request.path,
                'query':   feel_request.query,
                'headers': feel_request.headers,
                'body':    feel_request.body,
                'params':  feel_request.params,
                'files':   feel_request.files,
                'form':    feel_request.form,
            }
            local = Environment(closure_env)
            local.set('request', request_map)
            local.set('body',  feel_request.body)
            local.set('query', feel_request.query)
            local.set('files', feel_request.files)
            local.set('form',  feel_request.form)
            for k, v in feel_request.params.items():
                local.set(k, v)
            # WebSocket handler: expose `ws` in scope.
            if getattr(feel_request, '_ws', None) is not None:
                local.set('ws', feel_request._ws.to_feel_map())
            sub = Interpreter.__new__(Interpreter)
            sub.env = local
            sub.filename = filename
            sub.source = source
            sub.search_paths = self.search_paths
            sub.record_types = self.record_types
            try:
                return sub.eval_expr(handler_ast)
            except FeelThrow as ft:
                # `throw map { status: 4xx, ... }` — convert to HTTP response
                from runtime.http import FeelResponse
                val = ft.value
                if isinstance(val, dict) and 'status' in val:
                    return FeelResponse(status=int(val['status']), body=val)
                return FeelResponse(status=500, body={'error': 'unhandled_throw', 'message': str(val)})

        global_registry().register(route_node.method, route_node.path, py_handler)

    def _load_module(self, import_node):
        name = import_node.name
        # cari file
        target = None
        for base in self.search_paths:
            candidate = os.path.join(base, f'{name}.feel')
            if os.path.isfile(candidate):
                target = os.path.abspath(candidate)
                break
        if target is None:
            raise FeelError.runtime(import_node,
                f"module '{name}' not found",
                hint=f"searched in: {', '.join(self.search_paths)}. Make sure {name}.feel exists.",
                filename=self.filename, source=self.source)

        if target in Interpreter._module_cache:
            return Interpreter._module_cache[target]

        if target in Interpreter._module_loading:
            raise FeelError.runtime(import_node,
                f"circular import detected on '{name}'",
                hint="avoid modules importing each other",
                filename=self.filename, source=self.source)

        Interpreter._module_loading.add(target)
        try:
            with open(target, encoding='utf-8') as f:
                src = f.read()
            mod_env = Environment()
            sub = Interpreter(env=mod_env, filename=target, source=src,
                              search_paths=self.search_paths)
            sub.run(src)
            module = FeelModule(name, mod_env)
            Interpreter._module_cache[target] = module
            return module
        finally:
            Interpreter._module_loading.discard(target)


def run_file(path):
    abs_path = os.path.abspath(path)
    with open(abs_path, encoding='utf-8') as f:
        source = f.read()
    search = [os.path.dirname(abs_path), os.getcwd()]
    interp = Interpreter(filename=abs_path, source=source, search_paths=search)
    interp.run(source)
