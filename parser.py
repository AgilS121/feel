import re

from lexer import tokenize
from errors import FeelError


# Naming convention regex (enforced by parser, not linter)
_SNAKE_CASE = re.compile(r'^[a-z_][a-z0-9_]*$')
_PASCAL_CASE = re.compile(r'^[A-Z][a-zA-Z0-9]*$')


def _to_snake(name):
    """Convert PascalCase or camelCase to snake_case (for hint)."""
    s1 = re.sub(r'(.)([A-Z][a-z]+)', r'\1_\2', name)
    return re.sub(r'([a-z0-9])([A-Z])', r'\1_\2', s1).lower()


def _to_pascal(name):
    """Convert snake_case to PascalCase (for hint)."""
    return ''.join(part.capitalize() for part in name.split('_'))


# AST Nodes
class Node:
    line = 1
    col = 1


def _pos(node, tok_or_node):
    """Copy line/col dari token atau node ke node baru."""
    if tok_or_node is not None:
        node.line = getattr(tok_or_node, 'line', 1) or 1
        node.col = getattr(tok_or_node, 'col', 1) or 1
    return node


class Program(Node):
    def __init__(self, stmts): self.stmts = stmts


class LetStmt(Node):
    def __init__(self, name, value): self.name = name; self.value = value


class DefineStmt(Node):
    def __init__(self, name, params, body): self.name = name; self.params = params; self.body = body


class RecordDef(Node):
    def __init__(self, name, fields): self.name = name; self.fields = fields


class ShowStmt(Node):
    def __init__(self, expr): self.expr = expr


class WhenStmt(Node):
    def __init__(self, cond, then, otherwise=None):
        self.cond = cond; self.then = then; self.otherwise = otherwise


class RepeatStmt(Node):
    def __init__(self, count, body): self.count = count; self.body = body


class ForStmt(Node):
    def __init__(self, var, iterable, body): self.var = var; self.iterable = iterable; self.body = body


class TryStmt(Node):
    def __init__(self, body, err_name, handler):
        self.body = body; self.err_name = err_name; self.handler = handler


class ThrowStmt(Node):
    def __init__(self, expr): self.expr = expr


class CatchStep(Node):
    """Step di dalam pipeline: '| catch -> default_expr'."""
    def __init__(self, handler): self.handler = handler


class ImportStmt(Node):
    def __init__(self, name, expose=None, alias=None):
        self.name = name
        self.expose = expose  # list[str] or None (None = import all sebagai namespace)
        self.alias = alias


class Pipeline(Node):
    def __init__(self, steps): self.steps = steps


class BinOp(Node):
    def __init__(self, op, left, right): self.op = op; self.left = left; self.right = right


class UnaryOp(Node):
    def __init__(self, op, expr): self.op = op; self.expr = expr


class Call(Node):
    def __init__(self, name, args): self.name = name; self.args = args


class FieldAccess(Node):
    def __init__(self, obj, field): self.obj = obj; self.field = field


class IndexAccess(Node):
    def __init__(self, obj, index): self.obj = obj; self.index = index


class CallExpr(Node):
    """Pemanggilan fungsi pada hasil ekspresi (mis. modul.func(args), arr[i](x))."""
    def __init__(self, callee, args): self.callee = callee; self.args = args


class RecordLiteral(Node):
    def __init__(self, name, fields): self.name = name; self.fields = fields


class MapLiteral(Node):
    def __init__(self, entries):
        # entries: list of (key_node, value_node)
        self.entries = entries


class ListLiteral(Node):
    def __init__(self, items): self.items = items


class Block(Node):
    """do { stmt; stmt; expr } — sequence of statements, last value returned."""
    def __init__(self, stmts): self.stmts = stmts


class Lambda(Node):
    """fn x, y -> expr — anonymous function with closure."""
    def __init__(self, params, body): self.params = params; self.body = body


class RouteDecl(Node):
    """route METHOD "path" -> handler_body."""
    def __init__(self, method, path, handler):
        self.method = method
        self.path = path
        self.handler = handler


class RespondExpr(Node):
    """respond [STATUS] [BODY] — build response value."""
    def __init__(self, status, body):
        self.status = status
        self.body = body


class ServeStmt(Node):
    """serve on PORT — start HTTP server, blocking."""
    def __init__(self, port): self.port = port


class ToolDecl(Node):
    """tool NAME "description" taking p1, p2 -> body — function with metadata for AI."""
    def __init__(self, name, description, params, body):
        self.name = name
        self.description = description
        self.params = params
        self.body = body


class AgentDecl(Node):
    """agent NAME { system: ..., tools: [...], model: ... } — declarative agent."""
    def __init__(self, name, config):
        # config: dict of field_name -> expression node
        self.name = name
        self.config = config


class Ident(Node):
    def __init__(self, name): self.name = name


class Literal(Node):
    def __init__(self, value): self.value = value


class ArrowExpr(Node):
    def __init__(self, expr): self.expr = expr


class AssertStmt(Node):
    def __init__(self, cond, message=None):
        self.cond = cond; self.message = message


class Parser:
    def __init__(self, tokens, filename='<input>', source=None):
        self.tokens = tokens
        self.pos = 0
        self.filename = filename
        self.source = source

    def peek(self, offset=0):
        i = self.pos + offset
        while i < len(self.tokens) and self.tokens[i].type == 'NEWLINE':
            i += 1
        return self.tokens[i] if i < len(self.tokens) else None

    def advance(self):
        if self.pos >= len(self.tokens):
            return None
        t = self.tokens[self.pos]
        self.pos += 1
        return t

    def skip_newlines(self):
        while self.pos < len(self.tokens) and self.tokens[self.pos].type == 'NEWLINE':
            self.pos += 1

    def expect(self, type_, hint=None):
        self.skip_newlines()
        if self.pos >= len(self.tokens):
            last = self.tokens[-1] if self.tokens else None
            raise FeelError.syntax(last, f"expected {type_}, but reached end of file", hint=hint,
                                   filename=self.filename, source=self.source)
        t = self.tokens[self.pos]
        if t.type != type_:
            raise FeelError.syntax(t, f"expected {type_}, got {t.type} ({t.value!r})",
                                   hint=hint, filename=self.filename, source=self.source)
        self.pos += 1
        return t

    def current(self):
        self.skip_newlines()
        if self.pos >= len(self.tokens):
            return None
        return self.tokens[self.pos]

    # token keyword yang boleh dipakai sebagai nama field/method setelah '.'
    _NAME_LIKE = {
        'IDENT', 'LET', 'DEFINE', 'TAKING', 'SHOW', 'RECORD',
        'WHEN', 'OTHERWISE', 'REPEAT', 'TIMES', 'FOR', 'IN',
        'AND', 'OR', 'NOT', 'TRUE', 'FALSE', 'NOTHING',
        'TRY', 'CATCH', 'THROW', 'ERROR', 'MAP',
        'IMPORT', 'FROM', 'EXPOSE', 'ASSERT', 'FN', 'DO',
        'ROUTE', 'RESPOND', 'SERVE', 'ON', 'EXPECTS',
        'TOOL', 'AGENT',
    }

    # Token types yang bisa mulai sebuah ekspresi (untuk lookahead di respond)
    _EXPR_STARTERS = {
        'NUMBER', 'STRING', 'TRUE', 'FALSE', 'NOTHING', 'IDENT',
        'LPAREN', 'LBRACKET', 'MAP', 'FN', 'DO', 'SHOW', 'WHEN',
        'TRY', 'THROW', 'MINUS', 'NOT', 'RESPOND',
    }

    _HTTP_METHODS = {'GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS'}

    def check_snake_case(self, name, token, kind):
        """Enforce snake_case for variables, functions, parameters."""
        if not _SNAKE_CASE.match(name):
            suggested = _to_snake(name)
            raise FeelError.syntax(token,
                f"{kind} name '{name}' must be snake_case",
                hint=f"rename to '{suggested}'",
                filename=self.filename, source=self.source)

    def check_pascal_case(self, name, token, kind):
        """Enforce PascalCase for record types."""
        if not _PASCAL_CASE.match(name):
            suggested = _to_pascal(name)
            raise FeelError.syntax(token,
                f"{kind} name '{name}' must be PascalCase",
                hint=f"rename to '{suggested}'",
                filename=self.filename, source=self.source)

    def expect_name(self, hint=None):
        """Terima IDENT atau keyword sebagai nama (untuk method/field access)."""
        self.skip_newlines()
        if self.pos >= len(self.tokens):
            last = self.tokens[-1] if self.tokens else None
            raise FeelError.syntax(last, "expected field or method name",
                                   hint=hint, filename=self.filename, source=self.source)
        t = self.tokens[self.pos]
        if t.type not in self._NAME_LIKE:
            raise FeelError.syntax(t, f"expected field or method name, got {t.type}",
                                   hint=hint, filename=self.filename, source=self.source)
        self.pos += 1
        return t

    def parse(self):
        stmts = []
        while self.pos < len(self.tokens):
            self.skip_newlines()
            if self.pos >= len(self.tokens):
                break
            stmt = self.parse_stmt()
            if stmt is not None:
                stmts.append(stmt)
        prog = Program(stmts)
        if stmts:
            _pos(prog, stmts[0])
        return prog

    def parse_stmt(self):
        t = self.current()
        if t is None: return None
        if t.type == 'LET': return self.parse_let()
        if t.type == 'DEFINE': return self.parse_define()
        if t.type == 'RECORD': return self.parse_record()
        if t.type == 'SHOW': return self.parse_show()
        if t.type == 'WHEN': return self.parse_when()
        if t.type == 'REPEAT': return self.parse_repeat()
        if t.type == 'FOR': return self.parse_for()
        if t.type == 'TRY': return self.parse_try()
        if t.type == 'THROW': return self.parse_throw()
        if t.type == 'IMPORT': return self.parse_import()
        if t.type == 'FROM': return self.parse_from_import()
        if t.type == 'ASSERT': return self.parse_assert()
        if t.type == 'ROUTE': return self.parse_route()
        if t.type == 'SERVE': return self.parse_serve()
        if t.type == 'TOOL': return self.parse_tool()
        if t.type == 'AGENT': return self.parse_agent()
        return self.parse_expr_stmt()

    def parse_let(self):
        t = self.advance()  # let
        name_tok = self.expect('IDENT', hint="'let' must be followed by a variable name")
        name = name_tok.value
        self.check_snake_case(name, name_tok, "variable")
        self.expect('ASSIGN', hint="variable name must be followed by '='")
        value = self.parse_expr()
        return _pos(LetStmt(name, value), t)

    def parse_define(self):
        t = self.advance()  # define
        name_tok = self.expect('IDENT', hint="'define' must be followed by a function name")
        name = name_tok.value
        self.check_snake_case(name, name_tok, "function")
        params = []
        if self.current() and self.current().type == 'TAKING':
            self.advance()
            while self.current() and self.current().type == 'IDENT':
                p_tok = self.advance()
                self.check_snake_case(p_tok.value, p_tok, "parameter")
                params.append(p_tok.value)
                if self.current() and self.current().type == 'COMMA':
                    self.advance()
                else:
                    break
        self.expect('ARROW', hint="parameters must be followed by '->' then the function body")
        body = self.parse_expr()
        return _pos(DefineStmt(name, params, body), t)

    def parse_record(self):
        t = self.advance()  # record
        name_tok = self.expect('IDENT', hint="'record' must be followed by a record name")
        name = name_tok.value
        self.check_pascal_case(name, name_tok, "record")
        self.expect('LBRACE')
        fields = {}
        self.skip_newlines()
        while self.current() and self.current().type != 'RBRACE':
            fname_tok = self.expect('IDENT')
            self.check_snake_case(fname_tok.value, fname_tok, "field")
            self.expect('COLON')
            ftype = self.expect('IDENT').value
            fields[fname_tok.value] = ftype
            self.skip_newlines()
            if self.current() and self.current().type == 'COMMA':
                self.advance()
            self.skip_newlines()
        self.expect('RBRACE')
        return _pos(RecordDef(name, fields), t)

    def parse_show(self):
        t = self.advance()  # show
        self.expect('ARROW', hint="'show' must be followed by '->'")
        expr = self.parse_expr()
        return _pos(ShowStmt(expr), t)

    def parse_when(self):
        t = self.advance()  # when
        cond = self.parse_comparison()
        self.expect('ARROW', hint="'when' condition must be followed by '->'")
        then = self.parse_single_expr()
        otherwise = None
        saved = self.pos
        self.skip_newlines()
        if self.current() and self.current().type == 'OTHERWISE':
            self.advance()
            self.expect('ARROW')
            otherwise = self.parse_single_expr()
        else:
            self.pos = saved
        return _pos(WhenStmt(cond, then, otherwise), t)

    def parse_single_expr(self):
        t = self.current()
        if t and t.type == 'SHOW':
            return self.parse_show()
        return self.parse_expr()

    def parse_repeat(self):
        t = self.advance()  # repeat
        count = self.parse_primary()
        self.expect('TIMES', hint="count must be followed by 'times'")
        self.expect('ARROW')
        body = self.parse_expr()
        return _pos(RepeatStmt(count, body), t)

    def parse_for(self):
        t = self.advance()  # for
        var = self.expect('IDENT').value
        self.expect('IN', hint="variable name must be followed by 'in'")
        iterable = self.parse_primary()
        self.expect('ARROW')
        body = self.parse_expr()
        return _pos(ForStmt(var, iterable, body), t)

    def parse_try(self):
        t = self.advance()  # try
        body = self.parse_expr()
        self.skip_newlines()
        if not (self.current() and self.current().type == 'CATCH'):
            raise FeelError.syntax(self.current() or t,
                                   "'try' block requires a 'catch' clause",
                                   hint="syntax: try EXPR catch NAME -> EXPR",
                                   filename=self.filename, source=self.source)
        self.advance()  # catch
        err_name = self.expect('IDENT', hint="setelah 'catch' diharapkan nama variabel error").value
        self.expect('ARROW')
        handler = self.parse_expr()
        return _pos(TryStmt(body, err_name, handler), t)

    def parse_throw(self):
        t = self.advance()  # throw
        expr = self.parse_expr()
        return _pos(ThrowStmt(expr), t)

    def parse_import(self):
        t = self.advance()  # import
        name = self.expect('IDENT', hint="'import' must be followed by a module name").value
        expose = None
        if self.current() and self.current().type == 'EXPOSE':
            self.advance()
            expose = []
            while self.current() and self.current().type == 'IDENT':
                expose.append(self.advance().value)
                if self.current() and self.current().type == 'COMMA':
                    self.advance()
                else:
                    break
        return _pos(ImportStmt(name, expose=expose), t)

    def parse_from_import(self):
        t = self.advance()  # from
        name = self.expect('IDENT', hint="'from' must be followed by a module name").value
        self.expect('IMPORT', hint="module name must be followed by 'import'")
        names = []
        while self.current() and self.current().type == 'IDENT':
            names.append(self.advance().value)
            if self.current() and self.current().type == 'COMMA':
                self.advance()
            else:
                break
        return _pos(ImportStmt(name, expose=names), t)

    def parse_route(self):
        t = self.advance()  # route
        method_tok = self.current()
        if method_tok is None or method_tok.type not in self._HTTP_METHODS:
            raise FeelError.syntax(
                method_tok or t,
                "'route' must be followed by an HTTP method (GET, POST, PUT, PATCH, DELETE)",
                hint="example: route GET \"/path\" -> body",
                filename=self.filename, source=self.source)
        method = self.advance().value.upper()
        path_tok = self.expect('STRING', hint="HTTP method must be followed by a path string")
        path = path_tok.value
        self.expect('ARROW', hint="route path must be followed by '->' then handler body")
        handler = self.parse_expr()
        return _pos(RouteDecl(method, path, handler), t)

    def parse_serve(self):
        t = self.advance()  # serve
        self.expect('ON', hint="'serve' must be followed by 'on PORT'")
        port_tok = self.expect('NUMBER', hint="'on' must be followed by a port number")
        port = int(port_tok.value)
        return _pos(ServeStmt(port), t)

    def parse_tool(self):
        t = self.advance()  # tool
        name_tok = self.expect('IDENT', hint="'tool' must be followed by a tool name")
        name = name_tok.value
        self.check_snake_case(name, name_tok, "tool")
        desc_tok = self.expect('STRING', hint="tool name must be followed by a description string")
        description = desc_tok.value
        params = []
        if self.current() and self.current().type == 'TAKING':
            self.advance()
            while self.current() and self.current().type == 'IDENT':
                p_tok = self.advance()
                self.check_snake_case(p_tok.value, p_tok, "parameter")
                params.append(p_tok.value)
                if self.current() and self.current().type == 'COMMA':
                    self.advance()
                else:
                    break
        self.expect('ARROW', hint="tool parameters must be followed by '->' then the body")
        body = self.parse_expr()
        return _pos(ToolDecl(name, description, params, body), t)

    def parse_agent(self):
        t = self.advance()  # agent
        name_tok = self.expect('IDENT', hint="'agent' must be followed by an agent name")
        name = name_tok.value
        self.check_snake_case(name, name_tok, "agent")
        self.expect('LBRACE', hint="agent name must be followed by '{ system: ..., tools: ..., ... }'")
        config = {}
        self.skip_newlines()
        while self.current() and self.current().type != 'RBRACE':
            f_tok = self.current()
            if f_tok.type not in self._NAME_LIKE:
                raise FeelError.syntax(f_tok, "agent field name expected",
                                       filename=self.filename, source=self.source)
            self.advance()
            self.expect('COLON', hint="agent field name must be followed by ':'")
            value = self.parse_expr()
            config[f_tok.value] = value
            self.skip_newlines()
            if self.current() and self.current().type == 'COMMA':
                self.advance()
            self.skip_newlines()
        self.expect('RBRACE')
        return _pos(AgentDecl(name, config), t)

    def parse_respond(self):
        t = self.advance()  # respond
        status = 200
        body = None
        # Optional status (NUMBER literal)
        if self.current() and self.current().type == 'NUMBER':
            status = int(self.advance().value)
        # Optional body (any expression)
        if self.current() and self.current().type in self._EXPR_STARTERS:
            body = self.parse_expr()
        return _pos(RespondExpr(status, body), t)

    def parse_assert(self):
        t = self.advance()  # assert
        cond = self.parse_comparison()
        message = None
        if self.current() and self.current().type == 'COMMA':
            self.advance()
            message = self.parse_expr()
        return _pos(AssertStmt(cond, message), t)

    def parse_expr_stmt(self):
        return self.parse_expr()

    def parse_expr(self):
        left = self.parse_comparison()
        if self.current() and self.current().type == 'PIPE':
            t = self.current()
            steps = [left]
            while self.current() and self.current().type == 'PIPE':
                self.advance()
                # special-case: '| catch -> expr'
                if self.current() and self.current().type == 'CATCH':
                    ct = self.advance()
                    self.expect('ARROW', hint="'catch' in a pipeline must be followed by '-> default_value'")
                    handler = self.parse_call_or_ident()
                    steps.append(_pos(CatchStep(handler), ct))
                else:
                    steps.append(self.parse_call_or_ident())
            return _pos(Pipeline(steps), t)
        if self.current() and self.current().type == 'ARROW':
            t = self.advance()
            right = self.parse_expr()
            return _pos(ArrowExpr(right), t)
        return left

    def parse_comparison(self):
        left = self.parse_additive()
        ops = {'EQ': '==', 'NEQ': '!=', 'GT': '>', 'LT': '<', 'GTE': '>=', 'LTE': '<='}
        while self.current() and self.current().type in ops:
            t = self.current()
            op = ops[self.advance().type]
            right = self.parse_additive()
            left = _pos(BinOp(op, left, right), t)
        if self.current() and self.current().type == 'AND':
            t = self.advance()
            right = self.parse_comparison()
            return _pos(BinOp('and', left, right), t)
        if self.current() and self.current().type == 'OR':
            t = self.advance()
            right = self.parse_comparison()
            return _pos(BinOp('or', left, right), t)
        return left

    def parse_additive(self):
        left = self.parse_multiplicative()
        while self.current() and self.current().type in ('PLUS', 'MINUS'):
            t = self.current()
            op = self.advance().value
            right = self.parse_multiplicative()
            left = _pos(BinOp(op, left, right), t)
        return left

    def parse_multiplicative(self):
        left = self.parse_unary()
        while self.current() and self.current().type in ('STAR', 'SLASH'):
            t = self.current()
            op = self.advance().value
            right = self.parse_unary()
            left = _pos(BinOp(op, left, right), t)
        return left

    def parse_unary(self):
        if self.current() and self.current().type == 'NOT':
            t = self.advance()
            return _pos(UnaryOp('not', self.parse_unary()), t)
        if self.current() and self.current().type == 'MINUS':
            t = self.advance()
            return _pos(UnaryOp('-', self.parse_unary()), t)
        return self.parse_postfix()

    def parse_postfix(self):
        expr = self.parse_primary()
        while self.current() and self.current().type in ('DOT', 'LBRACKET', 'LPAREN'):
            t = self.current()
            if t.type == 'DOT':
                self.advance()
                field = self.expect_name(hint="'.' must be followed by a field name").value
                expr = _pos(FieldAccess(expr, field), t)
            elif t.type == 'LBRACKET':
                self.advance()
                index = self.parse_expr()
                self.expect('RBRACKET', hint="expected closing ']'")
                expr = _pos(IndexAccess(expr, index), t)
            else:  # LPAREN
                self.advance()
                args = []
                while self.current() and self.current().type != 'RPAREN':
                    args.append(self.parse_expr())
                    if self.current() and self.current().type == 'COMMA':
                        self.advance()
                self.expect('RPAREN', hint="expected closing ')' for function call")
                expr = _pos(CallExpr(expr, args), t)
        return expr

    def parse_call_or_ident(self):
        t = self.current()
        if t and t.type == 'SHOW':
            next_t = self.tokens[self.pos + 1] if self.pos + 1 < len(self.tokens) else None
            if next_t and next_t.type == 'ARROW':
                return self.parse_show()
        # Untuk pipeline step: gunakan postfix penuh supaya 'mod.fn', 'mod.fn(args)' work.
        return self.parse_postfix()

    def parse_primary(self):
        t = self.current()
        if t is None:
            last = self.tokens[-1] if self.tokens else None
            raise FeelError.syntax(last, "expression truncated at end of file",
                                   hint="complete the expression or remove trailing code",
                                   filename=self.filename, source=self.source)

        if t.type == 'NUMBER':
            self.advance()
            return _pos(Literal(t.value), t)

        if t.type == 'STRING':
            self.advance()
            return _pos(Literal(t.value), t)

        if t.type == 'TRUE':
            self.advance()
            return _pos(Literal(True), t)

        if t.type == 'FALSE':
            self.advance()
            return _pos(Literal(False), t)

        if t.type == 'NOTHING':
            self.advance()
            return _pos(Literal(None), t)

        if t.type == 'MAP':
            # Lookahead: kalau bukan diikuti LBRACE, perlakukan sebagai identifier biasa
            next_tok = None
            j = self.pos + 1
            while j < len(self.tokens) and self.tokens[j].type == 'NEWLINE':
                j += 1
            if j < len(self.tokens):
                next_tok = self.tokens[j]
            if not (next_tok and next_tok.type == 'LBRACE'):
                self.advance()
                return _pos(Ident('map'), t)
            self.advance()
            self.expect('LBRACE', hint="'map' must be followed by '{ key: value, ... }'")
            entries = []
            self.skip_newlines()
            while self.current() and self.current().type != 'RBRACE':
                # key can be identifier (or keyword used as name), or string literal
                k_tok = self.current()
                if k_tok.type in self._NAME_LIKE:
                    self.advance()
                    key_node = _pos(Literal(k_tok.value), k_tok)
                elif k_tok.type == 'STRING':
                    self.advance()
                    key_node = _pos(Literal(k_tok.value), k_tok)
                else:
                    raise FeelError.syntax(k_tok, "map key must be an identifier or string",
                                           filename=self.filename, source=self.source)
                self.expect('COLON', hint="map key must be followed by ':'")
                v = self.parse_expr()
                entries.append((key_node, v))
                self.skip_newlines()
                if self.current() and self.current().type == 'COMMA':
                    self.advance()
                self.skip_newlines()
            self.expect('RBRACE')
            return _pos(MapLiteral(entries), t)

        if t.type == 'LBRACKET':
            self.advance()
            items = []
            self.skip_newlines()
            while self.current() and self.current().type != 'RBRACKET':
                items.append(self.parse_expr())
                self.skip_newlines()
                if self.current() and self.current().type == 'COMMA':
                    self.advance()
                self.skip_newlines()
            self.expect('RBRACKET')
            return _pos(ListLiteral(items), t)

        if t.type == 'LPAREN':
            self.advance()
            expr = self.parse_expr()
            self.expect('RPAREN')
            return expr

        if t.type == 'SHOW':
            # 'show' bisa jadi:
            #   - statement form: 'show -> EXPR'
            #   - identifier (referensi fungsi show), mis. '... | show' atau passing show ke fungsi
            j = self.pos + 1
            while j < len(self.tokens) and self.tokens[j].type == 'NEWLINE':
                j += 1
            next_t = self.tokens[j] if j < len(self.tokens) else None
            if next_t and next_t.type == 'ARROW':
                self.advance()
                self.expect('ARROW')
                expr = self.parse_expr()
                return _pos(ShowStmt(expr), t)
            # treat as identifier 'show'
            self.advance()
            return _pos(Ident('show'), t)

        if t.type == 'THROW':
            self.advance()
            expr = self.parse_expr()
            return _pos(ThrowStmt(expr), t)

        if t.type == 'RESPOND':
            return self.parse_respond()

        if t.type == 'WHEN':
            # 'when COND -> THEN otherwise -> ELSE' sebagai ekspresi
            return self.parse_when()

        if t.type == 'DO':
            # 'do { stmt; stmt; expr }' block expression
            t_do = self.advance()
            self.expect('LBRACE', hint="'do' must be followed by '{'")
            stmts = []
            self.skip_newlines()
            while self.current() and self.current().type != 'RBRACE':
                stmts.append(self.parse_stmt())
                self.skip_newlines()
            self.expect('RBRACE', hint="expected closing '}' for do-block")
            return _pos(Block(stmts), t_do)

        if t.type == 'FN':
            # 'fn x, y -> expr' lambda
            t_fn = self.advance()
            params = []
            while self.current() and self.current().type == 'IDENT':
                p_tok = self.advance()
                self.check_snake_case(p_tok.value, p_tok, "parameter")
                params.append(p_tok.value)
                if self.current() and self.current().type == 'COMMA':
                    self.advance()
                else:
                    break
            self.expect('ARROW', hint="lambda parameters must be followed by '->' then the body")
            body = self.parse_expr()
            return _pos(Lambda(params, body), t_fn)

        if t.type == 'TRY':
            # 'try EXPR catch NAME -> EXPR' sebagai ekspresi
            t_try = self.advance()
            body = self.parse_expr()
            self.skip_newlines()
            if not (self.current() and self.current().type == 'CATCH'):
                raise FeelError.syntax(self.current() or t_try,
                                       "blok 'try' butuh 'catch' setelahnya",
                                       hint="format: try EXPR catch NAMA -> EXPR",
                                       filename=self.filename, source=self.source)
            self.advance()  # catch
            err_name = self.expect('IDENT', hint="'catch' must be followed by an error variable name").value
            self.expect('ARROW')
            handler = self.parse_expr()
            return _pos(TryStmt(body, err_name, handler), t_try)

        if t.type == 'IDENT':
            name_tok = self.advance()
            name = name_tok.value
            if self.current() and self.current().type == 'LPAREN':
                self.advance()
                args = []
                while self.current() and self.current().type != 'RPAREN':
                    args.append(self.parse_expr())
                    if self.current() and self.current().type == 'COMMA':
                        self.advance()
                self.expect('RPAREN')
                node = _pos(Call(name, args), name_tok)
                while self.current() and self.current().type in ('DOT', 'LBRACKET'):
                    tt = self.current()
                    if tt.type == 'DOT':
                        self.advance()
                        field = self.expect_name().value
                        node = _pos(FieldAccess(node, field), tt)
                    else:
                        self.advance()
                        idx = self.parse_expr()
                        self.expect('RBRACKET')
                        node = _pos(IndexAccess(node, idx), tt)
                return node
            if self.current() and self.current().type == 'LBRACE':
                return self.parse_record_literal(name_tok)
            return _pos(Ident(name), name_tok)

        raise FeelError.syntax(t, f"unexpected token: {t.type} ({t.value!r})",
                               hint="check for missing syntax before this point",
                               filename=self.filename, source=self.source)

    def parse_record_literal(self, name_tok):
        self.expect('LBRACE')
        fields = {}
        self.skip_newlines()
        while self.current() and self.current().type != 'RBRACE':
            fname = self.expect('IDENT').value
            self.expect('COLON')
            fval = self.parse_expr()
            fields[fname] = fval
            self.skip_newlines()
            if self.current() and self.current().type == 'COMMA':
                self.advance()
            self.skip_newlines()
        self.expect('RBRACE')
        return _pos(RecordLiteral(name_tok.value, fields), name_tok)


def parse(source, filename='<input>'):
    from errors import register_source
    register_source(filename, source)
    tokens = tokenize(source, filename=filename)
    return Parser(tokens, filename=filename, source=source).parse()
