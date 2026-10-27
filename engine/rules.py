"""Load and check the marketing-editable files in config/.

They are re-read for every decision, so an edit takes effect on the next offer.
A typo gives a clear error instead of a silently ignored rule.
"""

import re
from datetime import time
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"

OfferKind = Literal["percent_off", "flat_off", "free_delivery", "bonus_points", "reminder", "early_access"]
VALUE_KINDS = {"percent_off", "flat_off", "bonus_points"}
DISCOUNT_KINDS = {"percent_off", "flat_off"}


def _listed(v: Any) -> str:
    return " or ".join(v) if isinstance(v, list) else str(v)


# The conditions marketing can use under `eligible_when:` in offers.yaml:
# name -> (plain-English description, test on the customer profile)
CONDITIONS = {
    "has_cart": ("has items waiting in their cart",
                 lambda p, v: (p.cart_product is not None) == bool(v)),
    "min_orders": ("has ordered at least {v} times",
                   lambda p, v: p.orders >= v),
    "max_orders": ("has ordered at most {v} times",
                   lambda p, v: p.orders <= v),
    "min_days_since_last_order": ("last ordered {v}+ days ago",
                                  lambda p, v: p.days_since_last_order is not None and p.days_since_last_order >= v),
    "max_days_since_signup": ("signed up in the last {v} days",
                              lambda p, v: p.days_since_signup <= v),
    "min_points": ("has at least {v} reward points",
                   lambda p, v: p.points >= v),
    "points_expire_within_days": ("has points expiring within {v} days",
                                  lambda p, v: p.points_expire_in_days is not None and p.points_expire_in_days <= v),
    "min_avg_order_inr": ("usually spends ₹{v}+ per order",
                          lambda p, v: p.avg_order_inr >= v),
    "lifecycle_in": ("is currently {v}",
                     lambda p, v: p.lifecycle in v),
}


def describe_condition(name: str, value: Any) -> str:
    return CONDITIONS[name][0].format(v=_listed(value)).replace("_", " ")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Limits(_Strict):
    max_discount_percent: float = Field(ge=0, le=90)
    max_discount_inr: int = Field(ge=0)
    min_margin_left_percent: float = Field(ge=0, le=100)


class Budget(_Strict):
    daily_discount_budget_inr: int = Field(ge=0)


class QuietHours(_Strict):
    start: str
    end: str

    @field_validator("start", "end")
    @classmethod
    def _hhmm(cls, v: str) -> str:
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", v):
            raise ValueError("use 24-hour HH:MM, for example \"22:00\"")
        return v

    def contains(self, t: time) -> bool:
        start, end = time.fromisoformat(self.start), time.fromisoformat(self.end)
        if start > end:  # crosses midnight, e.g. 22:00-08:00
            return t >= start or t < end
        return start <= t < end


class Frequency(_Strict):
    max_offers_per_customer_per_day: int = Field(ge=0)
    min_minutes_between_offers: float = Field(ge=0)
    quiet_hours: QuietHours


class Targeting(_Strict):
    no_discount_if_ordered_within_days: int = Field(ge=0)
    block_discounts_if_return_rate_above: float = Field(ge=0, le=1)


class Messages(_Strict):
    max_length: int = Field(ge=50, le=1000)
    banned_phrases: list[str] = []


class Rules(_Strict):
    limits: Limits
    category_max_discount_percent: dict[str, float] = {}
    budget: Budget
    frequency: Frequency
    targeting: Targeting
    messages: Messages


class Offer(_Strict):
    name: str
    enabled: bool = True
    kind: OfferKind
    goal: str
    range: tuple[int, int] | None = None
    min_order_inr: int = Field(default=0, ge=0)
    cost_inr: int = Field(default=0, ge=0)
    valid_hours: int = Field(default=48, gt=0)
    eligible_when: dict[str, Any] = {}
    fallback_message: str

    @property
    def has_value(self) -> bool:
        return self.kind in VALUE_KINDS

    @model_validator(mode="after")
    def _check(self) -> "Offer":
        if self.has_value and self.range is None:
            raise ValueError(f"offers of kind '{self.kind}' need a range, e.g. range: [5, 15]")
        if not self.has_value and self.range is not None:
            raise ValueError(f"offers of kind '{self.kind}' have no value; remove 'range'")
        if self.range and self.range[0] > self.range[1]:
            raise ValueError("range must be [lowest, highest]")
        unknown = set(self.eligible_when) - set(CONDITIONS)
        if unknown:
            raise ValueError(f"unknown condition(s) {sorted(unknown)}; use one of {sorted(CONDITIONS)}")
        return self


def _read_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def load_rules(config_dir: Path = CONFIG_DIR) -> Rules:
    return Rules.model_validate(_read_yaml(Path(config_dir) / "rules.yaml"))


def load_offers(config_dir: Path = CONFIG_DIR) -> dict[str, Offer]:
    data = _read_yaml(Path(config_dir) / "offers.yaml")
    return {offer_id: Offer.model_validate(o) for offer_id, o in data["offers"].items()}
