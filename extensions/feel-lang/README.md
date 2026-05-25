# Feel Language Support for VS Code

Official VS Code extension for the **Feel** programming language — the AI-native backend language that compiles to standalone Go binaries.

## Features

### Syntax Highlighting
Full syntax highlighting for `.feel` files, including:
- Keywords: `let`, `define`, `taking`, `when`, `otherwise`, `route`, `respond`, `serve`, `record`, `agent`, `tool`, and more
- HTTP methods: `GET`, `POST`, `PUT`, `PATCH`, `DELETE`, `HEAD`, `OPTIONS`, `WS`
- All 18 stdlib namespaces: `db`, `ai`, `crypto`, `auth`, `list`, `map`, `string`, `json`, `time`, `file`, `math`, `validate`, `cache`, `mail`, `queue`, `security`, `env`, `http`
- String interpolation: `"{variable} and {expression}"`
- Comments: `-- single-line comments`
- Record types (PascalCase), functions, operators, and pipeline operator `|`

### Snippets
40+ production-ready snippets for rapid development:

| Prefix | Description |
|--------|-------------|
| `hello-api` | Hello World REST API |
| `route-get` | GET route handler |
| `route-post` | POST route handler |
| `route-put` | PUT route handler |
| `route-delete` | DELETE route handler |
| `crud-rest` | Full CRUD scaffold |
| `auth-scaffold` | JWT auth (register + login) |
| `define` | Named function |
| `fn` | Lambda function |
| `when` | Conditional expression |
| `try` | Try/catch block |
| `record` | Record type definition |
| `db-connect` | Database connection |
| `db-query` | SELECT query |
| `db-exec` | INSERT/UPDATE/DELETE |
| `db-transaction` | Transaction block |
| `ai-ask` | AI question |
| `ai-classify` | Text classification |
| `agent` | AI agent definition |
| `tool` | Tool definition |
| `jwt-sign` | Sign JWT token |
| `auth-jwt` | Require JWT middleware |
| `validate` | Body validation |
| `list-map` | Map over list |
| `list-filter` | Filter list |
| `cache-compute` | Cache get-or-compute |

### Language Features
- **Auto-closing brackets**: `{}`, `[]`, `()`
- **Comment toggling**: `-- comment` with `Ctrl+/`
- **Bracket matching**: Highlight matching pairs
- **Code folding**: `do { ... }` blocks
- **Run current file**: Click ▶ in editor title bar or use `Feel: Run Current File`
- **Format document**: `Feel: Format Document` command (requires Feel interpreter)

## Requirements

The syntax highlighting and snippets work out of the box with **no dependencies**.

For the **Run File** and **Format Document** features, you need:
- Python 3.8+ (for interpreter mode)
- The Feel project installed: `git clone https://github.com/AgilS121/feel.git`

## Extension Settings

| Setting | Default | Description |
|---------|---------|-------------|
| `feel.interpreter.path` | `""` | Path to Feel's `main.py`. Auto-detected if left empty. |
| `feel.formatting.enable` | `true` | Enable format-on-save with feelfmt. |

## Language Overview

Feel is a minimal, readable backend language. Example:

```feel
-- Simple REST API
let conn = db.connect(":memory:")

route GET "/users" -> do {
  let rows = db.query(conn, "SELECT * FROM users", [])
  respond rows
}

route POST "/users" -> do {
  let data = validate.shape(body, "UserInput")
  db.exec(conn, "INSERT INTO users (name) VALUES (?)", [data["name"]])
  respond 201 map { id: db.last_id(conn) }
}

route DELETE "/users/{id}" -> do {
  db.exec(conn, "DELETE FROM users WHERE id = ?", [id])
  respond map { message: "deleted" }
}

serve on 3000 cors
```

### Key Syntax Rules
- **Variables**: `let name = value` (immutable)
- **Functions**: `define add taking a, b -> a + b`
- **Lambdas**: `fn x -> x * 2`
- **Conditionals**: `when x > 0 -> "pos" otherwise -> "neg"`
- **Pipeline**: `data | json.decode | show`
- **Comments**: `-- this is a comment`
- **Naming**: `snake_case` for variables/functions, `PascalCase` for record types

## Quick Start

1. Install the extension
2. Create a file `hello.feel`
3. Type `hello-api` and press `Tab` for an instant API scaffold
4. Click ▶ in the title bar to run it

## Changelog

See [CHANGELOG.md](CHANGELOG.md).

## License

MIT © AgilS121 / Agil Bharata
