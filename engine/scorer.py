"""Rules-only choice: estimate the extra profit each allowed offer would earn.

    extra profit = (P(buy | offer) - P(buy | no offer)) x order value x margin
                   - P(buy | offer) x cost of the offer

The second term is why blanket discounts lose money: people who would have
bought anyway still redeem the discount. If no offer earns more than it costs,
the best choice is to send nothing.
"""

from dataclasses import dataclass

from engine.eligibility import Allowed
from engine.features import Profile
from engine.validator import OfferChoice

BASE_BUY = {"active": 0.45, "at_risk": 0.20, "lapsed": 0.08, "dormant": 0.03, "new": 0.15}


@dataclass
class Pick:
    offer_id: str
    value: int | None
    extra_profit: float
    base_prob: float
    uplift: float


def base_buy_probability(p: Profile) -> float:
    return min(BASE_BUY[p.lifecycle] + (0.15 if p.cart_product else 0), 0.9)


def uplift(p: Profile, a: Allowed, value: int | None, base: float) -> float:
    """How much more likely the customer is to buy because of this offer (rough heuristic)."""
    sensitivity = 0.2 + 0.8 * p.discount_share if p.orders else 0.6
    room = 1 - base
    kind = a.offer.kind
    if kind == "percent_off":
        return room * sensitivity * 1.2 * value / 100
    if kind == "flat_off":
        return room * sensitivity * 1.2 * value / a.order_value
    if kind == "free_delivery":
        return room * sensitivity * 0.12
    if kind == "bonus_points":
        return room * (1 - sensitivity) * 0.03 * (value - 1) + 0.01
    if kind == "early_access":
        return room * (1 - sensitivity) * 0.06
    return room * 0.08  # reminders


def candidate_values(a: Allowed) -> list[int | None]:
    if a.min_value is None:
        return [None]
    step = 10 if a.offer.kind == "flat_off" else 1
    return sorted(set(range(a.min_value, a.max_value + 1, step)) | {a.max_value})


def rank(p: Profile, allowed: dict[str, Allowed], margins: dict[str, float]) -> list[Pick]:
    """Best value for each allowed offer, most profitable first."""
    base = base_buy_probability(p)
    picks = []
    for a in allowed.values():
        margin = margins.get(a.category, 30) / 100
        best = None
        for value in candidate_values(a):
            up = uplift(p, a, value, base)
            profit = up * a.order_value * margin - (base + up) * a.cost(value)
            if best is None or profit > best.extra_profit:
                best = Pick(a.offer_id, value, profit, base, up)
        picks.append(best)
    return sorted(picks, key=lambda x: x.extra_profit, reverse=True)


def as_choice(picks: list[Pick], allowed: dict[str, Allowed]) -> OfferChoice:
    if not picks or picks[0].extra_profit <= 0:
        return OfferChoice(offer_id="none", value=None, message="",
                           reason="No allowed offer is expected to earn more than it costs for this customer.")
    best = picks[0]
    offer = allowed[best.offer_id].offer
    return OfferChoice(
        offer_id=best.offer_id, value=best.value, message=offer.fallback_message,
        reason=f"{offer.goal}. Highest expected extra profit of the offers allowed (about ₹{best.extra_profit:.0f}).",
    )
