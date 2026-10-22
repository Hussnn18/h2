"""The rule checker: nothing reaches a customer without passing validate().

It re-checks the hard limits from rules.yaml itself instead of trusting the
eligibility step, and checks the message for anything the AI may not write:
numbers, prices and codes only ever come from our own code via placeholders.
"""

import re

from pydantic import BaseModel, Field

from engine.eligibility import Allowed
from engine.rules import DISCOUNT_KINDS, Rules

PLACEHOLDER = re.compile(r"\{(\w*)\}")
NUMBER_WORDS = re.compile(
    r"\b(zero|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|twenty|thirty|forty|fifty|"
    r"sixty|seventy|eighty|ninety|hundred|thousand|lakh|crore|half|double|triple|twice)\b", re.IGNORECASE)
MONEY_OR_PERCENT = re.compile(r"[%₹]|\brs\b\.?|\binr\b|\brupees?\b|\bper\s?cent\b|\bpercent\b", re.IGNORECASE)
LINK = re.compile(r"https?://|www\.|@\w", re.IGNORECASE)
NEEDS_OFFER_PLACEHOLDER = {"percent_off", "flat_off", "free_delivery", "bonus_points", "early_access"}


class OfferChoice(BaseModel):
    """What the AI (or the rules-only fallback) proposes to send."""
    offer_id: str = Field(description='An offer_id from allowed_offers, or "none" to send nothing')
    value: int | None = Field(description="Whole number inside that offer's value_range; null if it has no range")
    message: str = Field(description="The Telegram message, using placeholders and containing no numbers")
    reason: str = Field(description="One plain sentence for the marketing team: why this offer for this customer")


def validate(choice: OfferChoice, allowed: dict[str, Allowed], rules: Rules, budget_left: float,
             placeholders: set[str]) -> list[str]:
    """Return a list of problems; an empty list means the choice may be sent."""
    if choice.offer_id == "none":
        return []
    a = allowed.get(choice.offer_id)
    if a is None:
        return [f"offer '{choice.offer_id}' is not allowed for this customer "
                f"(allowed: {', '.join(allowed) or 'none'})"]

    errors = []
    kind = a.offer.kind
    if a.offer.has_value:
        v = choice.value
        if v is None:
            errors.append("this offer needs a value")
        else:
            if not a.min_value <= v <= a.max_value:
                errors.append(f"value {v} is outside the allowed range {a.min_value}-{a.max_value}")
            if kind == "percent_off" and v > rules.limits.max_discount_percent:
                errors.append(f"{v}% is above the company maximum of {rules.limits.max_discount_percent:g}%")
            if kind in DISCOUNT_KINDS and a.cost(v) > rules.limits.max_discount_inr:
                errors.append(f"discount worth ₹{a.cost(v):.0f} is above the ₹{rules.limits.max_discount_inr} maximum")
            if a.cost(v) > budget_left:
                errors.append(f"costs about ₹{a.cost(v):.0f} but only ₹{budget_left:.0f} of today's budget is left")
    elif a.cost(None) > budget_left:
        errors.append("today's discount budget is used up")

    return errors + check_message(choice.message, kind, rules, placeholders)


def check_message(text: str, kind: str, rules: Rules, placeholders: set[str]) -> list[str]:
    errors = []
    text = text.strip()
    if len(text) < 10:
        errors.append("message is empty or too short")
    if len(text) > rules.messages.max_length:
        errors.append(f"message is {len(text)} characters; the limit is {rules.messages.max_length}")

    used = set(PLACEHOLDER.findall(text))
    unknown = used - placeholders
    if unknown:
        errors.append("unknown or unavailable placeholder(s): " + ", ".join(f"{{{u}}}" for u in sorted(unknown)))
    if kind in NEEDS_OFFER_PLACEHOLDER and "offer" not in used:
        errors.append("message must name the offer using {offer}")

    bare = PLACEHOLDER.sub(" ", text)
    if "{" in bare or "}" in bare:
        errors.append("message has stray curly braces")
    if re.search(r"\d", bare):
        errors.append("message contains digits; amounts and dates must come from placeholders")
    if MONEY_OR_PERCENT.search(bare):
        errors.append("message mentions money or percentages directly; use {offer}")
    if m := NUMBER_WORDS.search(bare):
        errors.append(f"message spells out a number ('{m.group(0)}')")
    for phrase in rules.messages.banned_phrases:
        if phrase.lower() in bare.lower():
            errors.append(f"banned phrase: '{phrase}'")
    if LINK.search(bare):
        errors.append("message contains a link or @mention")
    return errors
