"""SQLite storage: the schema and the small queries shared by the engine and the bot."""

import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

IST = timezone(timedelta(hours=5, minutes=30))
DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "offers.db"

# Offers that actually went out count towards budgets and frequency caps.
SENT_STATUSES = ("sent", "claimed", "declined")

SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
    id INTEGER PRIMARY KEY,
    first_name TEXT, last_name TEXT, gender TEXT, age INTEGER,
    city TEXT, city_tier INTEGER, language TEXT, payment_pref TEXT,
    signup_date TEXT, signup_interest TEXT,
    points INTEGER, points_expiry TEXT,
    -- hidden truth for evaluation only: the engine never reads these
    hidden_type TEXT, hidden_price_sensitivity REAL
);
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY, name TEXT, category TEXT, price INTEGER, margin_pct REAL
);
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY, customer_id INTEGER, order_date TEXT, product_id INTEGER,
    category TEXT, qty INTEGER, total INTEGER, used_coupon INTEGER, discount_inr INTEGER,
    returned INTEGER
);
CREATE INDEX IF NOT EXISTS idx_orders_customer ON orders(customer_id);
CREATE TABLE IF NOT EXISTS carts (
    customer_id INTEGER PRIMARY KEY, product_id INTEGER, qty INTEGER, abandoned_at TEXT
);
CREATE TABLE IF NOT EXISTS telegram_users (
    chat_id INTEGER PRIMARY KEY, customer_id INTEGER, joined_at TEXT, opted_out INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY,
    created_at TEXT, chat_id INTEGER, customer_id INTEGER, trigger TEXT,
    source TEXT,          -- ai | rules | blocked
    offer_id TEXT, value INTEGER, cost_inr REAL, code TEXT, expires_at TEXT,
    message TEXT, reason TEXT, why TEXT,
    ai_attempts INTEGER DEFAULT 0,
    rejected TEXT,        -- JSON: AI answers the validator turned down, with the errors
    status TEXT           -- sent | claimed | declined | not_sent
);
CREATE INDEX IF NOT EXISTS idx_decisions_customer ON decisions(customer_id, created_at);
"""


def now_ist() -> datetime:
    return datetime.now(IST).replace(microsecond=0)


def db_path() -> Path:
    return Path(os.getenv("DB_PATH") or DEFAULT_DB)


def connect(path: str | Path | None = None) -> sqlite3.Connection:
    path = Path(path) if path else db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def _start_of_day(now: datetime) -> str:
    return now.replace(hour=0, minute=0, second=0).isoformat()


def budget_used_today(conn: sqlite3.Connection, now: datetime) -> float:
    row = conn.execute(
        f"SELECT COALESCE(SUM(cost_inr), 0) FROM decisions "
        f"WHERE status IN {SENT_STATUSES} AND created_at >= ?",
        (_start_of_day(now),),
    ).fetchone()
    return float(row[0])


def offer_history(conn: sqlite3.Connection, customer_id: int, now: datetime) -> tuple[int, datetime | None]:
    """Offers this customer received today, and when the last one was sent."""
    today = conn.execute(
        f"SELECT COUNT(*) FROM decisions WHERE customer_id = ? AND status IN {SENT_STATUSES} AND created_at >= ?",
        (customer_id, _start_of_day(now)),
    ).fetchone()[0]
    last = conn.execute(
        f"SELECT MAX(created_at) FROM decisions WHERE customer_id = ? AND status IN {SENT_STATUSES}",
        (customer_id,),
    ).fetchone()[0]
    return today, datetime.fromisoformat(last) if last else None


def category_margins(conn: sqlite3.Connection) -> dict[str, float]:
    rows = conn.execute("SELECT category, AVG(margin_pct) FROM products GROUP BY category").fetchall()
    return {r[0]: r[1] for r in rows}


def save_decision(conn: sqlite3.Connection, **fields) -> int:
    cols = ", ".join(fields)
    marks = ", ".join("?" for _ in fields)
    cur = conn.execute(f"INSERT INTO decisions ({cols}) VALUES ({marks})", tuple(fields.values()))
    conn.commit()
    return cur.lastrowid


def get_decision(conn: sqlite3.Connection, decision_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM decisions WHERE id = ?", (decision_id,)).fetchone()


def set_decision_status(conn: sqlite3.Connection, decision_id: int, status: str, only_if: str = "sent") -> bool:
    cur = conn.execute(
        "UPDATE decisions SET status = ? WHERE id = ? AND status = ?", (status, decision_id, only_if)
    )
    conn.commit()
    return cur.rowcount == 1


# ── Telegram users ─────────────────────────────────────────────────────────

def get_user(conn: sqlite3.Connection, chat_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM telegram_users WHERE chat_id = ?", (chat_id,)).fetchone()


def link_new_customer(conn: sqlite3.Connection, chat_id: int, now: datetime) -> int:
    """Give this chat a synthetic customer nobody else is playing (one with some history)."""
    row = conn.execute(
        "SELECT id FROM customers "
        "WHERE id NOT IN (SELECT customer_id FROM telegram_users) "
        "AND (id IN (SELECT customer_id FROM orders) OR id IN (SELECT customer_id FROM carts)) "
        "ORDER BY RANDOM() LIMIT 1"
    ).fetchone()
    if row is None:
        raise RuntimeError("No free synthetic customers left; generate more data.")
    conn.execute(
        "INSERT INTO telegram_users (chat_id, customer_id, joined_at, opted_out) VALUES (?, ?, ?, 0) "
        "ON CONFLICT(chat_id) DO UPDATE SET customer_id = excluded.customer_id, opted_out = 0",
        (chat_id, row["id"], now.isoformat()),
    )
    conn.commit()
    return row["id"]


def set_opted_out(conn: sqlite3.Connection, chat_id: int, opted_out: bool) -> None:
    conn.execute("UPDATE telegram_users SET opted_out = ? WHERE chat_id = ?", (int(opted_out), chat_id))
    conn.commit()


def active_users(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM telegram_users WHERE opted_out = 0").fetchall()
