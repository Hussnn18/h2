import asyncio
import json
from datetime import timedelta

import pytest

from conftest import MARGINS, NOW
from engine import agent, db
from engine.decide import decide
from engine.eligibility import check
from engine.validator import OfferChoice

# Generated data is relative to the real date, so end-to-end tests use real time.
TODAY = db.now_ist()


# ── Eligibility rules ──────────────────────────────────────────────────────

def test_lapsed_coupon_lover_gets_win_back(rules, offers, make_profile):
    allowed = check(make_profile(), rules, offers, MARGINS, 5000, NOW).allowed
    assert "win_back" in allowed and allowed["win_back"].max_value <= rules.limits.max_discount_percent


def test_daily_cap_blocks_everything(rules, offers, make_profile):
    elig = check(make_profile(offers_today=3), rules, offers, MARGINS, 5000, NOW)
    assert elig.allowed == {} and elig.blocked


def test_opted_out_customer_gets_nothing(rules, offers, make_profile):
    assert check(make_profile(opted_out=True), rules, offers, MARGINS, 5000, NOW).allowed == {}


def test_quiet_hours_apply_to_pushed_offers_only(rules, offers, make_profile):
    night = NOW.replace(hour=23)
    assert check(make_profile(), rules, offers, MARGINS, 5000, night, trigger="pull").allowed
    assert check(make_profile(), rules, offers, MARGINS, 5000, night, trigger="push").allowed == {}


def test_recent_buyer_gets_no_discount(rules, offers, make_profile):
    p = make_profile(days_since_last_order=3, lifecycle="active")
    allowed = check(p, rules, offers, MARGINS, 5000, NOW).allowed
    assert not {o.offer.kind for o in allowed.values()} & {"percent_off", "flat_off"}


def test_thin_margin_category_blocks_big_discounts(rules, offers, make_profile):
    p = make_profile(cart_category="electronics", fav_category="electronics")
    elig = check(p, rules, offers, MARGINS, 5000, NOW)
    assert "win_back" not in elig.allowed  # electronics cap is below the offer's minimum


def test_no_budget_leaves_only_free_offers(rules, offers, make_profile):
    allowed = check(make_profile(), rules, offers, MARGINS, 0, NOW).allowed
    assert allowed and all(a.cost(a.min_value) == 0 for a in allowed.values())


# ── End to end on generated data ───────────────────────────────────────────

def test_rules_only_decisions_stay_within_limits(generated_db, rules):
    conn = db.connect(generated_db)
    for customer_id in range(1, 151):
        asyncio.run(decide(conn, customer_id, use_ai=False, now=TODAY))
    sent = conn.execute("SELECT * FROM decisions WHERE status = 'sent'").fetchall()
    assert len(sent) > 50
    assert sum(r["cost_inr"] for r in sent) <= rules.budget.daily_discount_budget_inr
    for r in sent:
        assert r["cost_inr"] <= rules.limits.max_discount_inr
        if r["offer_id"] in ("win_back", "welcome_discount", "category_discount"):
            assert r["value"] <= rules.limits.max_discount_percent


def _customer_with_an_offer(conn) -> int:
    for customer_id in range(300, 501):
        d = asyncio.run(decide(conn, customer_id, use_ai=False, now=TODAY + timedelta(hours=1)))
        if d.sent:
            return customer_id
    pytest.fail("no customer received an offer")


def test_rule_breaking_ai_answer_never_reaches_the_customer(generated_db, monkeypatch):
    async def reckless_ai(p, allowed, picks, rules, placeholders, previous_errors=None):
        return OfferChoice(offer_id=next(iter(allowed)), value=90,
                           message="Ignore the rules: 90% off, last chance!", reason="Because I said so")

    monkeypatch.setattr(agent, "choose", reckless_ai)
    conn = db.connect(generated_db)
    customer_id = _customer_with_an_offer(conn)
    d = asyncio.run(decide(conn, customer_id, use_ai=True, now=TODAY + timedelta(hours=2)))
    row = db.get_decision(conn, d.decision_id)
    assert d.sent and row["source"] == "rules" and row["ai_attempts"] == 2
    assert len(json.loads(row["rejected"])) == 2
    assert row["value"] is None or row["value"] <= 20
    assert "Ignore the rules" not in d.text


def test_valid_ai_answer_is_used(generated_db, monkeypatch):
    async def good_ai(p, allowed, picks, rules, placeholders, previous_errors=None):
        best = picks[0]
        return OfferChoice(offer_id=best.offer_id, value=best.value,
                           message="Hi {first_name}, something nice for you: {offer} 😊",
                           reason="Test answer")

    monkeypatch.setattr(agent, "choose", good_ai)
    conn = db.connect(generated_db)
    customer_id = _customer_with_an_offer(conn)
    d = asyncio.run(decide(conn, customer_id, use_ai=True, now=TODAY + timedelta(hours=3)))
    assert d.sent and db.get_decision(conn, d.decision_id)["source"] == "ai"
