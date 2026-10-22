from datetime import datetime

import pytest

from data.generate import generate
from engine.db import IST
from engine.features import Profile
from engine.rules import load_offers, load_rules

NOW = datetime(2026, 10, 22, 14, 0, tzinfo=IST)
MARGINS = {"fashion": 45, "electronics": 9, "beauty": 40, "home": 30, "grocery": 12}


def _profile(**overrides) -> Profile:
    """A lapsed fashion shopper who likes coupons and has a cart and expiring points."""
    fields = dict(
        customer_id=1, first_name="Simran", city="Ludhiana", language="pa", days_since_signup=300,
        orders=6, avg_order_inr=1200, days_since_last_order=75, typical_gap_days=30, discount_share=0.6,
        return_rate=0.1, fav_category="fashion", lifecycle="lapsed", points=250, points_expire_in_days=10,
        cart_product="Premium running shoes", cart_category="fashion", cart_value=2499, cart_hours_ago=5,
    )
    fields.update(overrides)
    return Profile(**fields)


@pytest.fixture
def make_profile():
    return _profile


@pytest.fixture
def rules():
    return load_rules()


@pytest.fixture
def offers():
    return load_offers()


@pytest.fixture(scope="session")
def generated_db(tmp_path_factory):
    path = tmp_path_factory.mktemp("data") / "test.db"
    generate(500, seed=7, path=path)
    return path
