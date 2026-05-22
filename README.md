# Feel — code that flows

Bahasa pemrograman yang dirancang untuk developer Indonesia.
Syntax bening, pipeline-native, target backend & REST API.

**Versi: v0.2-m1** (eksperimental — syntax & semantik masih berubah)

## Quick Start

```bash
# REPL interaktif
python main.py

# Jalankan file
python main.py hello.feel

# Jalankan test suite
python main.py test tests

# Compile ke binary native (eksperimental)
python main.py --compile hello.feel
```

## Contoh Singkat

```feel
-- Variabel & interpolasi
let nama = "Budi"
let umur = 25
show -> "Halo {nama}, umur {umur}"

-- Fungsi & pipeline
define greet taking nama -> "Halo, {nama}!"
"feel" | greet | uppercase | show

-- Kondisi sebagai ekspresi
let kategori = when umur >= 18 -> "dewasa" otherwise -> "anak"

-- List & map
let buah = ["apel", "mangga", "jeruk"]
let user = map { name: "Budi", age: 25 }
show -> user["name"]

-- Record
record Person { name: text, age: number }
let p = Person { name: "Siti", age: 30 }

-- Error handling
let aman = try risky_call() catch err -> "default: {err}"
let nilai = data | parse | catch -> nothing
```

## Fitur (M1)

- **Bahasa inti**: let, define+taking, when/otherwise, repeat times, for in, record
- **Pipeline `|`** sebagai operator komposisi utama
- **Map type**: `map { k: v }` literal, akses `m["k"]`, modul `map.*`
- **Error handling**: `try EXPR catch err -> ...`, `throw "..."`, `| catch -> default`
- **Module**: `import nama` atau `import nama expose foo, bar`
- **Stdlib**: `string`, `list`, `map`, `json`, `time`, `file`, `math`
- **Error messages**: baris+kolom, source caret, saran perbaikan (Bahasa Indonesia)
- **REPL**: multi-line, history (readline)

## Struktur

```
feel/
├── main.py           # CLI entry (REPL, file runner, test runner, compiler)
├── lexer.py          # Tokenizer dengan tracking line+col
├── parser.py         # Recursive-descent parser → AST
├── interpreter.py    # Tree-walking interpreter
├── compiler.py       # Compiler eksperimental (Feel → C → binary)
├── errors.py         # FeelError + source-aware rendering
├── stdlib/           # Standard library: string, list, map, json, time, file, math
├── tests/            # Test suite (*_test.feel + assert)
└── examples/         # Contoh program (word_count, dll)
```

## Status & Roadmap

Posisi sekarang: **M1 — bahasa solid untuk script non-trivial**.

| Milestone | Fokus | Status |
|---|---|---|
| M0 | Lexer/parser/interpreter dasar, syntax inti | ✅ |
| M1 | Error reporting proper, try/catch, map, module, stdlib | ✅ |
| M2 | HTTP runtime + keyword REST (`route`, `respond`) | ⏳ |
| M3 | DB driver, query DSL pipeline | ⏳ |
| M4 | Middleware, env config, production polish | ⏳ |

Target M2: REST API "Hello World" dalam < 10 baris Feel.

## Lisensi

Belum ditentukan.
