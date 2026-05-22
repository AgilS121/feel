from lexer import tokenize

# AST Nodes
class Node:
    pass

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
    def __init__(self, cond, then, otherwise=None): self.cond = cond; self.then = then; self.otherwise = otherwise

class RepeatStmt(Node):
    def __init__(self, count, body): self.count = count; self.body = body

class ForStmt(Node):
    def __init__(self, var, iterable, body): self.var = var; self.iterable = iterable; self.body = body

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

class RecordLiteral(Node):
    def __init__(self, name, fields): self.name = name; self.fields = fields

class ListLiteral(Node):
    def __init__(self, items): self.items = items

class Ident(Node):
    def __init__(self, name): self.name = name

class Literal(Node):
    def __init__(self, value): self.value = value

class ArrowExpr(Node):
    def __init__(self, expr): self.expr = expr


class Parser:
    def __init__(self, tokens):
        self.tokens = [t for t in tokens if t.type != 'NEWLINE' or self._keep_newline(t, tokens)]
        self.tokens = tokens
        self.pos = 0

    def _keep_newline(self, t, tokens): return True

    def peek(self, offset=0):
        i = self.pos + offset
        while i < len(self.tokens) and self.tokens[i].type == 'NEWLINE':
            i += 1
        return self.tokens[i] if i < len(self.tokens) else None

    def advance(self):
        t = self.tokens[self.pos]
        self.pos += 1
        return t

    def skip_newlines(self):
        while self.pos < len(self.tokens) and self.tokens[self.pos].type == 'NEWLINE':
            self.pos += 1

    def expect(self, type_):
        self.skip_newlines()
        t = self.tokens[self.pos]
        if t.type != type_:
            raise SyntaxError(f"Line {t.line}: expected {type_}, got {t.type} ({t.value!r})")
        self.pos += 1
        return t

    def current(self):
        self.skip_newlines()
        if self.pos >= len(self.tokens): return None
        return self.tokens[self.pos]

    def parse(self):
        stmts = []
        while self.pos < len(self.tokens):
            self.skip_newlines()
            if self.pos >= len(self.tokens): break
            stmts.append(self.parse_stmt())
        return Program(stmts)

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
        return self.parse_expr_stmt()

    def parse_let(self):
        self.advance()  # let
        name = self.expect('IDENT').value
        self.expect('ASSIGN')
        value = self.parse_expr()
        return LetStmt(name, value)

    def parse_define(self):
        self.advance()  # define
        name = self.expect('IDENT').value
        params = []
        if self.current() and self.current().type == 'TAKING':
            self.advance()
            while self.current() and self.current().type == 'IDENT':
                params.append(self.advance().value)
                if self.current() and self.current().type == 'COMMA':
                    self.advance()
                else:
                    break
        self.expect('ARROW')
        body = self.parse_expr()
        return DefineStmt(name, params, body)

    def parse_record(self):
        self.advance()  # record
        name = self.expect('IDENT').value
        self.expect('LBRACE')
        fields = {}
        self.skip_newlines()
        while self.current() and self.current().type != 'RBRACE':
            fname = self.expect('IDENT').value
            self.expect('COLON')
            ftype = self.expect('IDENT').value
            fields[fname] = ftype
            self.skip_newlines()
            if self.current() and self.current().type == 'COMMA':
                self.advance()
            self.skip_newlines()
        self.expect('RBRACE')
        return RecordDef(name, fields)

    def parse_show(self):
        self.advance()  # show
        self.expect('ARROW')
        expr = self.parse_expr()
        return ShowStmt(expr)

    def parse_when(self):
        self.advance()  # when
        cond = self.parse_comparison()
        self.expect('ARROW')
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
        return WhenStmt(cond, then, otherwise)

    def parse_single_expr(self):
        t = self.current()
        if t and t.type == 'SHOW':
            return self.parse_show()
        return self.parse_expr()

    def parse_repeat(self):
        self.advance()  # repeat
        count = self.parse_primary()
        self.expect('TIMES')
        self.expect('ARROW')
        body = self.parse_expr()
        return RepeatStmt(count, body)

    def parse_for(self):
        self.advance()  # for
        var = self.expect('IDENT').value
        self.expect('IN')
        iterable = self.parse_primary()
        self.expect('ARROW')
        body = self.parse_expr()
        return ForStmt(var, iterable, body)

    def parse_expr_stmt(self):
        return self.parse_expr()

    def parse_expr(self):
        left = self.parse_comparison()
        if self.current() and self.current().type == 'PIPE':
            steps = [left]
            while self.current() and self.current().type == 'PIPE':
                self.advance()
                steps.append(self.parse_call_or_ident())
            return Pipeline(steps)
        if self.current() and self.current().type == 'ARROW':
            self.advance()
            right = self.parse_expr()
            return ArrowExpr(right)
        return left

    def parse_comparison(self):
        left = self.parse_additive()
        ops = {'EQ': '==', 'NEQ': '!=', 'GT': '>', 'LT': '<', 'GTE': '>=', 'LTE': '<='}
        while self.current() and self.current().type in ops:
            op = ops[self.advance().type]
            right = self.parse_additive()
            left = BinOp(op, left, right)
        if self.current() and self.current().type == 'AND':
            self.advance()
            right = self.parse_comparison()
            return BinOp('and', left, right)
        if self.current() and self.current().type == 'OR':
            self.advance()
            right = self.parse_comparison()
            return BinOp('or', left, right)
        return left

    def parse_additive(self):
        left = self.parse_multiplicative()
        while self.current() and self.current().type in ('PLUS', 'MINUS'):
            op = self.advance().value
            right = self.parse_multiplicative()
            left = BinOp(op, left, right)
        return left

    def parse_multiplicative(self):
        left = self.parse_unary()
        while self.current() and self.current().type in ('STAR', 'SLASH'):
            op = self.advance().value
            right = self.parse_unary()
            left = BinOp(op, left, right)
        return left

    def parse_unary(self):
        if self.current() and self.current().type == 'NOT':
            self.advance()
            return UnaryOp('not', self.parse_unary())
        if self.current() and self.current().type == 'MINUS':
            self.advance()
            return UnaryOp('-', self.parse_unary())
        return self.parse_postfix()

    def parse_postfix(self):
        expr = self.parse_primary()
        while self.current() and self.current().type == 'DOT':
            self.advance()
            field = self.expect('IDENT').value
            expr = FieldAccess(expr, field)
        return expr

    def parse_call_or_ident(self):
        t = self.current()
        if t and t.type == 'SHOW':
            # In pipeline context, bare 'show' is a function reference
            next_t = self.tokens[self.pos + 1] if self.pos + 1 < len(self.tokens) else None
            if next_t and next_t.type == 'ARROW':
                return self.parse_show()
            self.advance()
            return Ident('show')
        if t and t.type == 'IDENT':
            name = self.advance().value
            # check if next non-newline is LBRACE (record literal)
            if self.current() and self.current().type == 'LBRACE':
                return self.parse_record_literal(name)
            return Ident(name)
        return self.parse_primary()

    def parse_primary(self):
        t = self.current()
        if t is None:
            raise SyntaxError("Unexpected end of input")

        if t.type == 'NUMBER':
            self.advance()
            return Literal(t.value)

        if t.type == 'STRING':
            self.advance()
            return Literal(t.value)

        if t.type == 'TRUE':
            self.advance()
            return Literal(True)

        if t.type == 'FALSE':
            self.advance()
            return Literal(False)

        if t.type == 'NOTHING':
            self.advance()
            return Literal(None)

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
            return ListLiteral(items)

        if t.type == 'LPAREN':
            self.advance()
            expr = self.parse_expr()
            self.expect('RPAREN')
            return expr

        if t.type == 'SHOW':
            self.advance()
            self.expect('ARROW')
            expr = self.parse_expr()
            return ShowStmt(expr)

        if t.type == 'IDENT':
            name = self.advance().value
            # function call: name(args)
            if self.current() and self.current().type == 'LPAREN':
                self.advance()
                args = []
                while self.current() and self.current().type != 'RPAREN':
                    args.append(self.parse_expr())
                    if self.current() and self.current().type == 'COMMA':
                        self.advance()
                self.expect('RPAREN')
                node = Call(name, args)
                while self.current() and self.current().type == 'DOT':
                    self.advance()
                    field = self.expect('IDENT').value
                    node = FieldAccess(node, field)
                return node
            # record literal: Name { ... }
            if self.current() and self.current().type == 'LBRACE':
                return self.parse_record_literal(name)
            return Ident(name)

        raise SyntaxError(f"Line {t.line}: unexpected token {t.type} ({t.value!r})")

    def parse_record_literal(self, name):
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
        return RecordLiteral(name, fields)


def parse(source):
    tokens = tokenize(source)
    return Parser(tokens).parse()
