# CRUD REST API + MySQL — Setup Lengkap

Panduan ini mengajarkan cara bangun REST API CRUD lengkap dengan Feel:
**setup → koneksi MySQL → migrate schema → CRUD endpoints → transaksi → deploy**.

Target: orang yang baru install Feel, ingin nyoba bikin "users + orders" backend
yang nyambung ke MySQL beneran (bukan SQLite).

---

## Daftar isi

1. [Setup awal](#1-setup-awal)
2. [Project structure](#2-project-structure)
3. [Koneksi MySQL](#3-koneksi-mysql)
4. [Migration: bikin tabel](#4-migration-bikin-tabel)
5. [CRUD endpoints](#5-crud-endpoints)
6. [Transaction](#6-transaction)
7. [Validation + error handling](#7-validation--error-handling)
8. [Auth (bonus): JWT login](#8-auth-bonus-jwt-login)
9. [Testing dengan curl](#9-testing-dengan-curl)
10. [Compile jadi binary untuk production](#10-compile-jadi-binary-untuk-production)
11. [Cheat sheet](#cheat-sheet)

---

## 1. Setup awal

### Prasyarat

| Tool | Versi | Untuk |
|---|---|---|
| Python | 3.10+ | Menjalankan interpreter Feel |
| Go | 1.21+ | Compile Feel → native binary |
| MySQL | 5.7+ atau MariaDB 10+ | Database |

Cek instalasi:
```bash
python --version
go version
mysql --version
```

### Clone & test Feel

```bash
git clone https://github.com/AgilS121/feel.git
cd feel

# Pastikan jalan
python main.py --help

# Run test suite
python main.py test tests
```

Output yang diharapkan:
```
  ──────────────────────────────────────────────────
  26 passed · 5.42s
```

### CLI singkat

| Command | Fungsi |
|---|---|
| `feel run file.feel` | Jalankan dengan interpreter (dev mode) |
| `feel build file.feel` | Compile ke native binary (production) |
| `feel test dir/` | Jalankan semua `*_test.feel` |
| `feel fmt file.feel` | Format ke canonical syntax |
| `feel repl` | REPL interaktif |

> **Note:** Di Windows, ganti `feel` dengan `python main.py` kalau belum dipasang
> sebagai alias. Sisa dokumen ini pakai `feel` saja untuk ringkasnya.

---

## 2. Project structure

Bikin folder project baru:

```bash
mkdir my-api
cd my-api
```

Layout yang akan kita pakai:

```
my-api/
├── main.feel              # Entry point — routes + serve
├── config.feel            # DB config + env loading
├── migrations/
│   ├── 001_create_users.sql
│   └── 002_create_orders.sql
└── handlers/
    ├── users.feel         # User CRUD endpoints
    └── orders.feel        # Order CRUD endpoints
```

Feel mendukung import antar file (`import handlers/users`), jadi pisahin modul
seperti ini direkomendasikan untuk project >10 endpoint.

---

## 3. Koneksi MySQL

### Format URL koneksi

Feel pakai URL-style connection string untuk semua database:

```
mysql://user:password@host:port/database
postgres://user:password@host:port/database
sqlite:///path/to/file.db
```

### config.feel

```feel
-- config.feel — load DB URL from env, fallback ke local default.
let db_url = env.get("DATABASE_URL")
let final_url = when db_url == nothing
  -> "mysql://feeluser:feelpass@localhost:3306/myapi"
  otherwise
  -> db_url

let conn = db.connect(final_url)
show -> "[config] connected to {final_url}"
```

> `env.get(name)` belum ada di stdlib resmi. Untuk sekarang gunakan default URL
> langsung, atau set lewat OS env var dan bikin helper kecil:
>
> ```feel
> define get_env taking name -> file.read_or(".env_value_" + name, "")
> ```

### Setup MySQL user (sekali jalan)

Di MySQL CLI:

```sql
CREATE DATABASE myapi CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'feeluser'@'localhost' IDENTIFIED BY 'feelpass';
GRANT ALL PRIVILEGES ON myapi.* TO 'feeluser'@'localhost';
FLUSH PRIVILEGES;
```

### Test koneksi

```bash
feel run config.feel
```

Output:
```
[config] connected to mysql://feeluser:feelpass@localhost:3306/myapi
```

Kalau error `connection refused` atau `Access denied`, cek:
1. MySQL service jalan? (`systemctl status mysql` / `services.msc`)
2. Port 3306 terbuka?
3. User+password benar?

---

## 4. Migration: bikin tabel

Feel punya module `migrate` untuk version-controlled schema changes.

### Strategi: SQL files

```sql
-- migrations/001_create_users.sql
CREATE TABLE users (
  id          BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
  email       VARCHAR(255) UNIQUE NOT NULL,
  password    VARCHAR(255) NOT NULL,
  name        VARCHAR(100) NOT NULL,
  created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB;
```

```sql
-- migrations/002_create_orders.sql
CREATE TABLE orders (
  id          BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
  user_id     BIGINT UNSIGNED NOT NULL,
  total       DECIMAL(12,2) NOT NULL,
  status      ENUM('pending','paid','shipped','cancelled') DEFAULT 'pending',
  created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

  INDEX idx_user (user_id),
  INDEX idx_status (status),
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB;
```

### migrate.feel

```feel
-- migrate.feel — jalankan sekali untuk apply semua migrations.
import config

migrate.run(config.conn, "migrations/")

show -> "migrations applied"
```

Jalankan:
```bash
feel run migrate.feel
```

`migrate.run` akan:
1. Bikin tabel `_migrations` (kalau belum ada) untuk track yang sudah jalan
2. Loop file `*.sql` di folder, alphabetically
3. Skip yang sudah pernah jalan
4. Execute yang baru, catat di `_migrations`

Mau lihat status:

```feel
let status = migrate.status(config.conn, "migrations/")
show -> status   -- [{file: "001_...", applied: true, applied_at: "..."}, ...]
```

---

## 5. CRUD endpoints

### Pattern dasar

Setiap resource CRUD biasanya punya 5 endpoint:

| Method | Path | Aksi |
|---|---|---|
| GET    | `/users`        | List semua |
| GET    | `/users/{id}`   | Detail satu |
| POST   | `/users`        | Bikin baru |
| PUT    | `/users/{id}`   | Update |
| DELETE | `/users/{id}`   | Hapus |

### handlers/users.feel

```feel
import config

-- GET /users — list all
route GET "/users" -> do {
  let limit = when query.limit == nothing -> 50 otherwise -> int(query.limit)
  let rows = db.query(config.conn, "SELECT id, email, name, created_at FROM users ORDER BY id DESC LIMIT ?", [limit])
  respond rows
}

-- GET /users/{id} — detail
route GET "/users/{id}" -> do {
  let row = db.query_one(config.conn, "SELECT id, email, name, created_at FROM users WHERE id = ?", [int(id)])
  when row == nothing
    -> respond 404 map { error: "user not found", id: int(id) }
    otherwise
    -> respond row
}

-- POST /users — create
route POST "/users" -> do {
  let email = body.email
  let password = body.password
  let name = body.name

  -- Validation
  when email == nothing or password == nothing or name == nothing
    -> respond 400 map { error: "email, password, name required" }
    otherwise -> do {
      -- Hash password before storing (NEVER store plain text)
      let hashed = crypto.hash_password(password)

      db.exec(
        config.conn,
        "INSERT INTO users (email, password, name) VALUES (?, ?, ?)",
        [email, hashed, name]
      )
      let new_id = db.last_insert_id(config.conn)
      respond 201 map { id: new_id, email: email, name: name }
    }
}

-- PUT /users/{id} — update
route PUT "/users/{id}" -> do {
  let name = body.name
  let email = body.email
  db.exec(
    config.conn,
    "UPDATE users SET name = COALESCE(?, name), email = COALESCE(?, email) WHERE id = ?",
    [name, email, int(id)]
  )
  let updated = db.query_one(config.conn, "SELECT id, email, name FROM users WHERE id = ?", [int(id)])
  when updated == nothing
    -> respond 404 map { error: "user not found" }
    otherwise -> respond updated
}

-- DELETE /users/{id}
route DELETE "/users/{id}" -> do {
  db.exec(config.conn, "DELETE FROM users WHERE id = ?", [int(id)])
  respond 204 nothing
}
```

### main.feel

```feel
import config
import handlers/users
import handlers/orders

route GET "/" -> respond map { name: "My API", version: "1.0" }
route GET "/health" -> respond map { ok: true, time: time.now() }

serve on 3000 cors
```

Jalankan:
```bash
feel run main.feel
```

---

## 6. Transaction

Kapan butuh transaction? **Setiap kali 1 operasi business harus update >1 tabel
sekaligus dan harus all-or-nothing.**

Contoh klasik: bikin order baru harus:
1. INSERT ke `orders`
2. UPDATE `users.last_order_at`
3. INSERT ke `audit_log`

Kalau step 2 atau 3 gagal, step 1 harus di-rollback.

### Pola transaction di Feel

```feel
-- POST /orders — create order in transaction
route POST "/orders" -> do {
  let user_id = int(body.user_id)
  let total = body.total

  try
    db.transaction(config.conn, fn ->
      do {
        -- Step 1: insert order
        db.exec(config.conn,
          "INSERT INTO orders (user_id, total) VALUES (?, ?)",
          [user_id, total]
        )
        let order_id = db.last_insert_id(config.conn)

        -- Step 2: update user.last_order_at
        db.exec(config.conn,
          "UPDATE users SET updated_at = NOW() WHERE id = ?",
          [user_id]
        )

        -- Step 3: audit log
        db.exec(config.conn,
          "INSERT INTO audit_log (action, target_id, payload) VALUES (?, ?, ?)",
          ["order_created", order_id, json.encode(map { total: total })]
        )

        order_id
      }
    )
  catch err ->
    respond 500 map { error: "transaction failed", detail: err }
}
```

Cara kerja `db.transaction(conn, fn)`:
1. Begin transaction (`BEGIN`)
2. Jalankan callback `fn`
3. Kalau callback return tanpa exception → `COMMIT`
4. Kalau callback `throw` exception → `ROLLBACK`, re-throw ke try/catch

### Contoh transfer saldo (klasik)

```feel
define transfer taking from_id, to_id, amount ->
  db.transaction(config.conn, fn ->
    do {
      let from_user = db.query_one(config.conn, "SELECT balance FROM users WHERE id = ? FOR UPDATE", [from_id])

      when from_user.balance < amount
        -> throw "insufficient funds"
        otherwise -> do {
          db.exec(config.conn, "UPDATE users SET balance = balance - ? WHERE id = ?", [amount, from_id])
          db.exec(config.conn, "UPDATE users SET balance = balance + ? WHERE id = ?", [amount, to_id])
          "transfer ok"
        }
    }
  )
```

Penting: `SELECT ... FOR UPDATE` mengunci row supaya transaction lain nggak bisa
ubah saldo bersamaan (race condition prevention).

---

## 7. Validation + error handling

### Validate field via record

```feel
record UserCreate {
  email: text
  password: text
  name: text
}

route POST "/users" -> do {
  let validated = validate.against(body, UserCreate)
  when validated.ok == false
    -> respond 400 map { error: "validation failed", details: validated.errors }
    otherwise -> do {
      -- ... proceed with insert
    }
}
```

### Catch errors per-route

```feel
route POST "/users" -> do {
  try
    do {
      db.exec(config.conn, "INSERT INTO users (email) VALUES (?)", [body.email])
      respond 201 map { ok: true }
    }
  catch err ->
    when contains(err, "Duplicate entry")
      -> respond 409 map { error: "email already exists" }
      otherwise
      -> respond 500 map { error: "database error", detail: err }
}
```

### Global error mapping

Throw apapun yang nggak ditangkap akan jadi 500. Tapi kamu bisa throw object
khusus untuk kontrol status code:

```feel
throw map { status: 422, message: "invalid input" }
```

(Feel runtime memperhatikan field `status` di throw value untuk HTTP responses.)

---

## 8. Auth (bonus): JWT login

### login endpoint

```feel
route POST "/auth/login" -> do {
  let email = body.email
  let password = body.password

  let user = db.query_one(config.conn, "SELECT id, password FROM users WHERE email = ?", [email])

  when user == nothing or not crypto.verify_password(password, user.password)
    -> respond 401 map { error: "invalid credentials" }
    otherwise -> do {
      let token = crypto.jwt_sign(
        map { user_id: user.id, exp: time.now() + 86400 },
        config.jwt_secret
      )
      respond map { token: token }
    }
}
```

### Protected endpoint

```feel
route GET "/api/me" -> do {
  let token = auth.bearer_token(request)
  let claims = try crypto.jwt_verify(token, config.jwt_secret) catch _ -> nothing

  when claims == nothing
    -> respond 401 map { error: "unauthorized" }
    otherwise -> do {
      let user = db.query_one(config.conn, "SELECT id, email, name FROM users WHERE id = ?", [claims.user_id])
      respond user
    }
}
```

---

## 9. Testing dengan curl

```bash
# Health check
curl http://localhost:3000/health

# Bikin user
curl -X POST http://localhost:3000/users \
  -H "Content-Type: application/json" \
  -d '{"email":"a@b.com","password":"secret123","name":"Alice"}'

# List users
curl http://localhost:3000/users

# Detail user
curl http://localhost:3000/users/1

# Update
curl -X PUT http://localhost:3000/users/1 \
  -H "Content-Type: application/json" \
  -d '{"name":"Alice Updated"}'

# Delete
curl -X DELETE http://localhost:3000/users/1

# Login
curl -X POST http://localhost:3000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"a@b.com","password":"secret123"}'

# Authenticated request
curl http://localhost:3000/api/me \
  -H "Authorization: Bearer eyJhbGciOi..."
```

---

## 10. Compile jadi binary untuk production

```bash
feel build main.feel -o my-api
```

Output:
```
  • Compiling main.feel → Go
  ✓ Linked native binary  16 MB
  Built my-api in 5.2s
```

Run binary di server production (Linux):

```bash
# Set env var untuk production DB
export DATABASE_URL="mysql://produser:prodpass@db.internal:3306/myapi_prod"

# Jalankan binary
./my-api
```

Binary self-contained — nggak butuh Python, Go runtime, atau library lain di
target machine. Cocok untuk Docker, systemd service, atau bare-metal deployment.

### Systemd service (Linux)

```ini
# /etc/systemd/system/my-api.service
[Unit]
Description=My Feel API
After=network.target mysql.service

[Service]
Type=simple
User=www-data
WorkingDirectory=/opt/my-api
Environment="DATABASE_URL=mysql://produser:prodpass@localhost:3306/myapi_prod"
ExecStart=/opt/my-api/my-api
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable my-api
sudo systemctl start my-api
sudo systemctl status my-api
```

### Tambah HTTPS

```feel
serve on 443 cors tls "/etc/letsencrypt/live/api.example.com/fullchain.pem"
                     "/etc/letsencrypt/live/api.example.com/privkey.pem"
```

---

## Cheat sheet

### DB operations
```feel
db.connect(url)                        -- open connection
db.exec(conn, sql, params)             -- INSERT/UPDATE/DELETE
db.query(conn, sql, params)            -- SELECT → list of maps
db.query_one(conn, sql, params)        -- SELECT → first map or nothing
db.last_insert_id(conn)                -- ID dari INSERT terakhir
db.transaction(conn, fn)               -- wrap fn in BEGIN/COMMIT/ROLLBACK
db.close(conn)                         -- tutup koneksi
```

### ORM-lite pipeline (alternatif raw SQL)
```feel
let users = db.find(conn, "users")
  | db.where("status", "=", "active")
  | db.order_by("created_at DESC")
  | db.limit(50)
  | db.all
```

### Migrate
```feel
migrate.run(conn, "migrations/")         -- apply pending
migrate.status(conn, "migrations/")      -- list status
```

### Crypto
```feel
crypto.hash_password(plain)              -- → string (PBKDF2)
crypto.verify_password(plain, hash)      -- → bool
crypto.jwt_sign(claims, secret)          -- → string (HS256)
crypto.jwt_verify(token, secret)         -- → claims map (throws if invalid)
crypto.hmac_sha256(data, key)            -- → hex string
crypto.random_token(bytes)               -- → hex string
```

### Validation
```feel
validate.against(value, RecordType)      -- → { ok, value | errors }
```

### HTTP client (panggil API lain)
```feel
let resp = http.get("https://api.x/users")
let r = http.post(url, map { name: "x" })           -- auto JSON encode
let data = http.get_json(url)                       -- shortcut
http.request("PUT", url, map { body: ..., headers: ..., timeout: 5 })
```

### Response shortcuts
```feel
respond value                            -- 200 with auto-JSON
respond 201 value                        -- custom status
respond 404 map { error: "not found" }
respond 204 nothing                      -- no body
```

### Request access
```feel
request.method        -- "GET", "POST", ...
request.path          -- "/users/42"
request.query         -- map of ?k=v
request.headers       -- map (lowercased keys)
request.body          -- auto-decoded (JSON, form, multipart)
request.files         -- multipart uploads → { name, size, content_type, save_to }
request.form          -- form fields (non-file)
request.params        -- path params { id: "42" } extracted from /users/{id}
```

---

## Troubleshooting

| Error | Penyebab | Solusi |
|---|---|---|
| `connection refused` (MySQL) | MySQL service down | `systemctl start mysql` |
| `Access denied for user` | Salah kredensial | Cek `GRANT` privileges |
| `Unknown database` | DB belum dibikin | `CREATE DATABASE myapi` |
| `Duplicate entry` di insert | UNIQUE constraint kena | Cek email/username yang sudah ada |
| `Table doesn't exist` | Belum migrate | `feel run migrate.feel` |
| `panic: feel_throw{...}` saat compile run | Connection string typo / unreachable host | Cek URL format + network |
| Binary error "missing go.sum" | `go mod tidy` belum jalan | `feel build` auto-runs ini; kalau gagal cek koneksi internet (untuk download deps Go) |

---

## Next steps

- **Queue** untuk background jobs: lihat `examples/queue_demo.feel`
- **Cache** untuk hot reads: `cache.set/get/get_or_compute`
- **Mail** untuk notifikasi: `mail.send(to, subject, body)`
- **Static file** untuk admin UI: `static "/admin" -> "./public"`
- **WebSocket** untuk real-time: `route WS "/live" -> ...`
- **AI primitives** kalau butuh agent/tool calling: `agent`, `tool` keywords

Lihat `examples/` untuk demo masing-masing fitur.

---

Selesai. Kalau ada pertanyaan atau issue, file di [GitHub](https://github.com/AgilS121/feel/issues).
