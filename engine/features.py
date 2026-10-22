"""Turn the raw tables into the customer profile the engine reasons about.

Only observable behaviour is used; the hidden_* columns are never read here.
"""

import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime

from engine import db


@dataclass
class Profile:
    customer_id: int
    first_name: str
    city: str
    language: str
    days_since_signup: int
    orders: int
    avg_order_inr: float
    days_since_last_order: int | None
    typical_gap_days: float | None
    discount_share: float          # share of orders that used a coupon
    return_rate: float
    fav_category: str
    lifecycle: str                 # new | active | at_risk | lapsed | dormant
    points: int
    points_expire_in_days: int | None
    cart_product: str | None = None
    cart_category: str | None = None
    cart_value: float = 0
    cart_hours_ago: float | None = None
    offers_today: int = 0
    minutes_since_last_offer: float | None = None
    opted_out: bool = False


def lifecycle(orders: int, days_since_signup: int, days_since_last: int | None, gap: float | None) -> str:
    if days_since_signup <= 45 and orders <= 1:
        return "new"
    if orders == 0 or days_since_last > 180:
        return "dormant"
    if days_since_last > 90:
        return "lapsed"
    if days_since_last > 1.5 * max(gap or 30, 14):
        return "at_risk"
    return "active"


def build_profile(conn: sqlite3.Connection, customer_id: int, now: datetime) -> Profile:
    c = conn.execute(
        "SELECT first_name, city, language, signup_date, signup_interest, points, points_expiry "
        "FROM customers WHERE id = ?", (customer_id,)).fetchone()
    if c is None:
        raise KeyError(f"customer {customer_id} not found")
    today = now.date()
    rows = conn.execute(
        "SELECT order_date, total, used_coupon, returned, category FROM orders "
        "WHERE customer_id = ? ORDER BY order_date", (customer_id,)).fetchall()
    n = len(rows)
    dates = [date.fromisoformat(r["order_date"]) for r in rows]
    days_since_last = (today - dates[-1]).days if rows else None
    gap = (dates[-1] - dates[0]).days / (n - 1) if n >= 2 else None
    days_since_signup = (today - date.fromisoformat(c["signup_date"])).days

    expire_days = (date.fromisoformat(c["points_expiry"]) - today).days if c["points_expiry"] else None
    points = c["points"] if expire_days is not None and expire_days >= 0 else 0

    cart = conn.execute(
        "SELECT p.name, p.category, p.price * c.qty AS value, c.abandoned_at "
        "FROM carts c JOIN products p ON p.id = c.product_id WHERE c.customer_id = ?",
        (customer_id,)).fetchone()
    offers_today, last_offer = db.offer_history(conn, customer_id, now)
    opted_out = conn.execute(
        "SELECT 1 FROM telegram_users WHERE customer_id = ? AND opted_out = 1", (customer_id,)).fetchone()

    return Profile(
        customer_id=customer_id,
        first_name=c["first_name"],
        city=c["city"],
        language=c["language"],
        days_since_signup=days_since_signup,
        orders=n,
        avg_order_inr=sum(r["total"] for r in rows) / n if n else 0.0,
        days_since_last_order=days_since_last,
        typical_gap_days=gap,
        discount_share=sum(r["used_coupon"] for r in rows) / n if n else 0.0,
        return_rate=sum(r["returned"] for r in rows) / n if n else 0.0,
        fav_category=Counter(r["category"] for r in rows).most_common(1)[0][0] if rows else c["signup_interest"],
        lifecycle=lifecycle(n, days_since_signup, days_since_last, gap),
        points=points,
        points_expire_in_days=expire_days if points else None,
        cart_product=cart["name"] if cart else None,
        cart_category=cart["category"] if cart else None,
        cart_value=float(cart["value"]) if cart else 0.0,
        cart_hours_ago=(now - datetime.fromisoformat(cart["abandoned_at"])).total_seconds() / 3600 if cart else None,
        offers_today=offers_today,
        minutes_since_last_offer=(now - last_offer).total_seconds() / 60 if last_offer else None,
        opted_out=opted_out is not None,
    )
