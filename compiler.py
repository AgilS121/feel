"""
Feel -> C compiler.

Feel's type system maps to C as follows:
  number  -> double
  text    -> char* (heap-allocated via feel_strdup / feel_concat)
  bool    -> int (1/0)
  nothing -> NULL (void*)
  list    -> FeelList* (dynamic array)
  record  -> struct FeelRecord_<Name>*

We emit a single self-contained .c file with:
  1. A small runtime header (feel_runtime) inlined at the top
  2. Forward declarations for every Feel function
  3. Record struct typedefs
  4. Function definitions
  5. main() containing top-level statements
"""

import sys, os, re, subprocess, tempfile
sys.path.insert(0, os.path.dirname(__file__))

from parser import (parse, Program, LetStmt, DefineStmt, RecordDef,
                    ShowStmt, WhenStmt, RepeatStmt, ForStmt,
                    Pipeline, BinOp, UnaryOp, Call, FieldAccess,
                    RecordLiteral, ListLiteral, Ident, Literal, ArrowExpr)

# ---------------------------------------------------------------------------
# Runtime (inlined into every compiled Feel program)
# ---------------------------------------------------------------------------
RUNTIME = r"""
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>

/* ---------- Value type ---------- */
typedef enum { T_NUM, T_STR, T_BOOL, T_NIL, T_LIST, T_RECORD } FeelType;

typedef struct FeelVal FeelVal;
typedef struct FeelList FeelList;

struct FeelList {
    FeelVal *items;
    int len;
    int cap;
};

struct FeelVal {
    FeelType type;
    union {
        double   num;
        char    *str;
        int      boolean;
        FeelList *list;
        void    *record;
    };
};

/* Constructors */
static inline FeelVal feel_num(double n)  { FeelVal v; v.type=T_NUM;  v.num=n;      return v; }
static inline FeelVal feel_str(char *s)   { FeelVal v; v.type=T_STR;  v.str=s;      return v; }
static inline FeelVal feel_bool(int b)    { FeelVal v; v.type=T_BOOL; v.boolean=b;  return v; }
static inline FeelVal feel_nil()          { FeelVal v; v.type=T_NIL;  v.num=0;      return v; }
static inline FeelVal feel_list(FeelList *l){ FeelVal v; v.type=T_LIST; v.list=l;   return v; }

/* String helpers */
static char *feel_strdup(const char *s) {
    char *d = malloc(strlen(s)+1);
    strcpy(d, s);
    return d;
}
static char *feel_concat(const char *a, const char *b) {
    char *d = malloc(strlen(a)+strlen(b)+1);
    strcpy(d, a); strcat(d, b);
    return d;
}
static char *feel_numstr(double n) {
    char buf[64];
    if (n == (long long)n) snprintf(buf, sizeof(buf), "%lld", (long long)n);
    else                   snprintf(buf, sizeof(buf), "%g", n);
    return feel_strdup(buf);
}
static char *feel_boolstr(int b) { return feel_strdup(b ? "true" : "false"); }
static char *feel_valstr(FeelVal v) {
    switch(v.type) {
        case T_NUM:  return feel_numstr(v.num);
        case T_STR:  return feel_strdup(v.str);
        case T_BOOL: return feel_boolstr(v.boolean);
        case T_NIL:  return feel_strdup("nothing");
        default:     return feel_strdup("<value>");
    }
}

/* List helpers */
static FeelList *feel_list_new() {
    FeelList *l = malloc(sizeof(FeelList));
    l->cap = 4; l->len = 0;
    l->items = malloc(sizeof(FeelVal)*l->cap);
    return l;
}
static void feel_list_push(FeelList *l, FeelVal v) {
    if (l->len >= l->cap) {
        l->cap *= 2;
        l->items = realloc(l->items, sizeof(FeelVal)*l->cap);
    }
    l->items[l->len++] = v;
}
static FeelVal feel_show(FeelVal v) {
    char *s = feel_valstr(v);
    printf("%s\n", s);
    free(s);
    return v;
}
static FeelVal feel_uppercase(FeelVal v) {
    char *s = feel_strdup(v.str);
    for(int i=0;s[i];i++) if(s[i]>='a'&&s[i]<='z') s[i]-=32;
    return feel_str(s);
}
static FeelVal feel_lowercase(FeelVal v) {
    char *s = feel_strdup(v.str);
    for(int i=0;s[i];i++) if(s[i]>='A'&&s[i]<='Z') s[i]+=32;
    return feel_str(s);
}
static FeelVal feel_length(FeelVal v) {
    if(v.type==T_STR)  return feel_num(strlen(v.str));
    if(v.type==T_LIST) return feel_num(v.list->len);
    return feel_num(0);
}
static FeelVal feel_first(FeelVal v) {
    if(v.type==T_LIST && v.list->len>0) return v.list->items[0];
    return feel_nil();
}
static FeelVal feel_last(FeelVal v) {
    if(v.type==T_LIST && v.list->len>0) return v.list->items[v.list->len-1];
    return feel_nil();
}
static int feel_truthy(FeelVal v) {
    switch(v.type) {
        case T_BOOL: return v.boolean;
        case T_NIL:  return 0;
        case T_NUM:  return v.num != 0;
        case T_STR:  return v.str && v.str[0];
        default:     return 1;
    }
}
/* String interpolation: replaces {varname} with the variable's string value.
   Vars are passed as name/value pairs, terminated by NULL name. */
static char *feel_interpolate(const char *tmpl, int nv, char **names, FeelVal *vals) {
    char *result = feel_strdup("");
    const char *p = tmpl;
    while (*p) {
        if (*p == '{') {
            const char *end = strchr(p, '}');
            if (end) {
                int klen = (int)(end - p - 1);
                char key[256]; strncpy(key, p+1, klen); key[klen]=0;
                char *rep = NULL;
                for(int i=0;i<nv;i++) if(strcmp(names[i],key)==0){ rep=feel_valstr(vals[i]); break; }
                if (!rep) rep = feel_strdup(p);
                char *next = feel_concat(result, rep);
                free(result); free(rep); result = next;
                p = end+1; continue;
            }
        }
        char tmp[2]={*p,0};
        char *next = feel_concat(result, tmp);
        free(result); result=next; p++;
    }
    return result;
}
"""

# ---------------------------------------------------------------------------
# Compiler
# ---------------------------------------------------------------------------

class Compiler:
    def __init__(self):
        self.lines = []          # C source lines
        self.indent = 0
        self.tmp_counter = 0
        self.record_types = {}   # name -> {field: type}
        self.functions = []      # (name, params, body_ast)
        self.top_stmts = []      # top-level AST nodes (non-func/record)
        self.string_vars = {}    # track which vars are strings for interpolation

    # ------------------------------------------------------------------
    def emit(self, line=""):
        self.lines.append("    "*self.indent + line)

    def tmp(self, prefix="t"):
        self.tmp_counter += 1
        return f"_feel_{prefix}_{self.tmp_counter}"

    # ------------------------------------------------------------------
    def compile(self, source):
        tree = parse(source)

        # First pass: collect record defs and function defs
        for node in tree.stmts:
            if isinstance(node, RecordDef):
                self.record_types[node.name] = node.fields
            elif isinstance(node, DefineStmt):
                self.functions.append(node)
            else:
                self.top_stmts.append(node)

        out = []
        out.append(RUNTIME)

        # Emit record structs
        for name, fields in self.record_types.items():
            out.append(f"typedef struct {{")
            for fname, ftype in fields.items():
                out.append(f"    FeelVal {fname};")
            out.append(f"}} FeelRecord_{name};")
            out.append("")

        # Forward declare Feel functions
        for fn in self.functions:
            params = ", ".join(f"FeelVal {p}" for p in fn.params) or "void"
            out.append(f"FeelVal feel_fn_{fn.name}({params});")
        out.append("")

        # Emit function bodies
        for fn in self.functions:
            params = ", ".join(f"FeelVal {p}" for p in fn.params) or "void"
            out.append(f"FeelVal feel_fn_{fn.name}({params}) {{")
            self.indent = 1
            self.lines = []
            ret = self.compile_expr(fn.body)
            self.emit(f"return {ret};")
            out.extend(self.lines)
            out.append("}")
            out.append("")

        # Emit main
        out.append("int main(void) {")
        self.indent = 1
        self.lines = []
        for node in self.top_stmts:
            self.compile_stmt(node)
        self.emit("return 0;")
        out.extend(self.lines)
        out.append("}")
        out.append("")

        return "\n".join(out)

    # ------------------------------------------------------------------
    def compile_stmt(self, node):
        if isinstance(node, LetStmt):
            val = self.compile_expr(node.value)
            self.emit(f"FeelVal {node.name} = {val};")

        elif isinstance(node, ShowStmt):
            val = self.compile_expr(node.expr)
            self.emit(f"feel_show({val});")

        elif isinstance(node, WhenStmt):
            cond = self.compile_expr(node.cond)
            self.emit(f"if (feel_truthy({cond})) {{")
            self.indent += 1
            self.compile_stmt_or_expr(node.then)
            self.indent -= 1
            if node.otherwise:
                self.emit("} else {")
                self.indent += 1
                self.compile_stmt_or_expr(node.otherwise)
                self.indent -= 1
            self.emit("}")

        elif isinstance(node, RepeatStmt):
            count = self.compile_expr(node.count)
            idx = self.tmp("i")
            self.emit(f"for (int {idx}=0; {idx}<(int)({count}).num; {idx}++) {{")
            self.indent += 1
            self.compile_stmt_or_expr(node.body)
            self.indent -= 1
            self.emit("}")

        elif isinstance(node, ForStmt):
            lst = self.compile_expr(node.iterable)
            lst_var = self.tmp("lst")
            idx = self.tmp("i")
            self.emit(f"FeelVal {lst_var} = {lst};")
            self.emit(f"for (int {idx}=0; {idx}<{lst_var}.list->len; {idx}++) {{")
            self.indent += 1
            self.emit(f"FeelVal {node.var} = {lst_var}.list->items[{idx}];")
            self.compile_stmt_or_expr(node.body)
            self.indent -= 1
            self.emit("}")

        else:
            # expression statement
            val = self.compile_expr(node)
            self.emit(f"{val};")

    def compile_stmt_or_expr(self, node):
        from parser import ShowStmt, WhenStmt, RepeatStmt, ForStmt, LetStmt
        if isinstance(node, (ShowStmt, WhenStmt, RepeatStmt, ForStmt, LetStmt)):
            self.compile_stmt(node)
        else:
            val = self.compile_expr(node)
            self.emit(f"{val};")

    # ------------------------------------------------------------------
    def compile_expr(self, node):
        if isinstance(node, Literal):
            if node.value is None:    return "feel_nil()"
            if node.value is True:    return "feel_bool(1)"
            if node.value is False:   return "feel_bool(0)"
            if isinstance(node.value, (int, float)):
                return f"feel_num({float(node.value)})"
            if isinstance(node.value, str):
                return self.compile_string(node.value)

        if isinstance(node, Ident):
            name = node.name
            builtins = {
                'show': 'feel_show', 'uppercase': 'feel_uppercase',
                'lowercase': 'feel_lowercase', 'length': 'feel_length',
                'first': 'feel_first', 'last': 'feel_last',
            }
            if name in builtins:
                # Return as function pointer wrapped in a macro call — 
                # for pipeline use we call directly
                return f"/* builtin:{name} */"
            return name

        if isinstance(node, ArrowExpr):
            return self.compile_expr(node.expr)

        if isinstance(node, ShowStmt):
            val = self.compile_expr(node.expr)
            tmp = self.tmp("show")
            self.emit(f"FeelVal {tmp} = feel_show({val});")
            return tmp

        if isinstance(node, BinOp):
            return self.compile_binop(node)

        if isinstance(node, UnaryOp):
            val = self.compile_expr(node.expr)
            if node.op == '-':  return f"feel_num(-({val}).num)"
            if node.op == 'not': return f"feel_bool(!feel_truthy({val}))"

        if isinstance(node, Call):
            return self.compile_call(node)

        if isinstance(node, Pipeline):
            return self.compile_pipeline(node)

        if isinstance(node, FieldAccess):
            obj = self.compile_expr(node.obj)
            tmp = self.tmp("rec")
            # We stored record pointer in .record field
            self.emit(f"FeelVal {tmp};")
            # Determine record type from obj — we use a generic accessor
            # stored as FeelRecord_X in .record
            self.emit(f"{{ void *_r = ({obj}).record; /* field {node.field} */")
            # We can't know the exact type at compile time without type inference,
            # so we use an offset trick — store fields as FeelVal array
            # Instead use the simpler struct approach: cast based on context
            # For now emit a comment and a nil — full type inference is future work
            self.emit(f"  {tmp} = feel_nil(); /* TODO: field access {node.field} */ }}")
            return tmp

        if isinstance(node, RecordLiteral):
            return self.compile_record_literal(node)

        if isinstance(node, ListLiteral):
            return self.compile_list(node)

        if isinstance(node, WhenStmt):
            # inline when as ternary-ish
            tmp = self.tmp("when")
            self.emit(f"FeelVal {tmp};")
            cond = self.compile_expr(node.cond)
            self.emit(f"if (feel_truthy({cond})) {{")
            self.indent += 1
            val = self.compile_expr(node.then)
            self.emit(f"{tmp} = {val};")
            self.indent -= 1
            if node.otherwise:
                self.emit("} else {")
                self.indent += 1
                val2 = self.compile_expr(node.otherwise)
                self.emit(f"{tmp} = {val2};")
                self.indent -= 1
            else:
                self.emit("} else {")
                self.emit(f"    {tmp} = feel_nil();")
            self.emit("}")
            return tmp

        return "feel_nil() /* unhandled */"

    # ------------------------------------------------------------------
    def compile_string(self, s):
        # Check for interpolation
        if '{' in s and '}' in s:
            # Extract variable names
            vars_in = re.findall(r'\{(\w+)\}', s)
            if vars_in:
                # Build interpolation call
                escaped = s.replace('\\', '\\\\').replace('"', '\\"')
                nv = len(vars_in)
                names_arr = self.tmp("names")
                vals_arr  = self.tmp("vals")
                result    = self.tmp("istr")
                self.emit(f'char *{names_arr}[] = {{{", ".join(chr(34)+v+chr(34) for v in vars_in)}}};')
                self.emit(f'FeelVal {vals_arr}[] = {{{", ".join(v for v in vars_in)}}};')
                self.emit(f'char *{result} = feel_interpolate("{escaped}", {nv}, {names_arr}, {vals_arr});')
                return f"feel_str({result})"
        escaped = s.replace('\\', '\\\\').replace('"', '\\"')
        return f'feel_str(feel_strdup("{escaped}"))'

    # ------------------------------------------------------------------
    def compile_binop(self, node):
        l = self.compile_expr(node.left)
        r = self.compile_expr(node.right)
        op = node.op
        if op == '+':
            tmp = self.tmp("add")
            self.emit(f"FeelVal {tmp};")
            self.emit(f"if (({l}).type==T_STR || ({r}).type==T_STR) {{")
            self.emit(f"    {tmp} = feel_str(feel_concat(feel_valstr({l}), feel_valstr({r})));")
            self.emit(f"}} else {{ {tmp} = feel_num(({l}).num + ({r}).num); }}")
            return tmp
        ops = {'-': '-', '*': '*', '/': '/'}
        cmp = {'==': '==', '!=': '!=', '>': '>', '<': '<', '>=': '>=', '<=': '<='}
        if op in ops:
            return f"feel_num(({l}).num {ops[op]} ({r}).num)"
        if op in cmp:
            # numeric comparison
            return f"feel_bool(({l}).num {cmp[op]} ({r}).num)"
        if op == 'and': return f"feel_bool(feel_truthy({l}) && feel_truthy({r}))"
        if op == 'or':  return f"feel_bool(feel_truthy({l}) || feel_truthy({r}))"
        return "feel_nil()"

    # ------------------------------------------------------------------
    def compile_call(self, node):
        builtin_map = {
            'show':      lambda args: f"feel_show({args[0]})",
            'uppercase': lambda args: f"feel_uppercase({args[0]})",
            'lowercase': lambda args: f"feel_lowercase({args[0]})",
            'length':    lambda args: f"feel_length({args[0]})",
            'first':     lambda args: f"feel_first({args[0]})",
            'last':      lambda args: f"feel_last({args[0]})",
            'text':      lambda args: f"feel_str(feel_valstr({args[0]}))",
            'number':    lambda args: f"feel_num(atof(feel_valstr({args[0]})))",
            'round':     lambda args: f"feel_num(round(({args[0]}).num))",
            'abs':       lambda args: f"feel_num(fabs(({args[0]}).num))",
        }
        args = [self.compile_expr(a) for a in node.args]
        if node.name in builtin_map:
            return builtin_map[node.name](args)
        # User-defined function
        return f"feel_fn_{node.name}({', '.join(args)})"

    # ------------------------------------------------------------------
    def compile_pipeline(self, node):
        val = self.compile_expr(node.steps[0])
        for step in node.steps[1:]:
            tmp = self.tmp("pipe")
            fn_name = self._pipeline_fn_name(step)
            if fn_name:
                self.emit(f"FeelVal {tmp} = {fn_name}({val});")
            else:
                fn_val = self.compile_expr(step)
                self.emit(f"FeelVal {tmp} = {fn_val}; /* pipeline step */")
            val = tmp
        return val

    def _pipeline_fn_name(self, node):
        builtin_map = {
            'show': 'feel_show', 'uppercase': 'feel_uppercase',
            'lowercase': 'feel_lowercase', 'length': 'feel_length',
            'first': 'feel_first', 'last': 'feel_last',
        }
        if isinstance(node, Ident):
            name = node.name
            if name in builtin_map: return builtin_map[name]
            return f"feel_fn_{name}"
        return None

    # ------------------------------------------------------------------
    def compile_record_literal(self, node):
        if node.name not in self.record_types:
            return "feel_nil() /* unknown record */"
        tmp = self.tmp("rec")
        self.emit(f"FeelRecord_{node.name} *{tmp} = malloc(sizeof(FeelRecord_{node.name}));")
        for fname, fexpr in node.fields.items():
            val = self.compile_expr(fexpr)
            self.emit(f"{tmp}->{fname} = {val};")
        # Wrap in FeelVal
        wrap = self.tmp("recval")
        self.emit(f"FeelVal {wrap}; {wrap}.type=T_RECORD; {wrap}.record={tmp};")
        return wrap

    # ------------------------------------------------------------------
    def compile_list(self, node):
        lst = self.tmp("lst")
        self.emit(f"FeelList *{lst} = feel_list_new();")
        for item in node.items:
            val = self.compile_expr(item)
            self.emit(f"feel_list_push({lst}, {val});")
        wrap = self.tmp("lval")
        self.emit(f"FeelVal {wrap} = feel_list({lst});")
        return wrap


# ---------------------------------------------------------------------------
# CLI helper
# ---------------------------------------------------------------------------

def compile_file(feel_path, out_path=None, keep_c=False):
    with open(feel_path) as f:
        source = f.read()

    c_source = Compiler().compile(source)

    base = feel_path.rsplit('.', 1)[0]
    c_path = base + ".c"
    if out_path is None:
        out_path = base

    with open(c_path, 'w') as f:
        f.write(c_source)

    print(f"[feel] C source written to {c_path}")

    result = subprocess.run(
        ['gcc', '-O2', '-o', out_path, c_path, '-lm'],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print("[feel] gcc error:")
        print(result.stderr)
        return False

    if not keep_c:
        os.remove(c_path)

    print(f"[feel] compiled -> {out_path}")
    return True
