"""Seed shared SQLite DB for benchmark."""
import sqlite3, random, os

DB = os.path.join(os.path.dirname(__file__), "orders.db")
if os.path.exists(DB):
    os.remove(DB)

c = sqlite3.connect(DB)
cur = c.cursor()

cur.executescript("""
CREATE TABLE users (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  email TEXT NOT NULL
);
CREATE TABLE customers (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  company TEXT NOT NULL
);
CREATE TABLE contacts (
  id INTEGER PRIMARY KEY,
  customer_id INTEGER NOT NULL,
  name TEXT NOT NULL,
  phone TEXT NOT NULL
);
CREATE TABLE invoices (
  id INTEGER PRIMARY KEY,
  status TEXT NOT NULL
);
CREATE TABLE orders (
  id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL,
  customer_id INTEGER NOT NULL,
  contact_id INTEGER NOT NULL,
  invoice_id INTEGER NOT NULL,
  total INTEGER NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX idx_orders_user ON orders(user_id);
CREATE INDEX idx_orders_customer ON orders(customer_id);
CREATE INDEX idx_orders_contact ON orders(contact_id);
CREATE INDEX idx_orders_invoice ON orders(invoice_id);
CREATE INDEX idx_invoices_status ON invoices(status);
""")

random.seed(42)
statuses = ["paid", "processing", "unpaid"]

users = [(i, f"User {i}", f"user{i}@example.com") for i in range(1, 101)]
cur.executemany("INSERT INTO users VALUES (?,?,?)", users)

customers = [(i, f"Customer {i}", f"Company {i}") for i in range(1, 201)]
cur.executemany("INSERT INTO customers VALUES (?,?,?)", customers)

contacts = [(i, random.randint(1, 200), f"Contact {i}", f"+62-{1000+i}") for i in range(1, 501)]
cur.executemany("INSERT INTO contacts VALUES (?,?,?,?)", contacts)

invoices = [(i, random.choice(statuses)) for i in range(1, 5001)]
cur.executemany("INSERT INTO invoices VALUES (?,?)", invoices)

orders = [
    (
        i,
        random.randint(1, 100),
        random.randint(1, 200),
        random.randint(1, 500),
        i,  # 1:1 invoice
        random.randint(100000, 10000000),
        f"2026-{random.randint(1,5):02d}-{random.randint(1,28):02d}",
    )
    for i in range(1, 5001)
]
cur.executemany("INSERT INTO orders VALUES (?,?,?,?,?,?,?)", orders)

c.commit()
c.close()

print(f"Seeded {DB}")
print("  users:    100")
print("  customers: 200")
print("  contacts:  500")
print("  invoices: 5000")
print("  orders:   5000")
