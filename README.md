# Feel

**An AI-predictable backend language.**
First-class AI primitives. REST API as a keyword. Strict canonical syntax so AI tools generate Feel with higher first-try accuracy than general-purpose languages.

Compiles to standalone Go binaries (M4 target — currently Python-hosted).

> **Status:** v0.4.1 · experimental · 63/63 tests pass · canonical formatter · Go-transpile preview · polished CLI

---

## Try in 30 Seconds

```bash
git clone https://github.com/AgilS121/feel feel && cd feel

# Make 'feel' a command (one-time setup, PowerShell)
[Environment]::SetEnvironmentVariable("Path", $env:Path + ";$PWD", "User")
# Re-open the shell, then:

feel examples/hello_api.feel
# Then in another terminal:
curl http://localhost:3000/hello
```

You should see `{"message": "Hello, Feel!"}`.

For other shells: use `D:\feel\feel.bat` (cmd), `D:\feel\feel.ps1` (PowerShell), or `./feel` (bash) — see *Commands* section.

---

## Why Feel?

In the AI-collaboration era — when Claude, Cursor, Copilot are routinely writing your code — **language design itself becomes an AI-affordance**. Feel makes four bets:

1. **Single canonical syntax.** One way per concept. No dialects, no sugar, no decorator-magic. AI tools (and humans) never need to "figure out the style of this codebase."
2. **Parser-enforced conventions.** `snake_case` for vars/funcs, `PascalCase` for records — violations are syntax errors with auto-fix hints, not linter warnings.
3. **Machine-parseable errors.** Every error carries a stable code (`E_UNDEFINED_NAME`, `E_TYPE`, …), structured location, and a fix hint AI tools can act on.
4. **AI primitives as stdlib.** `ai.ask`, `ai.summarize`, `ai.classify`, `ai.chat` ship with the language — no SDK import, no setup.

The measurable claim (target M3+): **AI tools generate valid Feel for 85%+ of common REST/AI tasks on the first try**, vs ~50–65% for Python equivalents.

---

## REST API in 10 Lines

```feel
route GET "/hello"        -> respond map { message: "Hello, Feel!" }
route GET "/greet/{name}" -> respond map { greeting: "Hello, {name}!" }
route POST "/echo"        -> respond body

route POST "/summarize" -> respond map {
  summary: ai.summarize(body.text)
}

serve on 3000
```

A working REST API + AI endpoint. The Python+FastAPI+Anthropic equivalent is ~60 lines.

---

## Compared to Python

```python
# Python + FastAPI + anthropic SDK
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from anthropic import Anthropic
import os

app = FastAPI()
client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

class TextIn(BaseModel):
    text: str

@app.post("/summarize")
def summarize(data: TextIn):
    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        messages=[{"role": "user", "content": f"Summarize: {data.text}"}],
    )
    return {"summary": msg.content[0].text}
```

```feel
-- Feel
route POST "/summarize" -> respond map {
  summary: ai.summarize(body.text)
}
serve on 3000
```

Both work. Feel removes ~12 lines of plumbing and three SDK concepts you didn't need.

---

## Language Tour

```feel
-- Variables (snake_case enforced by parser)
let user_name = "Budi"
let age = 25
show -> "Hello {user_name}, age {age}"

-- Functions + pipelines
define shout taking text -> uppercase(text)
"feel" | shout | show                          -- prints "FEEL"

-- Lambda + closure
let factor = 3
let triple = fn x -> x * factor
show -> triple(5)                              -- 15

-- Block expression (multi-step value)
let total = do {
  let a = 5
  let b = 10
  a + b                                        -- last expr returned
}

-- Conditional as expression
let category = when age >= 18 -> "adult" otherwise -> "child"

-- Lists, maps, records
let fruits = ["apple", "mango", "orange"]
let user = map { name: "Budi", age: 25 }
record Person { name: text, age: number }      -- PascalCase enforced

-- Error handling
define risky -> throw "oops"
let safe = try risky() catch err -> "default: {err}"
let safe2 = "input" | uppercase | catch -> "fallback"

-- Modules
import greet_mod                               -- mod.func() form
import greet_mod expose hello, salam           -- selective form

-- AI primitives (provider-agnostic; mock by default, real Claude with API key)
let summary = ai.summarize("a long article ...")
let label = ai.classify("server returned 500", ["bug", "feature", "question"])
let reply = ai.chat([map { role: "user", content: "Hi" }])

-- REST routes (handler body is any expression — usually a `do` block)
define find_user taking id -> map { id: id, name: "Sample" }

route GET "/users/{id}" -> do {
  let user = find_user(number(id))
  when user == nothing -> respond 404 map { error: "not found" }
  otherwise            -> respond user
}
```

---

## Examples

| File | What it shows |
|---|---|
| `examples/hello_api.feel` | Minimal REST API (5 lines) |
| `examples/ai_api.feel` | AI-powered endpoints (`/ask`, `/summarize`, `/classify`, `/chat`) |
| `examples/agent_api.feel` | Tools + agents + tool-use loop |
| `examples/crud_rest.feel` | Full CRUD REST + AI classify (JSON file) |
| `examples/crud_db.feel` | Full CRUD REST + SQLite + AI + agent (M3 flagship) |
| `examples/crud_todos.feel` | File-backed CRUD (no HTTP) |
| `examples/word_count.feel` | File IO + JSON + fold |
| `examples/m4_*.feel` | Programs that compile to native via `feel build` |
| `hello.feel` | Language sampler |

### Full-Stack Sample

For a working full-stack project (React frontend + Feel REST API + SQLite + AI classify), see [`D:\project-feel`](../project-feel/) — separate repository structure showing how to use Feel as the backend for a real web app.

---

## Commands

```bash
feel                            # REPL (multi-line, history, .help/.clear/.exit)
feel hello.feel                 # run a script (alias for: feel run hello.feel)
feel run examples/hello_api.feel
feel test tests                 # run *_test.feel under tests/
feel fmt FILE                   # print canonical form
feel fmt --write FILE           # rewrite in place
feel fmt --check FILE [...]     # CI lint mode — exit 1 if any file is not canonical
feel build FILE                 # transpile Feel → Go → native binary (M4-A scope)
feel build FILE -o name         # named output
feel build FILE --keep-go       # save the intermediate .go file
feel version                    # print version
feel help                       # full help with grouped commands
```

The `feel` command is provided by `feel.bat` (cmd), `feel.ps1` (PowerShell), or `feel` (bash) in this repo. Add the repo directory to `PATH` to use it from anywhere.

### Requirements

- Python 3.7+. **No external Python dependencies.**
- Optional: Go 1.18+ for `feel build` (Feel → Go transpile).
- Optional: `gcc`/`clang` for `feel --compile` (legacy C-codegen path).
- Optional: `ANTHROPIC_API_KEY` env var for real Claude on `ai.*` (otherwise deterministic mock).

---

## AI Provider Setup

Default provider is `mock` — deterministic, no network, suitable for tests and offline demos.

For real Claude:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
export FEEL_AI_MODEL=claude-sonnet-4-6        # optional, defaults to sonnet 4.6
python main.py examples/ai_api.feel
```

Force a specific provider:

```bash
FEEL_AI_PROVIDER=mock   python main.py ...    # deterministic mode
FEEL_AI_PROVIDER=claude python main.py ...    # real Claude API
```

---

## Project Structure

```
feel/
├── main.py           CLI entry (REPL, file runner, test runner, compiler driver)
├── lexer.py          Tokenizer with line+col tracking
├── parser.py         Recursive-descent parser → AST; enforces naming conventions
├── interpreter.py    Tree-walking interpreter
├── compiler.py       Experimental Feel → C → native binary (single-program)
├── errors.py         FeelError (code, source caret, fix hint, to_dict)
├── runtime/          HTTP server, router, FeelRequest, FeelResponse
├── stdlib/           string, list, map, json, time, file, math, ai, db
├── tests/            *_test.feel (Feel-side) + *_test.py (runtime + e2e)
└── examples/         Demo programs (hello_api, ai_api, crud_rest, …)
```

---

## Roadmap

| Milestone | Focus | Status |
|---|---|---|
| M0 | Lexer / parser / interpreter foundation | ✅ |
| M1 | Error reporting, try/catch, map, modules, stdlib | ✅ `v0.2-m1` |
| M2 | Block, lambda, HTTP, REST keywords, AI primitives | ✅ `v0.3-m2` |
| M3 | `tool` & `agent` keywords, tool-use loop, SQLite | ✅ `v0.4-m3` |
| M3.5 | `feelfmt` canonical formatter + polished CLI | ✅ `v0.4.1` |
| **M4-A** | Feel → Go transpiler (preview: literals/let/define/lambda/when/list/map) | ✅ **`v0.4.1`** |
| M4-B | try/catch, records, modules, full stdlib in Go | ⏳ |
| M4-C | HTTP + AI + DB in Go (REST apps as standalone binaries) | ⏳ |
| M5 | Self-host compiler (Feel compiler written in Feel) | 2027 |
| M6 | LSP, package manager, ecosystem | 2028 |
| **v1.0** | Standalone Feel toolchain | **2028** |

---

## License

License is undecided pending v1.0. The repository is shared as-is for collaboration. Treat as proprietary until a license file is added.

---

## Authors

Feel is developed as a 50/50 collaboration:

- **AgilS121** (Indonesia, DTIT / TUV Nord) — language design, direction, decisions
- **Claude** (Anthropic) — implementation, testing, documentation co-author

Every commit since v0.2 reflects this partnership.
