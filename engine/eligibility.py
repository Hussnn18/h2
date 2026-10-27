"""Step 1: which offers is this customer allowed to get, and within what range?

Everything the AI is later shown comes from here, so the AI can only ever
choose between offers the rules already allow.
"""

import math
from dataclasses import dataclass, field
from datetime import datetime

from engine.features import Profile
from engine.rules import CONDITIONS, DISCOUNT_KINDS, Offer, Rules, describe_condition

DEFAULT_ORDER_INR = 800  # for customers with no orders and no cart


@dataclass
class Allowed:
    offer_id: str
    offer: Offer
    category: str
    order_value: float          # the order this offer is expected to apply to
    min_value: int | None
    max_value: int | None
    max_discount_inr: int
    pct_cap: float | None = None   # discount cap for this category, for explanations
    qualifies: list[str] = field(default_factory=list)

    def cost(self, value: int | None) -> float:
        """Expected value given away if the customer redeems the offer."""
        kind = self.offer.kind
        if kind == "percent_off":
            return min(value / 100 * self.order_value, self.max_discount_inr)
        if kind == "flat_off":
            return float(value)
        if kind == "bonus_points":  # 1 point per Rs 100, 1 point = Rs 1
            return (value - 1) / 100 * self.order_value
        return float(self.offer.cost_inr)


@dataclass
class Eligibility:
    allowed: dict[str, Allowed]
    blocked: list[str]          # reasons no offer at all may be sent right now
    skipped: dict[str, str]     # offer_id -> why that offer is not allowed


def check(p: Profile, rules: Rules, offers: dict[str, Offer], margins: dict[str, float],
          budget_left: float, now: datetime, trigger: str = "pull") -> Eligibility:
    f = rules.frequency
    blocked = []
    if p.opted_out:
        blocked.append("you have opted out of offers")
    if p.offers_today >= f.max_offers_per_customer_per_day:
        blocked.append(f"you've had {p.offers_today} offers today (limit {f.max_offers_per_customer_per_day})")
    if p.minutes_since_last_offer is not None and p.minutes_since_last_offer < f.min_minutes_between_offers:
        blocked.append(f"your last offer was under {f.min_minutes_between_offers:g} min ago")
    if trigger == "push" and f.quiet_hours.contains(now.time()):
        blocked.append(f"quiet hours ({f.quiet_hours.start}–{f.quiet_hours.end})")
    if blocked:
        return Eligibility({}, blocked, {})

    allowed, skipped = {}, {}
    for offer_id, offer in offers.items():
        if not offer.enabled:
            skipped[offer_id] = "switched off by the marketing team"
            continue
        unmet =[describe_condition(n, v) for n, v in offer.eligible_when.items() if not CONDITIONS[n][1](p, v)]
        if unmet:
            skipped[offer_id] = "needs a customer who " + "; ".join(unmet)
            continue

        if offer.kind in DISCOUNT_KINDS:
            t = rules.targeting
            if p.days_since_last_order is not None and p.days_since_last_order <= t.no_discount_if_ordered_within_days:
                skipped[offer_id] = f"ordered {p.days_since_last_order} days ago, likely to buy without a discount"
                continue
            if p.return_rate > t.block_discounts_if_return_rate_above:
                skipped[offer_id] = f"return rate {p.return_rate:.0%} is above the limit"
                continue

        category = p.cart_category or p.fav_category
        a = Allowed(
            offer_id=offer_id,
            offer=offer,
            category=category,
            order_value=max(p.cart_value or p.avg_order_inr or DEFAULT_ORDER_INR, offer.min_order_inr),
            min_value=offer.range[0] if offer.range else None,
            max_value=offer.range[1] if offer.range else None,
            max_discount_inr=rules.limits.max_discount_inr,
            qualifies=[describe_condition(n, v) for n, v in offer.eligible_when.items()],
        )

        if offer.kind in DISCOUNT_KINDS:
            lim = rules.limits
            a.pct_cap = min(lim.max_discount_percent,
                            rules.category_max_discount_percent.get(category, lim.max_discount_percent),
                            margins.get(category, 0) - lim.min_margin_left_percent)
            if offer.kind == "percent_off":
                a.max_value = min(a.max_value, math.floor(a.pct_cap))
            else:
                a.max_value = min(a.max_value, lim.max_discount_inr,
                                  math.floor(a.pct_cap / 100 * max(offer.min_order_inr, 1)))

        if offer.has_value:
            while a.max_value >= a.min_value and a.cost(a.max_value) > budget_left:
                a.max_value -= 1
            if a.max_value < a.min_value:
                skipped[offer_id] = f"limits for {category} or today's remaining budget leave no valid value"
                continue
        elif a.cost(None) > budget_left:
            skipped[offer_id] = "today's discount budget is used up"
            continue

        allowed[offer_id] = a
    return Eligibility(allowed, [], skipped)
