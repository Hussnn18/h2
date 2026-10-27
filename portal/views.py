"""The portal's pages (Overview, Offers, Rules, Customer preview) and their helpers.

portal/app.py builds the menu from these. Each page is a plain function, so tests can run one alone.
"""

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import altair as alt  # noqa: E402
import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402
from dotenv import load_dotenv  # noqa: E402
from pydantic import ValidationError  # noqa: E402
from ruamel.yaml.comments import CommentedMap  # noqa: E402

from data.generate import generate  # noqa: E402
from engine import agent, db  # noqa: E402
from engine.config_store import (  # noqa: E402
    flow_list, flow_map, load_raw, quoted, save_offers, save_rules, set_in_order)
from engine.decide import preview  # noqa: E402
from engine.rules import CONDITIONS, VALUE_KINDS, describe_condition, load_offers, load_rules  # noqa: E402
from engine.validator import PLACEHOLDER, check_message  # noqa: E402

load_dotenv(ROOT / ".env")

KIND_LABELS = {
    "percent_off": "% off",
    "flat_off": "₹ off",
    "free_delivery": "Free delivery",
    "bonus_points": "Bonus points",
    "reminder": "Reminder (no discount)",
    "early_access": "Early sale access",
}
RANGE_UNITS = {"percent_off": "%", "flat_off": "₹", "bonus_points": "x points"}
CONDITION_LABELS = {
    "has_cart": "Has items in cart",
    "min_orders": "Ordered at least … times",
    "max_orders": "Ordered at most … times",
    "min_days_since_last_order": "Last order at least … days ago",
    "max_days_since_signup": "Signed up within the last … days",
    "min_points": "Has at least … reward points",
    "points_expire_within_days": "Points expire within … days",
    "min_avg_order_inr": "Average order at least ₹…",
    "lifecycle_in": "Customer stage is one of…",
}
LIFECYCLES = ["new", "active", "at_risk", "lapsed", "dormant"]
ALL_PLACEHOLDERS = {"first_name", "category", "offer", "product", "points"}
SAMPLE = {"first_name": "Simran", "category": "fashion", "product": "premium running shoes", "points": "1,240"}
SERIES_COLOR = {"light": "#2a78d6", "dark": "#3987e5"}  # single-series bar colour per theme


# ── Shared helpers ─────────────────────────────────────────────────────────

@st.cache_resource
def get_conn():
    path = db.db_path()
    path = path if path.is_absolute() else ROOT / path
    if not path.exists():
        generate(path=path)
    return db.connect(path)


def flash(message: str) -> None:
    st.session_state["flash"] = message


def show_flash() -> None:
    if msg := st.session_state.pop("flash", None):
        st.success(msg, icon="✅")


def show_validation_error(e: ValidationError) -> None:
    lines = []
    for err in e.errors():
        where = " → ".join(str(x) for x in err["loc"]) or "file"
        lines.append(f"- **{where}**: {err['msg']}")
    st.error("Not saved. Please fix:\n" + "\n".join(lines))


def fmt_range(o: dict) -> str:
    r = o.get("range")
    if not r:
        return "—"
    unit = RANGE_UNITS.get(o["kind"], "")
    return f"₹{r[0]}–₹{r[1]}" if unit == "₹" else f"{r[0]}–{r[1]}{unit if unit == '%' else ' ' + unit}"


def offer_label(offer: dict, value=None) -> str:
    """Sample wording for {offer} in previews."""
    kind = offer.get("kind")
    if kind == "percent_off":
        return f"{value}% off"
    if kind == "flat_off":
        return f"₹{value} off"
    if kind == "bonus_points":
        return f"{value}x reward points"
    if kind == "free_delivery":
        return "FREE delivery"
    if kind == "early_access":
        return "early access to our next big sale"
    return offer.get("name", "")


def telegram_bubble(html_text: str) -> None:
    body = html_text.replace("\n", "<br>")
    st.html(
        "<div style='border:1px solid rgba(128,128,128,.35);border-radius:14px;padding:12px 14px;"
        f"max-width:460px;line-height:1.45;font-size:0.95rem'>{body}</div>"
    )


def require_password() -> None:
    password = os.getenv("PORTAL_PASSWORD")
    if not password or st.session_state.get("authed"):
        return
    st.title("🎁 Offer Engine Portal")
    entered = st.text_input("Password", type="password")
    if entered == password:
        st.session_state["authed"] = True
        st.rerun()
    elif entered:
        st.error("Wrong password.")
    st.stop()


# ── Overview ───────────────────────────────────────────────────────────────

def overview() -> None:
    st.title("📊 Overview")
    st.caption("How the bot's offers are doing. Updates when you reload the page.")
    conn = get_conn()
    rules, offers = load_rules(), load_offers()
    now = db.now_ist()

    d = pd.read_sql_query(
        "SELECT d.*, c.first_name || ' ' || c.last_name AS customer FROM decisions d "
        "LEFT JOIN customers c ON c.id = d.customer_id WHERE d.chat_id IS NOT NULL ORDER BY d.id DESC", conn)
    players, opted_out = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(opted_out), 0) FROM telegram_users").fetchone()
    sent = d[d.status.isin(db.SENT_STATUSES)]
    claimed = int((sent.status == "claimed").sum())
    declined = int((sent.status == "declined").sum())
    used = db.budget_used_today(conn, now)
    budget = rules.budget.daily_discount_budget_inr

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Players", players, help=f"Classmates who started the bot. {opted_out} opted out.", border=True)
    c2.metric("Offers sent", len(sent), border=True)
    c3.metric("Claimed", claimed, f"{claimed / len(sent):.0%} claim rate" if len(sent) else None,
              delta_color="off", delta_arrow="off", border=True, help=f"{declined} said 'Not for me'")
    c4.metric("Budget used today", f"₹{used:,.0f}", f"of ₹{budget:,}", delta_color="off", delta_arrow="off",
              border=True)

    ai_share = (sent.source == "ai").mean() if len(sent) else 0
    no_offer = int((d.status == "not_sent").sum())
    rejected = sum(len(json.loads(r)) for r in d.rejected.dropna() if r.startswith("["))
    c5, c6, c7, c8 = st.columns(4)
    c5.metric("Picked by AI", f"{ai_share:.0%}", help="The rest were picked by the rules engine", border=True)
    c6.metric("No offer sent", no_offer, help="Blocked by rules, or nothing worth sending", border=True)
    c7.metric("AI answers blocked", rejected, help="AI suggestions the rule checker refused to send", border=True)
    c8.metric("AI", "On" if agent.enabled() else "Off", border=True,
              help=f"Model: {os.getenv('LLM_MODEL', 'claude-opus-5-5')}" if agent.enabled()
              else "No API key, so the rules engine picks every offer")

    if sent.empty:
        st.info("No offers sent yet. Ask classmates to open the bot and press Start.", icon="📭")
        return

    names = {oid: o.name for oid, o in offers.items()}
    by_offer = (sent.assign(offer=sent.offer_id.map(lambda o: names.get(o, o)))
                .groupby("offer")
                .agg(sent=("id", "size"), claimed=("status", lambda s: (s == "claimed").sum()),
                     declined=("status", lambda s: (s == "declined").sum()), cost=("cost_inr", "sum"))
                .reset_index()
                .sort_values("sent", ascending=False))
    by_offer["claim_rate"] = by_offer.claimed / by_offer.sent

    left, right = st.columns([3, 4])
    with left:
        st.subheader("Offers sent, by offer")
        theme = getattr(st.context.theme, "type", None) or "light"
        chart = (alt.Chart(by_offer)
                 .mark_bar(cornerRadiusEnd=4, size=16, color=SERIES_COLOR.get(theme, SERIES_COLOR["light"]))
                 .encode(x=alt.X("sent:Q", title="Offers sent", axis=alt.Axis(tickMinStep=1)),
                         y=alt.Y("offer:N", sort="-x", title=None),
                         tooltip=[alt.Tooltip("offer:N", title="Offer"), alt.Tooltip("sent:Q", title="Sent"),
                                  alt.Tooltip("claimed:Q", title="Claimed")])
                 .properties(height=max(120, 34 * len(by_offer))))
        st.altair_chart(chart, width="stretch")
    with right:
        st.subheader("Results by offer")
        st.dataframe(
            by_offer, hide_index=True, width="stretch",
            column_config={
                "offer": "Offer", "sent": "Sent", "claimed": "Claimed", "declined": "Not for me",
                "cost": st.column_config.NumberColumn("Expected cost", format="₹%.0f"),
                "claim_rate": st.column_config.ProgressColumn("Claim rate", format="percent", min_value=0,
                                                              max_value=1),
            })

    st.subheader("Latest decisions")
    recent = d.head(25).assign(
        time=lambda x: x.created_at.str[11:16],
        offer=lambda x: x.offer_id.map(lambda o: names.get(o, o) if isinstance(o, str) else "—"),
        reason=lambda x: x.reason.fillna(x.why).fillna("").str.replace(r"<[^>]+>", "", regex=True),
    )
    st.dataframe(recent[["time", "customer", "offer", "value", "source", "status", "reason"]],
                 hide_index=True, width="stretch",
                 column_config={"time": "Time", "customer": "Customer", "offer": "Offer", "value": "Value",
                                "source": "Picked by", "status": "Status", "reason": "Reason"})


# ── Offers ─────────────────────────────────────────────────────────────────

def offers_page() -> None:
    st.title("🎁 Offers")
    st.caption("Every offer the bot can send. The AI only chooses among offers a customer qualifies for, "
               "and never outside the ranges set here. Business rules can narrow ranges further.")
    show_flash()
    raw = load_raw("offers.yaml")
    items: CommentedMap = raw["offers"]

    rows = [{
        "On": bool(o.get("enabled", True)),
        "Offer": o["name"],
        "Type": KIND_LABELS.get(o["kind"], o["kind"]),
        "Range": fmt_range(o),
        "Min order": f"₹{o['min_order_inr']:,}" if o.get("min_order_inr") else "—",
        "Who qualifies": "; ".join(describe_condition(k, v) for k, v in (o.get("eligible_when") or {}).items())
                         or "everyone",
        "Goal": o["goal"],
    } for o in items.values()]
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch",
                 column_config={"On": st.column_config.CheckboxColumn("On", width="small")})

    ids = list(items)
    choice = st.selectbox("Choose an offer to edit", ["__new__"] + ids,
                          format_func=lambda i: "➕ Create a new offer" if i == "__new__" else
                          f"{items[i]['name']}  ({i})", index=1 if ids else 0)
    st.divider()
    offer_editor(raw, None if choice == "__new__" else choice)


def offer_editor(raw: CommentedMap, offer_id: str | None) -> None:
    items: CommentedMap = raw["offers"]
    is_new = offer_id is None
    o = items[offer_id] if not is_new else {}
    k = f"offer.{offer_id or '__new__'}"  # widget keys, one set per offer
    rules = load_rules()

    st.subheader("New offer" if is_new else f"Edit: {o['name']}")
    c1, c2 = st.columns([3, 1])
    name = c1.text_input("Name", o.get("name", ""), key=f"{k}.name")
    enabled = c2.toggle("Switched on", bool(o.get("enabled", True)), key=f"{k}.enabled",
                        help="Switched-off offers are never sent, but stay here to switch on later.")
    new_id = offer_id
    if is_new:
        new_id = st.text_input("Short ID (lowercase letters, numbers and _ only)", key=f"{k}.id",
                               placeholder="e.g. diwali_discount")
    goal = st.text_input("Business goal (shown to the AI and in 'Why this offer?')", o.get("goal", ""),
                         key=f"{k}.goal")

    kinds = list(KIND_LABELS)
    c1, c2, c3 = st.columns(3)
    kind = c1.selectbox("Type", kinds, index=kinds.index(o.get("kind", "percent_off")), key=f"{k}.kind",
                        format_func=KIND_LABELS.get)
    valid_hours = c2.number_input("Valid for (hours)", 1, 24 * 30, int(o.get("valid_hours", 48)),
                                  key=f"{k}.valid")
    min_order = c3.number_input("Minimum order (₹, 0 = none)", 0, 1_000_000, int(o.get("min_order_inr", 0)),
                                step=50, key=f"{k}.min_order")

    lo = hi = cost = None
    if kind in VALUE_KINDS:
        unit = RANGE_UNITS[kind]
        default = {"percent_off": [5, 15], "flat_off": [50, 200], "bonus_points": [2, 3]}[kind]
        r = list(o.get("range") or default) if o.get("kind") == kind else default
        c1, c2, c3 = st.columns([1, 1, 2])
        lo = c1.number_input(f"Lowest value ({unit})", 0, 100_000, int(r[0]), key=f"{k}.{kind}.lo")
        hi = c2.number_input(f"Highest value ({unit})", 0, 100_000, int(r[1]), key=f"{k}.{kind}.hi")
        c3.caption(f"The AI picks a value in this range. The rules also cap every discount at "
                   f"{rules.limits.max_discount_percent:g}% / ₹{rules.limits.max_discount_inr}, and lower "
                   f"for thin-margin categories.")
    if kind == "free_delivery":
        cost = st.number_input("Cost to us per use (₹)", 0, 10_000, int(o.get("cost_inr", 49)), key=f"{k}.cost")

    st.markdown("**Who qualifies?** All ticked conditions must be true.")
    current = o.get("eligible_when") or {}
    eligible_when = {}
    cols = st.columns(3)
    for i, cond in enumerate(CONDITIONS):
        col = cols[i % 3]
        on = col.checkbox(CONDITION_LABELS[cond], cond in current, key=f"{k}.c.{cond}")
        if not on:
            continue
        if cond == "has_cart":
            eligible_when[cond] = True
        elif cond == "lifecycle_in":
            eligible_when[cond] = col.multiselect(
                "Stages", LIFECYCLES, list(current.get(cond, ["active"])), key=f"{k}.c.{cond}.v",
                format_func=lambda s: s.replace("_", " "), label_visibility="collapsed")
        else:
            eligible_when[cond] = col.number_input(
                CONDITION_LABELS[cond], 0, 1_000_000, int(current.get(cond, 1)), key=f"{k}.c.{cond}.v",
                label_visibility="collapsed")

    message = st.text_area(
        "Message when the AI is off (fallback)", o.get("fallback_message", ""), key=f"{k}.msg",
        help="Placeholders: {first_name} {offer} {category} {product} {points}. Don't type numbers: "
             "the system adds the amount, code and expiry under the message.")
    problems = check_message(message, kind, rules, ALL_PLACEHOLDERS)
    if problems:
        st.warning("The rule checker would block this message:\n" + "\n".join(f"- {p}" for p in problems))
    elif message:
        sample = {**SAMPLE, "offer": f"<b>{offer_label({'kind': kind, 'name': name}, lo)}</b>"}
        st.caption("✓ Passes the message checks. Preview with sample values:")
        telegram_bubble(PLACEHOLDER.sub(lambda m: sample.get(m.group(1), m.group(0)), message))

    c1, c2, _ = st.columns([1, 1, 4])
    if c1.button("💾 Save offer", type="primary", key=f"{k}.save", width="stretch"):
        if is_new and not re.fullmatch(r"[a-z][a-z0-9_]{2,40}", new_id or ""):
            st.error("Give the offer a short ID: lowercase letters, numbers and _ only, e.g. diwali_discount.")
            return
        if is_new and new_id in items:
            st.error(f"An offer with the ID '{new_id}' already exists.")
            return
        entry = items[offer_id] if not is_new else CommentedMap()
        set_in_order(entry, "name", name)
        if enabled:
            entry.pop("enabled", None)  # on is the default, keep the file clean
        else:
            set_in_order(entry, "enabled", False)
        set_in_order(entry, "kind", kind)
        set_in_order(entry, "goal", goal)
        if kind in VALUE_KINDS:
            set_in_order(entry, "range", flow_list([int(lo), int(hi)]))
        else:
            entry.pop("range", None)
        for key, value in (("min_order_inr", min_order), ("cost_inr", cost)):
            if value:
                set_in_order(entry, key, int(value))
            else:
                entry.pop(key, None)
        set_in_order(entry, "eligible_when", flow_map({c: (list(v) if isinstance(v, list) else v)
                                                       for c, v in eligible_when.items()}))
        set_in_order(entry, "valid_hours", int(valid_hours))
        set_in_order(entry, "fallback_message", quoted(message.strip()))
        if is_new:
            items[new_id] = entry
        try:
            save_offers(raw)
        except ValidationError as e:
            show_validation_error(e)
            return
        flash(f"Saved '{name}'. The bot uses it from the next offer.")
        st.rerun()

    if not is_new:
        with c2.popover("🗑️ Delete", width="stretch"):
            st.write(f"Delete **{o['name']}** for good? Switching it off keeps it for later.")
            if st.button("Yes, delete it", type="primary", key=f"{k}.delete"):
                del items[offer_id]
                save_offers(raw)
                flash(f"Deleted '{o['name']}'.")
                st.rerun()


# ── Rules ──────────────────────────────────────────────────────────────────

def rules_page() -> None:
    st.title("⚙️ Business rules")
    st.caption("Limits every offer must respect, whether the AI or the rules engine picked it. "
               "Saved changes apply to the next offer.")
    show_flash()
    raw = load_raw("rules.yaml")
    lim, fq, tg, msg = raw["limits"], raw["frequency"], raw["targeting"], raw["messages"]
    cats = raw["category_max_discount_percent"]

    with st.form("rules"):
        st.subheader("💰 Money limits")
        c1, c2, c3, c4 = st.columns(4)
        max_pct = c1.number_input("Max discount (%)", 0, 90, int(lim["max_discount_percent"]),
                                  help="No offer above this, ever")
        max_inr = c2.number_input("Max discount per offer (₹)", 0, 100_000, int(lim["max_discount_inr"]), step=50)
        min_margin = c3.number_input("Min margin left after discount (%)", 0, 100,
                                     int(lim["min_margin_left_percent"]))
        budget = c4.number_input("Daily discount budget (₹)", 0, 10_000_000,
                                 int(raw["budget"]["daily_discount_budget_inr"]), step=500,
                                 help="Expected discount value sent per day, all customers together")
        st.markdown("**Max discount by category (%)**: thin-margin categories get less")
        cat_cols = st.columns(len(cats))
        new_cats = {c: col.number_input(c.title(), 0, 90, int(v)) for col, (c, v) in zip(cat_cols, cats.items())}

        st.subheader("🔕 Don't annoy people")
        c1, c2, c3, c4 = st.columns(4)
        per_day = c1.number_input("Max offers per customer per day", 0, 50,
                                  int(fq["max_offers_per_customer_per_day"]))
        gap = c2.number_input("Min minutes between offers", 0, 60 * 24 * 14, int(fq["min_minutes_between_offers"]),
                              help="A real store would use about 2 days (2880 minutes)")
        quiet_start = c3.time_input("Quiet hours start", datetime.strptime(fq["quiet_hours"]["start"], "%H:%M").time(),
                                    help="No offers are pushed at night (India time)")
        quiet_end = c4.time_input("Quiet hours end", datetime.strptime(fq["quiet_hours"]["end"], "%H:%M").time())

        st.subheader("🎯 Who should not get a discount")
        c1, c2 = st.columns(2)
        recent = c1.number_input("No discount if they ordered in the last … days", 0, 365,
                                 int(tg["no_discount_if_ordered_within_days"]),
                                 help="They would likely buy anyway, so a discount is lost margin")
        returns = c2.slider("No discount if they return more than … of orders", 0, 100,
                            int(round(float(tg["block_discounts_if_return_rate_above"]) * 100)), format="%d%%")

        st.subheader("✉️ Message checks")
        c1, c2 = st.columns([1, 3])
        max_len = c1.number_input("Max message length (characters)", 50, 1000, int(msg["max_length"]))
        banned = c2.text_area("Banned phrases (one per line)", "\n".join(msg["banned_phrases"]), height=150)

        submitted = st.form_submit_button("💾 Save rules", type="primary")

    if submitted:
        lim["max_discount_percent"] = int(max_pct)
        lim["max_discount_inr"] = int(max_inr)
        lim["min_margin_left_percent"] = int(min_margin)
        raw["budget"]["daily_discount_budget_inr"] = int(budget)
        for c, v in new_cats.items():
            cats[c] = int(v)
        fq["max_offers_per_customer_per_day"] = int(per_day)
        fq["min_minutes_between_offers"] = int(gap)
        fq["quiet_hours"]["start"] = quoted(quiet_start.strftime("%H:%M"))
        fq["quiet_hours"]["end"] = quoted(quiet_end.strftime("%H:%M"))
        tg["no_discount_if_ordered_within_days"] = int(recent)
        tg["block_discounts_if_return_rate_above"] = round(returns / 100, 2)
        msg["max_length"] = int(max_len)
        phrases = msg["banned_phrases"]
        phrases.clear()
        phrases.extend(p.strip() for p in banned.splitlines() if p.strip())
        try:
            save_rules(raw)
        except ValidationError as e:
            show_validation_error(e)
            return
        flash("Rules saved. The bot uses them from the next offer.")
        st.rerun()


# ── Customer preview ───────────────────────────────────────────────────────

def customer_page() -> None:
    st.title("🔍 Customer preview")
    st.caption("See any customer the way the engine sees them, and what the rules engine would send right now. "
               "Nothing is sent or logged.")
    conn = get_conn()
    offers = load_offers()
    players = conn.execute(
        "SELECT t.customer_id, c.first_name || ' ' || c.last_name AS name FROM telegram_users t "
        "JOIN customers c ON c.id = t.customer_id ORDER BY t.joined_at DESC").fetchall()
    max_id = conn.execute("SELECT MAX(id) FROM customers").fetchone()[0]

    source = st.radio("Customer", ["Telegram players", "Any customer"], horizontal=True,
                      index=0 if players else 1, label_visibility="collapsed")
    if source == "Telegram players" and players:
        names = {r["customer_id"]: r["name"] for r in players}
        customer_id = st.selectbox("Player", list(names), format_func=lambda c: f"{names[c]}  (customer {c})")
    else:
        customer_id = st.number_input("Customer number", 1, max_id, 1)

    pv = preview(conn, int(customer_id))
    p = pv.profile
    stage = p.lifecycle.replace("_", " ")
    cols = st.columns(6)
    cols[0].metric("Stage", stage, border=True)
    cols[1].metric("Orders (last year)", p.orders, border=True)
    cols[2].metric("Avg order", f"₹{p.avg_order_inr:,.0f}" if p.orders else "—", border=True)
    cols[3].metric("Days since order", p.days_since_last_order if p.days_since_last_order is not None
                   else "never", border=True)
    cols[4].metric("Orders w/ coupon", f"{p.discount_share:.0%}" if p.orders else "—", border=True)
    cols[5].metric("Reward points", f"{p.points:,}", border=True)
    facts = [f"📍 {p.city}", f"❤️ likes {p.fav_category}", f"🗣️ {agent.LANGUAGES.get(p.language, p.language)}"]
    if p.cart_product:
        facts.append(f"🛒 cart: {p.cart_product.lower()} (₹{p.cart_value:,.0f}, {p.cart_hours_ago:.0f}h ago)")
    if p.offers_today:
        facts.append(f"📨 {p.offers_today} offer(s) today")
    st.caption("  ·  ".join(facts))

    left, right = st.columns([2, 3])
    with left:
        st.subheader("Would send now")
        if pv.message:
            telegram_bubble(pv.message)
            st.caption(f"**Why:** {pv.choice.reason}")
        elif pv.eligibility.blocked:
            st.info("Nothing: " + "; ".join(pv.eligibility.blocked), icon="🚫")
        elif pv.errors:
            st.warning("Nothing: the chosen offer failed the rule check: " + "; ".join(pv.errors))
        else:
            st.info("Nothing. " + pv.choice.reason, icon="🤷")
        st.caption("This is the rules engine's pick. With the AI on, Claude may choose differently and "
                   "rewrite the message, within the same limits.")
    with right:
        st.subheader("Offers this customer qualifies for")
        allowed = pv.eligibility.allowed
        if pv.picks:
            st.dataframe(pd.DataFrame([{
                "Offer": allowed[pk.offer_id].offer.name,
                "Allowed range": (f"{allowed[pk.offer_id].min_value}–{allowed[pk.offer_id].max_value}"
                                  f"{RANGE_UNITS.get(allowed[pk.offer_id].offer.kind, '')}"
                                  if allowed[pk.offer_id].offer.has_value else "—"),
                "Best value": pk.value if pk.value is not None else "—",
                "Expected extra profit": pk.extra_profit,
            } for pk in pv.picks]), hide_index=True, width="stretch",
                column_config={"Expected extra profit": st.column_config.NumberColumn(format="₹%.0f")})
        else:
            st.caption("None right now.")
        if pv.eligibility.skipped:
            with st.expander(f"Not eligible ({len(pv.eligibility.skipped)})"):
                st.dataframe(pd.DataFrame([{"Offer": offers[o].name if o in offers else o, "Why not": why}
                                           for o, why in pv.eligibility.skipped.items()]),
                             hide_index=True, width="stretch")

    hidden = conn.execute("SELECT hidden_type, hidden_price_sensitivity FROM customers WHERE id = ?",
                          (int(customer_id),)).fetchone()
    with st.expander("Hidden truth (for evaluation only: the engine never sees this)"):
        st.write(f"Type: **{hidden['hidden_type'].replace('_', ' ')}** · price sensitivity "
                 f"**{hidden['hidden_price_sensitivity']:.2f}** (0 = ignores coupons, 1 = only buys with one)")
