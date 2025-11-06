# init_db.py
import os
import sqlite3

DB_PATH = os.environ.get("OPENAI_DB_PATH", "./data/app.db")
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

con = sqlite3.connect(DB_PATH)
cur = con.cursor()

cur.execute("PRAGMA journal_mode=WAL;")
cur.execute("PRAGMA foreign_keys=ON;")

# ----- wallets -----
cur.execute("""
CREATE TABLE IF NOT EXISTS wallets (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  customer_id TEXT UNIQUE,
  phone TEXT UNIQUE,
  balance_cents INTEGER NOT NULL DEFAULT 0
);
""")

# ----- meetings -----
cur.execute("""
CREATE TABLE IF NOT EXISTS meetings (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  date TEXT NOT NULL,                 -- YYYY-MM-DD
  time TEXT NOT NULL,                 -- HH:MM (24h)
  duration_minutes INTEGER NOT NULL DEFAULT 30,
  subject TEXT,
  customer_id TEXT,
  UNIQUE(date, time)                  -- global conflict: one meeting per slot
);
""")

# seed (optional)
cur.execute("INSERT OR IGNORE INTO wallets (customer_id, phone, balance_cents) VALUES (?,?,?)",
            ("1", "+989121234567", 1250000))
cur.execute("INSERT OR IGNORE INTO wallets (customer_id, phone, balance_cents) VALUES (?,?,?)",
            ("2", "+982188800000", 899900))

con.commit()
con.close()

print(f"Initialized DB at {DB_PATH}")

