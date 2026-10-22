"""Generate synthetic customers, products, orders and carts into SQLite.

Each customer has a hidden type (deal hunter, loyal, drifting...) that drives
how often they buy, how much they spend and how much they like coupons. The
offer engine never reads the hidden_* columns: it has to infer behaviour from
the orders, like a real platform. The hidden truth lets us check later whether
the targeting found the right people.

Usage:  python -m data.generate [--customers 500] [--seed 42] [--db data/offers.db]
"""

import argparse
import math
import random
from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from engine import db


@dataclass(frozen=True)
class CustomerType:
    share: float              # fraction of customers
    orders_per_year: float    # while active
    median_order_inr: int
    price_sensitivity: float  # 0 = ignores coupons, 1 = only buys with one
    p_cart: float             # chance of an abandoned cart right now


TYPES = {
    "deal_hunter": CustomerType(0.25, 5, 700, 0.85, 0.45),
    "loyal":       CustomerType(0.15, 12, 1100, 0.15, 0.20),
    "premium":     CustomerType(0.10, 5, 3200, 0.10, 0.30),
    "festival":    CustomerType(0.20, 3, 1500, 0.50, 0.35),
    "new":         CustomerType(0.10, 0, 900, 0.50, 0.50),
    "drifting":    CustomerType(0.10, 8, 1000, 0.45, 0.30),
    "dormant":     CustomerType(0.10, 4, 900, 0.50, 0.05),
}

# category: items, price range (Rs), mean margin %, return rate, share of interest
CATEGORIES = {
    "fashion": (["Cotton kurta set", "Phulkari dupatta", "Running shoes", "Denim jacket", "Printed salwar suit",
                 "Casual sneakers", "Woollen shawl", "Formal shirt", "Hooded sweatshirt", "Embroidered jutti"],
                (399, 3999), 45, 0.22, 0.35),
    "electronics": (["Wireless earbuds", "Smartwatch", "Power bank", "Bluetooth speaker", "Neckband earphones",
                     "Smart LED bulb", "Beard trimmer", "Mixer grinder"],
                    (499, 14999), 9, 0.06, 0.20),
    "beauty": (["Vitamin C face serum", "Sunscreen lotion", "Herbal hair oil", "Matte lipstick", "Neem face wash",
                "Beard oil", "Kajal", "Body lotion"],
               (149, 899), 40, 0.05, 0.15),
    "home": (["Pressure cooker", "Steel bottle set", "Non-stick tawa", "Cotton bedsheet set", "Storage container set",
              "Dinner set", "Wall clock", "Cushion covers"],
             (249, 2999), 30, 0.08, 0.15),
    "grocery": (["Chakki atta", "Basmati rice", "Mustard oil", "Desi ghee", "Assam tea", "Dry fruits mix",
                 "Pinni gift box", "Rajma"],
                (99, 999), 12, 0.02, 0.15),
}
VARIANTS = {"Everyday": 0.6, "Classic": 1.0, "Premium": 1.8}

CITIES = {  # city: (weight, tier)
    "Ludhiana": (0.20, 2), "Amritsar": (0.14, 2), "Jalandhar": (0.12, 2), "Chandigarh": (0.10, 2),
    "Mohali": (0.10, 2), "Patiala": (0.10, 3), "Bathinda": (0.07, 3), "Delhi": (0.07, 1),
    "Hoshiarpur": (0.05, 3), "Moga": (0.05, 3),
}
MALE = ["Gurpreet", "Harpreet", "Jaspreet", "Manpreet", "Arjun", "Rohan", "Karan", "Ishaan", "Rajveer",
        "Amandeep", "Sukhwinder", "Navjot", "Aman", "Vikram", "Harjot", "Kabir", "Yuvraj", "Simranjit"]
FEMALE = ["Simran", "Harleen", "Jasleen", "Navneet", "Priya", "Neha", "Mehak", "Tanvi", "Ravneet",
          "Sukhmani", "Gurleen", "Ananya", "Kirandeep", "Rupinder", "Sanya", "Pooja", "Amrit", "Diya"]
SURNAMES = ["Singh", "Sharma", "Gill", "Sandhu", "Dhillon", "Grewal", "Sidhu", "Bajwa", "Arora", "Verma",
            "Malhotra", "Kapoor", "Bedi", "Chawla", "Brar", "Mann"]

# Festival dates in the last year (approximate). Spending rises in the days before.
FESTIVALS = [date(2025, 10, 20), date(2025, 11, 5), date(2026, 1, 13), date(2026, 3, 4),
             date(2026, 4, 14), date(2026, 8, 28), date(2026, 11, 8)]


def pick(rng: random.Random, weights: dict) -> str:
    keys = list(weights)
    return rng.choices(keys, weights=[weights[k] if not isinstance(weights[k], tuple) else weights[k][0]
                                      for k in keys])[0]


def make_products(rng: random.Random) -> list[tuple]:
    products = []
    for category, (items, (lo, hi), margin, _, _) in CATEGORIES.items():
        for item in items:
            base = math.exp(rng.uniform(math.log(lo), math.log(hi / 1.8)))
            for variant, mult in VARIANTS.items():
                price = int(round(base * mult / 10) * 10 - 1)
                m = max(3.0, rng.gauss(margin, margin * 0.15))
                products.append((len(products) + 1, f"{variant} {item.lower()}", category, max(price, 49), round(m, 1)))
    return products


def season(d: date, kind: str) -> float:
    m = 1.3 if d.weekday() >= 5 else 1.0          # weekends
    if d.day <= 5:                                 # payday
        m *= 1.35
    if any(0 <= (f - d).days <= 10 for f in FESTIVALS):
        m *= 3.0 if kind == "festival" else 1.4
    return m


def generate(n_customers: int = 500, seed: int = 42, path: str | Path | None = None) -> Path:
    path = Path(path) if path else db.db_path()
    if path.exists():
        path.unlink()
    conn = db.connect(path)
    rng = random.Random(seed)
    now = db.now_ist()
    today = now.date()

    products = make_products(rng)
    by_category: dict[str, list[tuple]] = {}
    for p in products:
        by_category.setdefault(p[2], []).append(p)
    conn.executemany("INSERT INTO products VALUES (?, ?, ?, ?, ?)", products)
    interest_weights = {c: v[4] for c, v in CATEGORIES.items()}

    customers, orders, carts = [], [], []
    for cid in range(1, n_customers + 1):
        kind = pick(rng, {k: t.share for k, t in TYPES.items()})
        t = TYPES[kind]
        gender = rng.choice("MF")
        first = rng.choice(MALE if gender == "M" else FEMALE)
        last = rng.choice([s for s in SURNAMES if s != "Singh"] + (["Singh"] * 4 if gender == "M" else ["Kaur"] * 4))
        city = pick(rng, CITIES)
        tier = CITIES[city][1]
        language = rng.choices(["en", "pa", "hi"], weights=[0.3, 0.45, 0.25] if tier == 3 else [0.45, 0.3, 0.25])[0]
        payment = rng.choices(["UPI", "COD", "card"], weights=[0.45, 0.45, 0.10] if tier == 3 else [0.60, 0.25, 0.15])[0]
        age = int(min(65, max(18, rng.gauss(31, 8))))
        signup = today - timedelta(days=rng.randint(1, 40) if kind == "new" else int(60 + rng.expovariate(1 / 400)))
        interest = rng.choice(["electronics", "fashion"]) if kind == "premium" else pick(rng, interest_weights)
        sensitivity = min(1.0, max(0.0, rng.gauss(t.price_sensitivity, 0.1)))

        # ── orders: a daily buying chance shaped by type, payday, weekends and festivals
        rate = t.orders_per_year * rng.gammavariate(2, 0.5)  # people within a type differ a lot
        median = t.median_order_inr * rng.lognormvariate(0, 0.3)
        active_until = today
        if kind == "drifting":
            active_until = today - timedelta(days=rng.randint(45, 120))
        elif kind == "dormant":
            active_until = today - timedelta(days=rng.randint(200, 400))
        start = max(signup, today - timedelta(days=365))
        order_days = []
        if kind == "new":
            if rng.random() < 0.5:
                order_days.append(signup + timedelta(days=rng.randint(0, (today - signup).days - 1 or 0)))
        else:
            d = start
            while d < today:
                activity = 1.0
                if d > active_until:
                    activity = math.exp(-(d - active_until).days / 20) if kind == "drifting" else 0.0
                if activity and rng.random() < rate / 365 * season(d, kind) * activity:
                    order_days.append(d)
                d += timedelta(days=1)

        earned = 0
        for d in order_days:
            category = interest if rng.random() < 0.55 else pick(rng, interest_weights)
            target = median * rng.lognormvariate(0, 0.5)
            product = min(rng.sample(by_category[category], 6), key=lambda p: abs(p[3] - target))
            qty = rng.randint(1, 3) if category == "grocery" else 1
            total = product[3] * qty
            coupon = rng.random() < 0.1 + 0.75 * sensitivity
            returned = rng.random() < CATEGORIES[category][3] * (1.3 if kind == "deal_hunter" else 1.0)
            orders.append((cid, d.isoformat(), product[0], category, qty, total, int(coupon),
                           round(total * 0.1) if coupon else 0, int(returned)))
            if not returned:
                earned += total // 100

        points = earned - round(earned * rng.uniform(0, 0.6))
        expiry = (today + timedelta(days=rng.randint(3, 120))).isoformat() if points > 0 else None
        customers.append((cid, first, last, gender, age, city, tier, language, payment, signup.isoformat(),
                          interest, points, expiry, kind, round(sensitivity, 2)))

        if rng.random() < t.p_cart:
            product = rng.choice(by_category[interest if rng.random() < 0.7 else pick(rng, interest_weights)])
            abandoned = now - timedelta(hours=rng.uniform(1, 96))
            carts.append((cid, product[0], 1, abandoned.isoformat(timespec="seconds")))

    conn.executemany("INSERT INTO customers VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", customers)
    conn.executemany(
        "INSERT INTO orders (customer_id, order_date, product_id, category, qty, total, used_coupon, discount_inr, "
        "returned) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", orders)
    conn.executemany("INSERT INTO carts VALUES (?, ?, ?, ?)", carts)
    conn.commit()
    conn.close()
    return path


def realism_report(conn) -> dict:
    """Key statistics to compare against typical e-commerce patterns."""
    spend = sorted((r[0] for r in conn.execute("SELECT SUM(total) FROM orders GROUP BY customer_id")), reverse=True)
    counts = [r[0] for r in conn.execute("SELECT COUNT(*) FROM orders GROUP BY customer_id")]
    totals = sorted(r[0] for r in conn.execute("SELECT total FROM orders"))
    n_customers = conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
    top = max(1, round(n_customers * 0.2))
    coupon = dict(conn.execute(
        "SELECT c.hidden_type, AVG(o.used_coupon) FROM orders o JOIN customers c ON c.id = o.customer_id "
        "GROUP BY c.hidden_type").fetchall())
    returns = dict(conn.execute("SELECT category, AVG(returned) FROM orders GROUP BY category").fetchall())
    weekday = Counter(date.fromisoformat(r[0]).weekday() >= 5 for r in conn.execute("SELECT order_date FROM orders"))
    cod = dict(conn.execute(
        "SELECT city_tier, AVG(payment_pref = 'COD') FROM customers GROUP BY city_tier").fetchall())
    return {
        "customers": n_customers,
        "orders": len(totals),
        "top20_revenue_share": sum(spend[:top]) / sum(spend),
        "buyers_share": len(counts) / n_customers,
        "repeat_buyer_share": sum(c >= 2 for c in counts) / len(counts),
        "median_order_inr": totals[len(totals) // 2],
        "coupon_share_by_type": coupon,
        "return_rate_by_category": returns,
        "weekend_lift": (weekday[True] / 2) / (weekday[False] / 5),
        "cod_share_by_tier": cod,
        "abandoned_cart_share": conn.execute("SELECT COUNT(*) FROM carts").fetchone()[0] / n_customers,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--customers", type=int, default=500)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--db", default=None, help="SQLite file (default: DB_PATH or data/offers.db)")
    args = parser.parse_args()

    path = generate(args.customers, args.seed, args.db)
    report = realism_report(db.connect(path))
    print(f"Wrote {path}")
    for key, value in report.items():
        if isinstance(value, dict):
            value = ", ".join(f"{k}={v:.2f}" for k, v in value.items())
        elif isinstance(value, float):
            value = f"{value:.2f}"
        print(f"  {key:24} {value}")


if __name__ == "__main__":
    main()
