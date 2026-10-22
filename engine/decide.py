"""One offer decision, end to end:

    profile -> eligibility (rules) -> AI choice -> validator -> render -> audit log

If the AI is off, fails, or is rejected twice by the validator, the rules-only
pick is used, and that pick passes through the same validator.
"""

import html
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta

from engine import agent, db, eligibility, render, scorer
from engine.features import build_profile
from engine.rules import load_offers, load_rules
from engine.validator import OfferChoice, validate

AI_ATTEMPTS = 2


@dataclass
class Decision:
    sent: bool
    text: str                  # HTML for the customer: the offer, or why there is none
    decision_id: int | None = None


async def decide(conn: sqlite3.Connection, customer_id: int, chat_id: int | None = None, trigger: str = "pull",
                 now: datetime | None = None, use_ai: bool | None = None) -> Decision:
    now = now or db.now_ist()
    rules, offers = load_rules(), load_offers()
    margins = db.category_margins(conn)
    p = build_profile(conn, customer_id, now)
    budget_left = rules.budget.daily_discount_budget_inr - db.budget_used_today(conn, now)
    elig = eligibility.check(p, rules, offers, margins, budget_left, now, trigger)
    record = dict(created_at=now.isoformat(), chat_id=chat_id, customer_id=customer_id, trigger=trigger)

    if not elig.allowed:
        reason = "; ".join(elig.blocked) or "none of our current offers fit your profile"
        db.save_decision(conn, **record, source="blocked", status="not_sent", why=reason,
                         rejected=json.dumps(elig.skipped))
        return Decision(False, f"🙂 No offer for you right now: {html.escape(reason)}.")

    picks = scorer.rank(p, elig.allowed, margins)
    placeholders = render.available_placeholders(p)
    choice, source, attempts, rejected = None, "rules", 0, []

    ai_on = agent.enabled() if use_ai is None else use_ai
    if ai_on:
        errors = None
        while attempts < AI_ATTEMPTS:
            attempts += 1
            proposal = await agent.choose(p, elig.allowed, picks, rules, placeholders, errors)
            if proposal is None:
                break
            errors = validate(proposal, elig.allowed, rules, budget_left, placeholders)
            if not errors:
                choice, source = proposal, "ai"
                break
            rejected.append({"proposal": proposal.model_dump(), "errors": errors})

    if choice is None:
        choice = scorer.as_choice(picks, elig.allowed)
        errors = validate(choice, elig.allowed, rules, budget_left, placeholders)
        if errors:  # e.g. marketing wrote a number into a fallback_message
            rejected.append({"proposal": choice.model_dump(), "errors": errors})
            db.save_decision(conn, **record, source="rules", offer_id=choice.offer_id, status="not_sent",
                             why="fallback offer failed the rule check", ai_attempts=attempts,
                             rejected=json.dumps(rejected, ensure_ascii=False))
            return Decision(False, "🙂 No offer for you right now.")

    if choice.offer_id == "none":
        db.save_decision(conn, **record, source=source, offer_id="none", status="not_sent", reason=choice.reason,
                         why=choice.reason, ai_attempts=attempts, rejected=json.dumps(rejected, ensure_ascii=False))
        return Decision(False, "🙂 No offer for you right now. Our engine thinks a discount wouldn't change "
                               "much for you today, so we're not spamming you 😉")

    a = elig.allowed[choice.offer_id]
    value = choice.value if a.offer.has_value else None
    code = render.new_code(a.offer.kind)
    expires = now + timedelta(hours=a.offer.valid_hours)
    text = render.render(choice.message, a, value, p, code, expires)
    why = explain(choice, source, rejected, a, value, rules, budget_left, p)
    decision_id = db.save_decision(
        conn, **record, source=source, offer_id=a.offer_id, value=value, cost_inr=round(a.cost(value), 2),
        code=code, expires_at=expires.isoformat(), message=text, reason=choice.reason, why=why,
        ai_attempts=attempts, rejected=json.dumps(rejected, ensure_ascii=False), status="sent")
    return Decision(True, text, decision_id)


def explain(choice: OfferChoice, source: str, rejected: list, a: eligibility.Allowed, value: int | None,
            rules, budget_left: float, p) -> str:
    """The 'Why this offer?' text: the reason plus the rules it passed."""
    e = html.escape
    if source == "ai":
        picked_by = "the AI agent (Claude), then checked against every rule ✅"
    elif rejected:
        picked_by = "the rules engine, because the AI's suggestion broke a rule: " + e("; ".join(rejected[-1]["errors"]))
    else:
        picked_by = "the rules engine (expected extra profit)"
    lines = [f"🤔 <b>Why this offer?</b>", e(choice.reason), "", f"<b>Picked by:</b> {picked_by}", "<b>Rules passed:</b>"]
    if a.qualifies:
        lines.append("• you " + e(", ".join(a.qualifies)))
    if a.pct_cap is not None:
        lines.append(f"• discount within the {a.pct_cap:g}% cap for {e(a.category)} "
                     f"(company max {rules.limits.max_discount_percent:g}%, ₹{rules.limits.max_discount_inr})")
    lines.append(f"• cost about ₹{a.cost(value):.0f}, today's budget left ₹{budget_left:,.0f}")
    lines.append(f"• offer {p.offers_today + 1} of max {rules.frequency.max_offers_per_customer_per_day} today")
    return "\n".join(lines)
