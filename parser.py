from lexer import tokenize
from errors import FeelError


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
            raise FeelError.syntax(last, f"diharapkan {type_}, tapi sudah end of file", hint=hint,
                                   filename=self.filename, source=self.source)
        t = self.tokens[self.pos]
        if t.type != type_:
            raise FeelError.syntax(t, f"diharapkan {type_}, tapi dapat {t.type} ({t.value!r})",
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
        'IMPORT', 'FROM', 'EXPOSE', 'ASSERT',
    }

    def expect_name(self, hint=None):
        """Terima IDENT atau keyword sebagai nama (untuk method/field access)."""
        self.skip_newlines()
        if self.pos >= len(self.tokens):
            last = self.tokens[-1] if self.tokens else None
            raise FeelError.syntax(last, "diharapkan nama field/method",
                                   hint=hint, filename=self.filename, source=self.source)
        t = self.tokens[self.pos]
        if t.type not in self._NAME_LIKE:
            raise FeelError.syntax(t, f"diharapkan nama field/method, dapat {t.type}",
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
        return self.parse_expr_stmt()

    def parse_let(self):
        t = self.advance()  # let
        name = self.expect('IDENT', hint="setelah 'let' diharapkan nama variabel").value
        self.expect('ASSIGN', hint="setelah nama variabel diharapkan '='")
        value = self.parse_expr()
        return _pos(LetStmt(name, value), t)

    def parse_define(self):
        t = self.advance()  # define
        name = self.expect('IDENT', hint="setelah 'define' diharapkan nama fungsi").value
        params = []
        if self.current() and self.current().type == 'TAKING':
            self.advance()
            while self.current() and self.current().type == 'IDENT':
                params.append(self.advance().value)
                if self.current() and self.current().type == 'COMMA':
                    self.advance()
                else:
                    break
        self.expect('ARROW', hint="setelah parameter diharapkan '->' lalu body fungsi")
        body = self.parse_expr()
        return _pos(DefineStmt(name, params, body), t)

    def parse_record(self):
        t = self.advance()  # record
        name = self.expect('IDENT', hint="setelah 'record' diharapkan nama record").value
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
        return _pos(RecordDef(name, fields), t)

    def parse_show(self):
        t = self.advance()  # show
        self.expect('ARROW', hint="setelah 'show' diharapkan '->'")
        expr = self.parse_expr()
        return _pos(ShowStmt(expr), t)

    def parse_when(self):
        t = self.advance()  # when
        cond = self.parse_comparison()
        self.expect('ARROW', hint="setelah kondisi 'when' diharapkan '->'")
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
        self.expect('TIMES', hint="setelah jumlah diharapkan 'times'")
        self.expect('ARROW')
        body = self.parse_expr()
        return _pos(RepeatStmt(count, body), t)

    def parse_for(self):
        t = self.advance()  # for
        var = self.expect('IDENT').value
        self.expect('IN', hint="setelah nama variabel diharapkan 'in'")
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
                                   "blok 'try' butuh 'catch' setelahnya",
                                   hint="format: try EXPR catch NAMA -> EXPR",
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
        name = self.expect('IDENT', hint="setelah 'import' diharapkan nama modul").value
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
        name = self.expect('IDENT', hint="setelah 'from' diharapkan nama modul").value
        self.expect('IMPORT', hint="setelah nama modul diharapkan 'import'")
        names = []
        while self.current() and self.current().type == 'IDENT':
            names.append(self.advance().value)
            if self.current() and self.current().type == 'COMMA':
                self.advance()
            else:
                break
        return _pos(ImportStmt(name, expose=names), t)

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
                    self.expect('ARROW', hint="setelah 'catch' di pipeline diharapkan '-> nilai_default'")
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
                field = self.expect_name(hint="setelah '.' diharapkan nama field").value
                expr = _pos(FieldAccess(expr, field), t)
            elif t.type == 'LBRACKET':
                self.advance()
                index = self.parse_expr()
                self.expect('RBRACKET', hint="diharapkan ']' penutup")
                expr = _pos(IndexAccess(expr, index), t)
            else:  # LPAREN
                self.advance()
                args = []
                while self.current() and self.current().type != 'RPAREN':
                    args.append(self.parse_expr())
                    if self.current() and self.current().type == 'COMMA':
                        self.advance()
                self.expect('RPAREN', hint="diharapkan ')' penutup pemanggilan fungsi")
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
            raise FeelError.syntax(last, "ekspresi terpotong di akhir file",
                                   hint="lengkapi ekspresi atau hapus sisa kode yang menggantung",
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
            self.expect('LBRACE', hint="setelah 'map' diharapkan '{ key: value, ... }'")
            entries = []
            self.skip_newlines()
            while self.current() and self.current().type != 'RBRACE':
                # key bisa IDENT atau STRING
                k_tok = self.current()
                if k_tok.type == 'IDENT':
                    self.advance()
                    key_node = _pos(Literal(k_tok.value), k_tok)
                elif k_tok.type == 'STRING':
                    self.advance()
                    key_node = _pos(Literal(k_tok.value), k_tok)
                else:
                    raise FeelError.syntax(k_tok, "key map harus identifier atau string",
                                           filename=self.filename, source=self.source)
                self.expect('COLON', hint="setelah key map diharapkan ':'")
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

        if t.type == 'WHEN':
            # 'when COND -> THEN otherwise -> ELSE' sebagai ekspresi
            return self.parse_when()

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
            err_name = self.expect('IDENT', hint="setelah 'catch' diharapkan nama variabel error").value
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

        raise FeelError.syntax(t, f"token tidak diharapkan: {t.type} ({t.value!r})",
                               hint="cek apakah ada syntax yang hilang sebelumnya",
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
