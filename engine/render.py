"""Turn a validated choice into the final Telegram message (HTML).

Every number the customer sees (discount, minimum order, code, expiry) is
written here by code, never by the AI.
"""

import html
import random
from datetime import datetime

from engine.eligibility import Allowed
from engine.features import Profile
from engine.validator import PLACEHOLDER

EMOJI = {"percent_off": "🎁", "flat_off": "💸", "free_delivery": "🚚", "bonus_points": "⭐",
         "reminder": "🛒", "early_access": "🔑"}
CODE_PREFIX = {"percent_off": "SAVE", "flat_off": "FLAT", "free_delivery": "SHIP", "bonus_points": "PTS",
               "early_access": "VIP"}
CODE_CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def offer_text(a: Allowed, value: int | None) -> str:
    kind = a.offer.kind
    if kind == "percent_off":
        return f"{value}% off"
    if kind == "flat_off":
        return f"₹{value} off"
    if kind == "free_delivery":
        return "FREE delivery"
    if kind == "bonus_points":
        return f"{value}x reward points"
    if kind == "early_access":
        return "early access to our next big sale"
    return a.offer.name


def available_placeholders(p: Profile) -> set[str]:
    names = {"first_name", "category", "offer"}
    if p.cart_product:
        names.add("product")
    if p.points:
        names.add("points")
    return names


def new_code(kind: str, rng: random.Random = random.Random()) -> str | None:
    prefix = CODE_PREFIX.get(kind)
    return f"{prefix}-{''.join(rng.choices(CODE_CHARS, k=4))}" if prefix else None


def render(message: str, a: Allowed, value: int | None, p: Profile, code: str | None, expires_at: datetime) -> str:
    values = {"first_name": p.first_name, "category": a.category, "offer": offer_text(a, value),
              "product": (p.cart_product or "").lower(), "points": f"{p.points:,}"}

    def fill(m):
        text = html.escape(values[m.group(1)])
        return f"<b>{text}</b>" if m.group(1) == "offer" else text

    body = PLACEHOLDER.sub(fill, html.escape(message.strip()))
    if code is None:
        return body

    kind = a.offer.kind
    headline = a.offer.name if kind == "early_access" else offer_text(a, value)
    if kind in ("percent_off", "flat_off"):
        headline += f" on {a.category}"
    elif kind == "bonus_points":
        headline += " on your next order"
    terms = []
    if a.offer.min_order_inr:
        terms.append(f"min order ₹{a.offer.min_order_inr:,}")
    if kind == "percent_off":
        terms.append(f"up to ₹{a.max_discount_inr:,} off")
    terms.append("valid till " + expires_at.strftime("%a %d %b, %I:%M %p"))
    return (f"{body}\n\n{EMOJI[kind]} <b>{html.escape(headline)}</b>\n"
            f"Code: <code>{code}</code>\n<i>{html.escape(' · '.join(terms))}</i>")
