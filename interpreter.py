import re
from parser import parse
from parser import (Program, LetStmt, DefineStmt, RecordDef, ShowStmt,
                    WhenStmt, RepeatStmt, ForStmt, Pipeline, BinOp, UnaryOp,
                    Call, FieldAccess, RecordLiteral, ListLiteral,
                    Ident, Literal, ArrowExpr)


class FeelRecord:
    def __init__(self, type_name, fields):
        self.type_name = type_name
        self.fields = fields

    def __repr__(self):
        parts = ', '.join(f'{k}: {repr(v)}' for k, v in self.fields.items())
        return f'{self.type_name} {{ {parts} }}'


class FeelFunction:
    def __init__(self, name, params, body, closure):
        self.name = name
        self.params = params
        self.body = body
        self.closure = closure

    def __repr__(self):
        return f'<function {self.name}>'


class Environment:
    def __init__(self, parent=None):
        self.vars = {}
        self.parent = parent

    def get(self, name):
        if name in self.vars:
            return self.vars[name]
        if self.parent:
            return self.parent.get(name)
        raise NameError(f"'{name}' is not defined")

    def set(self, name, value):
        self.vars[name] = value

    def assign(self, name, value):
        if name in self.vars:
            self.vars[name] = value
        elif self.parent:
            self.parent.assign(name, value)
        else:
            self.vars[name] = value


def interpolate(s, env):
    def replace(m):
        expr = m.group(1).strip()
        try:
            val = Interpreter(env).eval_expr(parse(expr).stmts[0])
            return feel_str(val)
        except:
            return m.group(0)
    return re.sub(r'\{([^}]+)\}', replace, s)


def feel_str(val):
    if val is None: return 'nothing'
    if val is True: return 'true'
    if val is False: return 'false'
    if isinstance(val, float) and val == int(val): return str(int(val))
    if isinstance(val, list): return '[' + ', '.join(feel_str(v) for v in val) + ']'
    return str(val)


BUILTINS = {
    'uppercase': lambda s: s.upper() if isinstance(s, str) else s,
    'lowercase': lambda s: s.lower() if isinstance(s, str) else s,
    'length':    lambda x: len(x),
    'reverse':   lambda x: x[::-1],
    'type_of':   lambda x: type(x).__name__,
    'number':    lambda x: float(x) if '.' in str(x) else int(x),
    'text':      lambda x: feel_str(x),
    'round':     lambda x: round(x),
    'floor':     lambda x: int(x),
    'abs':       lambda x: abs(x),
    'sum':       lambda x: sum(x),
    'max':       lambda x: max(x),
    'min':       lambda x: min(x),
    'first':     lambda x: x[0] if x else None,
    'last':      lambda x: x[-1] if x else None,
    'rest':      lambda x: x[1:],
    'push':      lambda x, item: x + [item],
    'join':      lambda x, sep='': sep.join(feel_str(v) for v in x),
    'split':     lambda s, sep=' ': s.split(sep),
    'contains':  lambda x, item: item in x,
    'filter':    None,  # handled specially
    'map':       None,  # handled specially
}


class Interpreter:
    def __init__(self, env=None):
        self.env = env or Environment()
        self._setup_builtins()
        self.record_types = {}

    def _setup_builtins(self):
        for name, fn in BUILTINS.items():
            if fn is not None:
                self.env.set(name, fn)
        self.env.set('show', lambda x: print(feel_str(x)) or x)

    def run(self, source):
        tree = parse(source)
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
            # Store a constructor in env
            def make_constructor(rname, rfields):
                def constructor(**kwargs):
                    return FeelRecord(rname, kwargs)
                return constructor
            self.env.set(node.name, make_constructor(node.name, node.fields))
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
            result = None
            for _ in range(int(count)):
                result = self.eval_expr(node.body)
            return result

        if isinstance(node, ForStmt):
            iterable = self.eval_expr(node.iterable)
            result = None
            for item in iterable:
                local = Environment(self.env)
                local.set(node.var, item)
                old_env = self.env
                self.env = local
                result = self.eval_expr(node.body)
                self.env = old_env
            return result

        # Expression as statement
        return self.eval_expr(node)

    def eval_expr(self, node):
        if isinstance(node, Literal):
            val = node.value
            if isinstance(val, str):
                return interpolate(val, self.env)
            return val

        if isinstance(node, Ident):
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
            if op == '+': return l + r
            if op == '-': return l - r
            if op == '*': return l * r
            if op == '/': return l / r
            if op == '==': return l == r
            if op == '!=': return l != r
            if op == '>':  return l > r
            if op == '<':  return l < r
            if op == '>=': return l >= r
            if op == '<=': return l <= r
            if op == 'and': return l and r
            if op == 'or':  return l or r

        if isinstance(node, UnaryOp):
            val = self.eval_expr(node.expr)
            if node.op == 'not': return not val
            if node.op == '-':   return -val

        if isinstance(node, Pipeline):
            value = self.eval_expr(node.steps[0])
            for step in node.steps[1:]:
                fn = self.eval_expr(step)
                value = self._call_fn(fn, [value])
            return value

        if isinstance(node, Call):
            fn = self.env.get(node.name)
            args = [self.eval_expr(a) for a in node.args]
            return self._call_fn(fn, args)

        if isinstance(node, FieldAccess):
            obj = self.eval_expr(node.obj)
            if isinstance(obj, FeelRecord):
                if node.field in obj.fields:
                    return obj.fields[node.field]
                raise AttributeError(f"Record has no field '{node.field}'")
            raise TypeError(f"Cannot access field on {type(obj).__name__}")

        if isinstance(node, RecordLiteral):
            fields = {k: self.eval_expr(v) for k, v in node.fields.items()}
            return FeelRecord(node.name, fields)

        if isinstance(node, ListLiteral):
            return [self.eval_expr(i) for i in node.items]

        raise RuntimeError(f"Unknown node: {type(node).__name__}")

    def _call_fn(self, fn, args):
        if callable(fn) and not isinstance(fn, FeelFunction):
            try:
                return fn(*args)
            except TypeError:
                return fn(args[0]) if args else fn()

        if isinstance(fn, FeelFunction):
            local = Environment(fn.closure)
            for param, arg in zip(fn.params, args):
                local.set(param, arg)
            old_env = self.env
            self.env = local
            result = self.eval_expr(fn.body)
            self.env = old_env
            return result

        raise TypeError(f"'{feel_str(fn)}' is not callable")


def run_file(path):
    with open(path) as f:
        source = f.read()
    interp = Interpreter()
    interp.run(source)
