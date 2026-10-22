"""The synthetic data should behave like real e-commerce data."""

from data.generate import realism_report
from engine import db


def test_data_looks_like_real_customers(generated_db):
    r = realism_report(db.connect(generated_db))
    assert 0.50 <= r["top20_revenue_share"] <= 0.80            # a few customers bring most revenue
    assert 400 <= r["median_order_inr"] <= 2000
    assert r["coupon_share_by_type"]["deal_hunter"] > r["coupon_share_by_type"]["loyal"] + 0.3
    assert r["return_rate_by_category"]["fashion"] > r["return_rate_by_category"]["electronics"]
    assert r["weekend_lift"] > 1.1                              # people shop more at weekends
    assert r["cod_share_by_tier"][3] > r["cod_share_by_tier"][1]  # cash on delivery in smaller towns
    assert 0.2 <= r["abandoned_cart_share"] <= 0.5
