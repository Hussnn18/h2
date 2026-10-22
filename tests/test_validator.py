"""The validator is the last line of defence: try hard to get bad offers past it."""

import pytest

from conftest import MARGINS, NOW
from engine.eligibility import check
from engine.render import available_placeholders
from engine.validator import OfferChoice, check_message, validate

GOOD_MESSAGE = "Sat Sri Akal {first_name}! We've missed you. Enjoy {offer} on {category} 💛"


@pytest.fixture
def setup(rules, offers, make_profile):
    p = make_profile()
    allowed = check(p, rules, offers, MARGINS, budget_left=5000, now=NOW).allowed
    return allowed, available_placeholders(p)


def choice(**overrides) -> OfferChoice:
    fields = dict(offer_id="win_back", value=15, message=GOOD_MESSAGE, reason="Lapsed customer who likes coupons.")
    fields.update(overrides)
    return OfferChoice(**fields)


def test_valid_choice_passes(setup, rules):
    allowed, ph = setup
    assert validate(choice(), allowed, rules, 5000, ph) == []


def test_value_above_range_is_blocked(setup, rules):
    allowed, ph = setup
    errors = validate(choice(value=50), allowed, rules, 5000, ph)
    assert any("outside the allowed range" in e for e in errors)


def test_company_maximum_holds_even_if_eligibility_is_wrong(setup, rules):
    allowed, ph = setup
    allowed["win_back"].max_value = 60  # pretend the eligibility step had a bug
    errors = validate(choice(value=40), allowed, rules, 5000, ph)
    assert any("company maximum" in e for e in errors)


def test_offer_not_in_allowed_list_is_blocked(setup, rules):
    allowed, ph = setup
    assert validate(choice(offer_id="free_iphone"), allowed, rules, 5000, ph)


def test_budget_is_enforced(setup, rules):
    allowed, ph = setup
    errors = validate(choice(), allowed, rules, budget_left=10, placeholders=ph)
    assert any("budget" in e for e in errors)


@pytest.mark.parametrize("message", [
    "Get 50% off everything with {offer}!",
    "Enjoy {offer}, that's fifty percent!",
    "Save Rs 200 today with {offer}",
    "Use code SAVE20 for {offer}",
    "{offer} for you, {first_name}. Last chance!",
    "{offer} at https://example.com",
    "Hi {first_name}, {offer} with {coupon}",
    "Hi {first_name}, half price on everything {offer}",
    "Hi {first_name}, enjoy {offer} ₹ off",
    "Hi {first_name} {offer} {",
])
def test_bad_messages_are_blocked(setup, rules, message):
    allowed, ph = setup
    assert validate(choice(message=message), allowed, rules, 5000, ph)


def test_offer_must_be_named(setup, rules):
    allowed, ph = setup
    errors = validate(choice(message="We've missed you, {first_name}!"), allowed, rules, 5000, ph)
    assert any("{offer}" in e for e in errors)


def test_product_placeholder_needs_a_cart(rules, offers, make_profile):
    p = make_profile(cart_product=None, cart_category=None, cart_value=0, cart_hours_ago=None)
    allowed = check(p, rules, offers, MARGINS, budget_left=5000, now=NOW).allowed
    errors = validate(choice(message="Your {product} is waiting, {offer}"), allowed, rules, 5000,
                      available_placeholders(p))
    assert any("placeholder" in e for e in errors)


def test_sending_nothing_is_always_allowed(setup, rules):
    allowed, ph = setup
    assert validate(OfferChoice(offer_id="none", value=None, message="", reason="x"), allowed, rules, 0, ph) == []


def test_every_fallback_message_passes(rules, offers):
    """Marketing-written templates in offers.yaml must pass the same checks as the AI."""
    ph = {"first_name", "category", "offer", "product", "points"}
    for offer_id, offer in offers.items():
        assert check_message(offer.fallback_message, offer.kind, rules, ph) == [], offer_id
