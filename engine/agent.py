"""Step 2: Claude picks one offer from the allowed list and writes the message.

The agent only sees offers the rules already allow, and its answer still goes
through validator.validate() before anything is sent. Any failure here
(no key, network, refusal, bad output) returns None and the rules-only pick is used.
"""

import json
import logging
import os

import anthropic

from engine.eligibility import Allowed
from engine.features import Profile
from engine.rules import Rules
from engine.scorer import Pick
from engine.validator import OfferChoice

log = logging.getLogger(__name__)

LANGUAGES = {"en": "English", "hi": "Hindi", "pa": "Punjabi"}
VALUE_UNITS = {"percent_off": "percent", "flat_off": "rupees", "bonus_points": "points multiplier"}

SYSTEM_PROMPT = """You are the offer agent for a demo online store in Punjab, India. For one customer you \
choose at most one offer from a list the business rules already allow, and write a short Telegram message for it.

Choosing:
- Choose an offer_id from allowed_offers, or "none" if no offer is worth sending.
- For an offer with a value_range, choose a whole number inside the range. Prefer the smallest value that is \
likely to change what the customer does: a discount given to someone who would buy anyway is lost margin.
- Each allowed offer shows the rules engine's best value and expected extra profit. Follow it unless the \
profile gives you a good reason to choose differently.

Writing the message:
- Warm, simple English, one to three short sentences, at most one or two emojis. Speak to the customer directly.
- If the customer's preferred language is Punjabi or Hindi, you may open with a short greeting in Latin \
script, such as "Sat Sri Akal" or "Namaste".
- Never write numbers, prices, percentages, dates or coupon codes, not even as words ("fifty", "half", \
"double"). Use placeholders instead; the system fills in real values and adds the code and expiry below \
your message.
- Use only the placeholders listed in available_placeholders. {offer} (for example "15% off") must appear \
unless the offer kind is "reminder".
- No pressure or fake urgency, no promises the offer does not make, no links, none of the banned phrases.

reason: one plain sentence for the marketing team on why this offer suits this customer."""

_client: anthropic.AsyncAnthropic | None = None


def enabled() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY"))


def _get_client() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic(timeout=60.0, max_retries=1)
    return _client


def build_request(p: Profile, allowed: dict[str, Allowed], picks: list[Pick], rules: Rules,
                  placeholders: set[str], previous_errors: list[str] | None) -> str:
    best = {pick.offer_id: pick for pick in picks}
    payload = {
        "customer": {
            "first_name": p.first_name,
            "city": p.city,
            "preferred_language": LANGUAGES.get(p.language, "English"),
            "lifecycle_stage": p.lifecycle,
            "orders_in_last_year": p.orders,
            "average_order_inr": round(p.avg_order_inr),
            "days_since_last_order": p.days_since_last_order,
            "share_of_orders_with_coupon": round(p.discount_share, 2),
            "favourite_category": p.fav_category,
            "reward_points": p.points,
            "points_expire_in_days": p.points_expire_in_days,
            "cart": {"product": p.cart_product, "value_inr": round(p.cart_value),
                     "abandoned_hours_ago": round(p.cart_hours_ago or 0)} if p.cart_product else None,
        },
        "allowed_offers": [
            {
                "offer_id": a.offer_id,
                "name": a.offer.name,
                "kind": a.offer.kind,
                "goal": a.offer.goal,
                "value_range": [a.min_value, a.max_value] if a.offer.has_value else None,
                "value_unit": VALUE_UNITS.get(a.offer.kind),
                "applies_to_category": a.category,
                "min_order_inr": a.offer.min_order_inr,
                "rules_engine_best_value": best[a.offer_id].value,
                "expected_extra_profit_inr": round(best[a.offer_id].extra_profit),
            }
            for a in allowed.values()
        ],
        "rules_engine_suggestion": picks[0].offer_id if picks and picks[0].extra_profit > 0 else "none",
        "available_placeholders": sorted("{" + name + "}" for name in placeholders),
        "message_limits": {"max_characters": rules.messages.max_length,
                           "banned_phrases": rules.messages.banned_phrases},
    }
    if previous_errors:
        payload["your_previous_answer_was_rejected_because"] = previous_errors
    return json.dumps(payload, ensure_ascii=False, indent=1)


async def choose(p: Profile, allowed: dict[str, Allowed], picks: list[Pick], rules: Rules,
                 placeholders: set[str], previous_errors: list[str] | None = None) -> OfferChoice | None:
    model = os.getenv("LLM_MODEL", "claude-opus-5-5")
    extra = {}
    if not model.startswith("claude-haiku"):  # Haiku has no server-side fallback
        extra = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
    try:
        response = await _get_client().beta.messages.parse(
            model=model,
            max_tokens=16000,
            output_config={"effort": os.getenv("LLM_EFFORT", "low")},
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": build_request(p, allowed, picks, rules, placeholders,
                                                                previous_errors)}],
            output_format=OfferChoice,
            **extra,
        )
    except anthropic.APIConnectionError as e:
        log.warning("AI unreachable, using rules: %s", e)
        return None
    except anthropic.RateLimitError:
        log.warning("AI rate-limited, using rules")
        return None
    except anthropic.APIStatusError as e:
        log.warning("AI error %s, using rules: %s", e.status_code, e.message)
        return None
    except ValueError as e:  # output did not match the OfferChoice schema
        log.warning("AI output invalid, using rules: %s", e)
        return None

    if response.stop_reason == "refusal":
        log.warning("AI declined the request, using rules")
        return None
    return response.parsed_output
