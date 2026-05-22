# Feel — code that flows

**An AI-predictable backend language.**
Designed for human + AI collaboration: strict canonical syntax, first-class AI primitives, REST API as a built-in concern. Compiles to standalone Go binaries (M4 target).

**Version: v0.3-m2** (experimental — syntax & semantics still evolving)

## Why Feel?

In the AI-collaboration era (Claude / Cursor / Copilot writing code), language design matters in a new way. Feel optimizes for **AI first-try accuracy**:

- **Single canonical syntax** — one way per concept, no dialects, no sugar
- **Naming conventions enforced by the parser** — `snake_case` for vars/funcs, `PascalCase` for records, machine-fixable error messages
- **Machine-parseable errors** — every error has a code (e.g. `E_UNDEFINED_NAME`), structured location, fix hint
- **First-class AI primitives** — `ai.ask`, `ai.summarize`, `ai.classify`, `ai.chat` in stdlib
- **REST API native** — `route`, `respond`, `serve` are keywords, not framework imports

The bet: AI tools generate Feel with materially higher first-try accuracy than Python or general-purpose languages — a measurable differentiator (target M2: 85%+).

## REST API in 10 Lines

```feel
route GET "/hello"        -> respond map { message: "Hello, Feel!" }
route GET "/greet/{name}" -> respond map { greeting: "Halo, {name}!" }
route POST "/echo"        -> respond body

route POST "/summarize" -> respond map {
  summary: ai.summarize(body.text)
}

serve on 3000
```

That's a working REST API with an AI endpoint.

## Quick Start

```bash
# REPL
python main.py

# Run a file
python main.py hello.feel
python main.py examples/hello_api.feel        # serves on :3000

# Test suite
python main.py test tests

# Native binary (experimental, via C-codegen)
python main.py --compile hello.feel
```

## Language Tour

```feel
-- Variables (snake_case enforced)
let user_name = "Budi"
let age = 25
show -> "Halo {user_name}, age {age}"

-- Functions, pipelines
define greet taking name -> "Halo, {name}!"
"feel" | greet | uppercase | show

-- Lambda (closure)
let multiplier = 3
let triple = fn x -> x * multiplier
show -> triple(5)                              -- 15

-- Block expression (multi-step)
let result = do {
  let a = 5
  let b = 10
  a + b                                        -- last expr returned
}

-- Conditional as expression
let category = when age >= 18 -> "adult" otherwise -> "child"

-- Lists, maps, records
let fruits = ["apel", "mangga", "jeruk"]
let user = map { name: "Budi", age: 25 }
record Person { name: text, age: number }       -- PascalCase enforced

-- Error handling
let safe = try risky_call() catch err -> "default: {err}"
let safe2 = data | parse | catch -> nothing     -- pipeline catch

-- Modules
import greet_mod
import greet_mod expose hello, salam

-- AI primitives
let summary = ai.summarize(article)
let label = ai.classify(text, ["bug", "feature", "question"])
let reply = ai.chat([map { role: "user", content: "Hi" }])

-- REST routes
route GET "/users/{id}" -> do {
  let user = find_user(id)
  when user == nothing -> respond 404 map { error: "not found" }
  otherwise -> respond user
}

serve on 3000
```

## Examples

| File | What it shows |
|---|---|
| `examples/hello_api.feel` | Minimal REST API in 5 lines |
| `examples/ai_api.feel` | AI-powered endpoints (summarize / classify / chat) |
| `examples/crud_rest.feel` | Full CRUD REST API with AI classify |
| `examples/crud_todos.feel` | File-backed CRUD (no HTTP) |
| `examples/word_count.feel` | File IO + JSON + fold |

## Project Structure

```
feel/
├── main.py           # CLI (REPL, file runner, test runner, compiler)
├── lexer.py          # Tokenizer (line + col tracking)
├── parser.py         # Recursive-descent parser → AST
├── interpreter.py    # Tree-walking interpreter
├── compiler.py       # Experimental compiler (Feel → C → binary)
├── errors.py         # FeelError with code + source rendering
├── runtime/          # HTTP server, router, FeelRequest/FeelResponse
├── stdlib/           # string, list, map, json, time, file, math, ai
├── tests/            # Test suite (Feel + Python e2e)
└── examples/         # Demo programs
```

## AI Provider Setup

Default provider is `mock` (deterministic, no network). For real Claude:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
export FEEL_AI_MODEL=claude-sonnet-4-6        # optional
python main.py examples/ai_api.feel
```

Switch providers explicitly:

```bash
FEEL_AI_PROVIDER=mock python main.py ...      # deterministic
FEEL_AI_PROVIDER=claude python main.py ...    # real Claude
```

## Roadmap

| Milestone | Focus | Status |
|---|---|---|
| M0 | Lexer/parser/interpreter foundation | ✅ |
| M1 | Error reporting, try/catch, map, modules, stdlib | ✅ (v0.2-m1) |
| **M2** | Block + lambda + HTTP + REST + AI primitives | **✅ (v0.3-m2)** |
| M3 | Agent/tool keywords, DB driver, query DSL, feelfmt | ⏳ |
| M4 | Feel → Go transpiler; drop Python at user runtime | ⏳ |
| M5 | Self-host compiler (Feel compiler written in Feel) | ⏳ |
| M6 | LSP, package manager, ecosystem | ⏳ |
| v1.0 | Standalone Feel toolchain, target 2028 | ⏳ |

## License

TBD.
