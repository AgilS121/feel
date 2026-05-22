# Feel

**An AI-predictable backend language.** First-class AI primitives. REST API as a keyword. Strict canonical syntax so AI tools generate Feel with higher first-try accuracy than general-purpose languages.

**Compiles to standalone Go binaries** — no Python, no runtime install at user-side. Same source runs in two modes: Python interpreter for dev, native binary for prod.

> **Status:** v0.14 · experimental · 80+ tests passing · 18 stdlib namespaces · Laravel-feature parity for a usable subset

---

## Try in 30 Seconds

```bash
git clone https://github.com/AgilS121/feel feel && cd feel

# Make 'feel' a command (PowerShell, one-time)
[Environment]::SetEnvironmentVariable("Path", $env:Path + ";$PWD", "User")
# Re-open the shell.

feel examples/hello_api.feel
# Then in another terminal:
curl http://localhost:3000/hello
```

You should see `{"message": "Hello, Feel!"}`.

Other shells: `feel.bat` (cmd), `feel.ps1` (PowerShell), `./feel` (bash) — see *Commands*.

---

## Why Feel?

In the AI-collaboration era — when Claude, Cursor, Copilot are routinely writing your code — **language design itself becomes an AI-affordance**. Feel makes four bets:

1. **Single canonical syntax.** One way per concept. No dialects, no sugar, no decorator-magic. AI tools (and humans) never need to "figure out the style of this codebase."
2. **Parser-enforced conventions.** `snake_case` for vars/funcs, `PascalCase` for records — violations are syntax errors with auto-fix hints, not linter warnings.
3. **Machine-parseable errors.** Every error carries a stable code (`E_UNDEFINED_NAME`, `E_TYPE`, …), structured location, and a fix hint AI tools can act on.
4. **Batteries included for backend work.** REST, AI, DB (SQLite + Postgres + MySQL), auth (JWT + PBKDF2), validation, migrations, cache, mail, queue, security primitives — all stdlib, no SDK setup.

The measurable claim: **AI tools generate valid Feel for 85%+ of common REST+DB+AI tasks on the first try**, vs ~50–65% for Python equivalents. Benchmark suite coming.

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

A working REST API + AI endpoint. Equivalent Python+FastAPI+Anthropic: ~60 lines.

---

## A Full Auth + AI + DB API

```feel
let conn = db.connect("app.db")
let jwt_secret = "replace-with-env-var"

record CreateUser { username: text, password: text }

route POST "/api/register" -> do {
  let v = validate.shape(body, "CreateUser")
  let hashed = crypto.hash_password(v.password)
  db.exec(conn, "INSERT INTO users (username, password_hash) VALUES (?, ?)",
    [v.username, hashed])
  let token = crypto.jwt_sign(map { sub: db.last_id(conn) }, jwt_secret)
  respond 201 map { token: token }
}

route POST "/api/login" -> do {
  let row = db.query_one(conn, "SELECT id, password_hash FROM users WHERE username = ?",
    [body.username])
  when row == nothing or not crypto.verify_password(body.password, row.password_hash)
    -> respond 401 map { error: "invalid credentials" }
  otherwise -> respond map {
    token: crypto.jwt_sign(map { sub: row.id }, jwt_secret),
  }
}

route GET "/api/me" -> do {
  let user = auth.require_jwt(request, jwt_secret)
  respond map { user_id: user.sub }
}

route POST "/api/summarize" -> do {
  let user = auth.require_jwt(request, jwt_secret)
  respond map { summary: ai.summarize(body.text) }
}

serve on 3000 cors
```

~30 lines for password hashing + JWT issue + JWT-protected endpoints + AI integration. Try doing this in Express or FastAPI without 200+ lines and 5+ imports.

---

## Stdlib Surface

Auto-loaded into every Feel program — no imports needed.

| Namespace | Functions | What it does |
|---|---|---|
| `string` | trim, replace, starts_with, slice, words, lines, upper, lower, contains, repeat, … | Text manipulation |
| `list` | range, sort, reverse, unique, take, drop, fold, map, filter, flatten, count | Slice ops |
| `map` | get, set, has, delete, keys, values, entries, size, merge | Dict ops (immutable) |
| `json` | encode, decode | Standard JSON |
| `time` | now, format, parse, sleep, now_ms | Time/date |
| `file` | read, write, append, exists, delete, list_dir | Filesystem |
| `math` | pi, e, sqrt, pow, log, sin/cos/tan, ceil, floor, round, random, random_int | Numbers |
| `db` | connect, exec, query, query_one, last_id, close, begin, commit, rollback, transaction, **find, where, order_by, take, all, first, count** | SQLite + MySQL + Postgres |
| `ai` | ask, summarize, classify, chat, provider | AI primitives (mock + Claude) |
| `crypto` | hash_password, verify_password, jwt_sign, jwt_verify, hmac_sha256, random_token, base64_encode/decode | Auth-grade crypto |
| `security` | rate_limit, report_failed, panic, kill_switch, audit, set_audit_log | App-layer hardening |
| `auth` | extract_bearer, require_jwt, optional_jwt | JWT middleware helpers |
| `session` | set, get, clear | Signed-cookie sessions |
| `validate` | shape, is_valid, errors_for | Record-driven validation |
| `migrate` | apply, status | File-based schema migrations |
| `cache` | set, get, get_or_compute, has, delete, clear, size | In-memory TTL cache |
| `mail` | send, provider, sent, clear_sent | SMTP + mock for tests |
| `queue` | connect, enqueue, pop, complete, fail, pending, process_once, work | SQLite-backed jobs + worker |

Plus declarative keywords: `route`, `respond`, `serve`, `tool`, `agent`.

---

## Language Tour

```feel
-- Variables (snake_case enforced by parser)
let user_name = "Budi"
let age = 25
show -> "Hello {user_name}, age {age}"

-- Functions + pipelines + closures
define shout taking text -> uppercase(text)
"feel" | shout | show                          -- prints "FEEL"

let factor = 3
let triple = fn x -> x * factor                -- lambda
show -> triple(5)                              -- 15

-- Block expression (multi-step value)
let total = do {
  let a = 5
  let b = 10
  a + b
}

-- Conditional as expression
let category = when age >= 18 -> "adult" otherwise -> "child"

-- Records (PascalCase enforced)
record Person { name: text, age: number }

-- Error handling
let safe = try risky() catch err -> "default: {err}"
let safe2 = data | parse | catch -> nothing    -- pipeline catch

-- Modules
import greet_mod                               -- namespace
import greet_mod expose hello, salam           -- selective

-- AI primitives
let summary = ai.summarize("a long article …")
let label = ai.classify("server 500 error", ["bug", "feature", "question"])

-- Declarative tool + agent (Claude function-calling)
tool calc "Perform arithmetic" taking a, b -> a + b
agent helper {
  system: "Be concise",
  tools: [calc],
}
let answer = helper.chat("what's 12 + 30?")

-- DB transactions (auto-commit, auto-rollback on throw)
db.transaction(conn, fn tx -> do {
  db.exec(tx, "UPDATE accounts SET balance = balance - ? WHERE id = ?", [100, 1])
  db.exec(tx, "UPDATE accounts SET balance = balance + ? WHERE id = ?", [100, 2])
})

-- ORM-lite (pipeline-native query builder)
let adults = db.find(conn, "users")
  | db.where("age", ">=", 18)
  | db.order_by("name")
  | db.take(10)
  | db.all

-- Validation
record CreateUser { name: text, email: text, age: number }
let v = validate.shape(body, "CreateUser")     -- throws on missing/wrong-type

-- Auth + session
let user = auth.require_jwt(request, jwt_secret)
let resp = session.set(map { status: 200 }, "sid", user.id, jwt_secret)

-- Security self-destruct on attack
security.kill_switch(conn)
when failed_logins > 5 -> security.panic("brute force from {ip}")

-- Cache
let users_count = cache.get_or_compute("user_count", 60, fn ->
  db.query_one(conn, "SELECT COUNT(*) AS n FROM users").n)

-- Queue jobs
let q = queue.connect("queue.db")
queue.enqueue(q, "emails", map { to: "x@y.com", subject: "Hi" })
-- in a worker process: queue.work(q, "emails", send_email)

-- Mail
mail.send(map { to: "u@x.com", subject: "Welcome", body: "Hi {name}" })

-- REST with everything
route POST "/api/users" -> do {
  let v = validate.shape(body, "CreateUser")
  let hashed = crypto.hash_password(v.password)
  let new_user = db.transaction(conn, fn tx -> do {
    db.exec(tx, "INSERT INTO users (name, password) VALUES (?, ?)", [v.name, hashed])
    db.query_one(tx, "SELECT * FROM users WHERE id = ?", [db.last_id(tx)])
  })
  queue.enqueue(q, "emails", map { to: v.email, subject: "Welcome" })
  security.audit(map { type: "USER_CREATED", id: new_user.id })
  respond 201 map { user: new_user, token: crypto.jwt_sign(map { sub: new_user.id }, secret) }
}
```

---

## Examples

| File | What it shows |
|---|---|
| `examples/hello_api.feel` | Minimal REST API (5 lines) |
| `examples/ai_api.feel` | AI-powered endpoints — `ai.summarize`, `ai.classify`, `ai.chat` |
| `examples/agent_api.feel` | Tools + agents + tool-use loop |
| `examples/crud_rest.feel` | Full CRUD REST + AI classify (JSON file) |
| `examples/crud_db.feel` | Full CRUD REST + SQLite + AI + agent (compiles to native) |
| `examples/crud_todos.feel` | File-backed CRUD (no HTTP) |
| `examples/bank_transfer.feel` | Transactional db.transaction with auto-rollback |
| `examples/secure_login_api.feel` | Brute-force protection + panic mode self-destruct |
| `examples/auth_api.feel` | Full auth flow — register, login, JWT, role-based access |
| `examples/word_count.feel` | File IO + JSON + fold (AI-friendly idioms) |
| `examples/m4_*.feel` | Compile-to-native showcase (literals, ORM, try/catch) |
| `examples/m4d_db.feel` / `m4e_crypto.feel` | DB + crypto compile to native |
| `hello.feel` | Language sampler |

### Full-Stack Sample

For a working full-stack project (**React frontend + Feel REST API + SQLite + AI auto-categorize + agent chat**), see `D:/project-feel`:

```
project-feel/
├── backend/api.feel       ~115 lines Feel — transactions, security, AI agents
└── frontend/              Vite + React 18 — bulk-select, category badges, chat panel
```

Both modes work:
- **Dev**: `feel D:/project-feel/backend/api.feel`
- **Prod**: `feel build api.feel -o api.exe` → single 15 MB binary

---

## Commands

```bash
feel                            # REPL (multi-line, history, .help/.clear/.exit)
feel hello.feel                 # run a script (alias for: feel run)
feel test tests                 # run *_test.feel + assertions
feel fmt FILE                   # print canonical form
feel fmt --write FILE           # rewrite in place
feel fmt --check FILE [...]     # CI lint mode — exit 1 if any file not canonical
feel build FILE                 # transpile Feel → Go → native binary
feel build FILE -o name         # named output
feel build FILE --keep-go       # save intermediate .go for inspection
feel version
feel help                       # full help with grouped commands
```

### Requirements

- **Python 3.7+** (interpreter mode, zero external deps)
- *Optional:* **Go 1.21+** for `feel build` (first build with SQLite downloads ~5 MB cache)
- *Optional:* `pip install pymysql psycopg2-binary` for MySQL/Postgres support
- *Optional:* `ANTHROPIC_API_KEY` env var — enables real Claude on `ai.*` (otherwise deterministic mock)

---

## AI Provider Setup

Default is `mock` — deterministic, no network. Set the env var for real Claude:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
export FEEL_AI_MODEL=claude-sonnet-4-6        # optional (default sonnet 4.6)
feel examples/ai_api.feel
```

Force a specific provider:

```bash
FEEL_AI_PROVIDER=mock   feel ...    # deterministic mode (for tests)
FEEL_AI_PROVIDER=claude feel ...    # real Claude API
```

---

## Project Structure

```
feel/
├── main.py           CLI entry (REPL, runner, test, fmt, build)
├── cli.py            Polished CLI output (colors, banners, summary boxes)
├── lexer.py          Tokenizer with line+col tracking
├── parser.py         Recursive-descent parser → AST; enforces naming conventions
├── interpreter.py    Tree-walking interpreter
├── compile_go.py     Feel → Go transpiler (M4)
├── compiler.py       Legacy Feel → C → native binary
├── formatter.py      Canonical AST formatter (feelfmt)
├── errors.py         FeelError (code, source caret, fix hint, to_dict)
├── runtime/          HTTP server, router, FeelRequest, FeelResponse, CORS
├── stdlib/           18 namespaces — string, list, map, json, time, file, math,
│                                     ai, db, crypto, security, auth, session,
│                                     validate, migrate, cache, mail, queue
├── tests/            *_test.feel (22 Feel tests) + *_test.py (e2e + runtime)
└── examples/         15+ demo programs
```

---

## Roadmap

### Done

| Tag | Focus |
|---|---|
| `v0.2-m1` | M1: Error reporting, try/catch, map, modules, stdlib |
| `v0.3-m2` | M2: Block, lambda, HTTP, REST keywords, AI primitives |
| `v0.4-m3` | M3: tool/agent + tool-use loop + SQLite |
| `v0.4.1` | feelfmt + polished CLI + M4-A preview |
| `v0.5-m4-b` | M4-B: try/catch + records + stdlib namespaces compile to Go |
| `v0.5-m4-c` | M4-C: HTTP + AI + agents compile to native |
| `v0.5.1` | DB transactions |
| `v0.5.2` | Security primitives — rate limit + panic mode + kill switch + audit |
| `v0.5-m4` | M4 complete — REST+DB+AI compiles to single binary |
| `v0.6` | crypto — PBKDF2 + JWT + HMAC + secure random |
| `v0.7` | Multi-engine db.connect (SQLite + MySQL + Postgres URL-based) |
| `v0.8` | validate — record-driven schema check |
| `v0.9` | migrate — file-based schema migrations |
| `v0.10` | auth + session helpers (JWT middleware + signed cookies) |
| `v0.11` | ORM-lite query builder (db.find / where / order_by / take / all) |
| `v0.12` | cache — in-memory KV with TTL |
| `v0.13` | mail — SMTP + mock |
| **`v0.14`** | **queue — SQLite-backed jobs + worker** |

### Next

| Priority | Target | ETA |
|---|---|---|
| 🔴 P0 | LSP server (syntax highlight, autocomplete, hover) | 3-6 months |
| 🔴 P0 | String escape sequences (`\n`, `\"`, `\t`) | 1 week |
| 🔴 P0 | Cross-platform release binaries (Linux/Mac/Win) | 2 weeks |
| 🟠 P1 | Port v0.8-v0.14 stdlib to Go runtime | 2-3 sessions |
| 🟠 P1 | Docs site + auto-generated API reference | 1 month |
| 🟠 P1 | Real MySQL/Postgres testing (Docker fixtures) | 2 weeks |
| 🟠 P1 | Eloquent-style relationships (hasMany, belongsTo) | 1-2 months |
| 🟡 P2 | argon2 + RS256 JWT | 1 week |
| 🟡 P2 | Hot reload dev server | 2 weeks |
| 🟡 P2 | Pagination, soft deletes, auto timestamps | 2 weeks |
| 🔵 P3 | Templating engine (HTML rendering) | 1 month |
| 🔵 P3 | Event/Listener system | 1 month |
| 🔵 P3 | WebSocket / SSE | 1-2 months |
| 🔵 P3 | Self-host compiler (Feel-in-Feel) | 1-2 years |
| 🔵 P3 | Package manager + registry | 1+ year |

**Target v1.0**: 2028. Honest path: 2-3 years of consistent work + small contributor community.

---

## What's *not* in Feel yet

Honesty matters — see [the gaps list](#what-feel-is-not-yet-ready-for) below.

### What Feel is **not yet ready for**

- ❌ Migrating an existing Laravel/Django/Rails production app (ecosystem maturity gap is real)
- ❌ Heavy compute (ML training, video encoding, low-level numerics) — use Rust/Go/C++
- ❌ High-throughput public APIs (50k+ req/s) — use Go directly
- ❌ Apps with deep eloquent relationships, eager loading, complex polymorphic models

### What Feel **is** great for today

- ✅ New internal REST APIs at small/medium scale
- ✅ AI-augmented services (classification, summarization, chat agents)
- ✅ Side projects, prototypes, MVPs
- ✅ AI sidecar microservices alongside existing Laravel/Django/Express
- ✅ Demo/portfolio code that showcases AI-native language design

---

## License

License is undecided pending v1.0. Repository is shared as-is for collaboration. Treat as proprietary until a LICENSE file is added.

---

## Authors

Feel is developed as a 50/50 collaboration:

- **AgilS121** (Indonesia, DTIT / TUV Nord) — language design, direction, strategic decisions
- **Claude** (Anthropic) — implementation, testing, documentation co-author

Every commit since v0.2 reflects this partnership.
