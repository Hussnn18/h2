"""Telegram bot: each classmate plays a synthetic customer and receives offers.

Run:  python -m bot.main      (needs TELEGRAM_BOT_TOKEN in .env)
"""

import asyncio
import html
import logging
import os
import sqlite3
from datetime import datetime

from dotenv import load_dotenv
from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction, ParseMode
from telegram.error import BadRequest, Forbidden, NetworkError, TelegramError
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from data.generate import generate
from engine import agent, db
from engine.decide import decide, has_offer
from engine.features import build_profile

load_dotenv()
logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("bot")

CONN: sqlite3.Connection  # set in main()
ADMINS = {int(x) for x in os.getenv("ADMIN_CHAT_IDS", "").replace(" ", "").split(",") if x}

COMMANDS = [
    ("offer", "Get a personalised offer now"),
    ("profile", "See the customer you are playing"),
    ("newpersona", "Switch to a different customer"),
    ("stop", "Stop receiving offers"),
    ("help", "How this works"),
]
WELCOME = (
    "👋 <b>Welcome to the Personalised Offer Engine demo!</b>\n\n"
    "You'll play a <b>synthetic customer</b> of an online store in Punjab. All customer data here is made up.\n"
    "Offers are chosen for you by an AI, inside limits set by business rules, and every offer is checked "
    "against those rules before it reaches you.\n\n"
    + "\n".join(f"/{name} {text.lower()}" for name, text in COMMANDS)
)


def offer_buttons(decision_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Claim offer", callback_data=f"claim:{decision_id}"),
         InlineKeyboardButton("🙅 Not for me", callback_data=f"decline:{decision_id}")],
        [InlineKeyboardButton("🤔 Why this offer?", callback_data=f"why:{decision_id}")],
    ])


def why_button(decision_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🤔 Why this offer?", callback_data=f"why:{decision_id}")]])


def profile_card(customer_id: int) -> str:
    c = CONN.execute("SELECT first_name, last_name, age, city FROM customers WHERE id = ?", (customer_id,)).fetchone()
    p = build_profile(CONN, customer_id, db.now_ist())
    e = html.escape
    lines = [f"👤 You are <b>{e(c['first_name'])} {e(c['last_name'])}</b>, {c['age']}, from {e(c['city'])}"]
    if p.orders:
        lines.append(f"🛍 {p.orders} orders in the last year · avg ₹{p.avg_order_inr:,.0f} · mostly {e(p.fav_category)}")
        lines.append(f"⏱ Last order {p.days_since_last_order} days ago · status: {p.lifecycle.replace('_', ' ')}")
    else:
        lines.append(f"🛍 No orders yet · interested in {e(p.fav_category)}")
    if p.points:
        lines.append(f"⭐ {p.points:,} reward points (expire in {p.points_expire_in_days} days)")
    if p.cart_product:
        lines.append(f"🛒 In your cart: {e(p.cart_product.lower())} (₹{p.cart_value:,.0f})")
    lines.append("<i>Synthetic data, made up for this project.</i>")
    return "\n".join(lines)


async def send_offer(bot, chat_id: int, customer_id: int, trigger: str) -> bool:
    """Decide and send. Returns True if an offer went out."""
    decision = await decide(CONN, customer_id, chat_id, trigger)
    if not decision.sent:
        if trigger == "pull":  # the user asked, so tell them why there's nothing
            await bot.send_message(chat_id, decision.text, parse_mode=ParseMode.HTML)
        return False
    try:
        await bot.send_message(chat_id, decision.text, parse_mode=ParseMode.HTML,
                               reply_markup=offer_buttons(decision.decision_id))
    except TelegramError:
        db.set_decision_status(CONN, decision.decision_id, "not_sent")
        raise
    return True


def assign_customer(chat_id: int) -> int:
    """Link the chat to a free synthetic customer who qualifies for an offer right now."""
    now = db.now_ist()
    return db.link_new_customer(CONN, chat_id, now, accept=lambda customer_id: has_offer(CONN, customer_id, now))


async def show_typing(bot, chat_id: int) -> None:
    """Cosmetic only: never let a failed typing indicator stop an offer."""
    try:
        await bot.send_chat_action(chat_id, ChatAction.TYPING)
    except TelegramError as e:
        log.warning("Typing indicator failed: %s", e)


async def current_user(update: Update) -> sqlite3.Row | None:
    user = db.get_user(CONN, update.effective_chat.id)
    if user is None:
        await update.message.reply_text("Send /start first to get your customer profile.")
    return user


# ── Commands ───────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    user = db.get_user(CONN, chat_id)
    if user is None:
        customer_id = assign_customer(chat_id)
    else:
        db.set_opted_out(CONN, chat_id, False)
        customer_id = user["customer_id"]
    await update.message.reply_html(WELCOME)
    await update.message.reply_html(profile_card(customer_id))
    await show_typing(context.bot, chat_id)
    await send_offer(context.bot, chat_id, customer_id, "pull")


async def offer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = await current_user(update)
    if user is None:
        return
    if user["opted_out"]:
        await update.message.reply_text("You've opted out of offers. Send /start to opt back in.")
        return
    await show_typing(context.bot, user["chat_id"])
    await send_offer(context.bot, user["chat_id"], user["customer_id"], "pull")


async def profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = await current_user(update)
    if user:
        await update.message.reply_html(profile_card(user["customer_id"]))


async def new_persona(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await current_user(update) is None:
        return
    customer_id = assign_customer(update.effective_chat.id)
    await update.message.reply_html("🔄 New customer assigned.\n\n" + profile_card(customer_id))


async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await current_user(update):
        db.set_opted_out(CONN, update.effective_chat.id, True)
        await update.message.reply_text("🔕 You won't receive any more offers. Send /start to come back.")


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_html(WELCOME)


async def my_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(f"Your chat id: {update.effective_chat.id}")


# ── Buttons ────────────────────────────────────────────────────────────────

async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    action, _, raw_id = query.data.partition(":")
    d = db.get_decision(CONN, int(raw_id))
    if d is None or d["chat_id"] != query.message.chat.id:
        await query.answer("Offer not found.")
        return

    if action == "why":
        await query.answer()
        await query.message.reply_html(d["why"] or "No details recorded.")
        return

    if action == "claim":
        if d["expires_at"] and datetime.fromisoformat(d["expires_at"]) < db.now_ist():
            await query.answer("Sorry, this offer has expired.", show_alert=True)
            return
        if not db.set_decision_status(CONN, d["id"], "claimed"):
            await query.answer(f"Already {d['status']}.")
            return
        await query.answer("Claimed ✅")
        await query.message.reply_html("✅ <b>Offer claimed!</b> In a real store it would be applied at checkout. "
                                       "(Demo: no order is placed.)")
    elif action == "decline":
        if not db.set_decision_status(CONN, d["id"], "declined"):
            await query.answer(f"Already {d['status']}.")
            return
        await query.answer("Thanks for the feedback 👍")

    try:
        await query.edit_message_reply_markup(why_button(d["id"]))
    except BadRequest:
        pass  # message unchanged or too old to edit


# ── Admin ──────────────────────────────────────────────────────────────────

def is_admin(update: Update) -> bool:
    return update.effective_chat.id in ADMINS


async def campaign(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Push an offer to every user who hasn't opted out (rules may skip some)."""
    if not is_admin(update):
        await update.message.reply_text("Admins only.")
        return
    users = db.active_users(CONN)
    sent = 0
    for u in users:
        try:
            sent += await send_offer(context.bot, u["chat_id"], u["customer_id"], "push")
        except Forbidden:  # the user blocked the bot
            db.set_opted_out(CONN, u["chat_id"], True)
        except TelegramError as e:
            log.warning("Could not send to %s: %s", u["chat_id"], e)
        await asyncio.sleep(0.05)  # stay well under Telegram's rate limits
    await update.message.reply_text(f"📣 Campaign done: {sent} offers sent to {len(users)} users "
                                    f"({len(users) - sent} skipped by the rules).")


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update):
        await update.message.reply_text("Admins only.")
        return
    users = CONN.execute("SELECT COUNT(*), COALESCE(SUM(opted_out), 0) FROM telegram_users").fetchone()
    rows = CONN.execute("SELECT source, status, COUNT(*) FROM decisions WHERE chat_id IS NOT NULL "
                        "GROUP BY source, status ORDER BY 3 DESC").fetchall()
    lines = [f"👥 Users: {users[0]} ({users[1]} opted out)",
             f"💰 Budget used today: ₹{db.budget_used_today(CONN, db.now_ist()):,.0f}",
             f"🤖 AI: {os.getenv('LLM_MODEL', 'claude-opus-5-5') if agent.enabled() else 'off (rules only)'}",
             "", "Decisions (source / status):"]
    lines += [f"  {r[0]} / {r[1]}: {r[2]}" for r in rows]
    await update.message.reply_text("\n".join(lines))


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    if isinstance(context.error, NetworkError) and update is None:
        log.warning("Telegram unreachable, retrying: %s", context.error)  # internet dropped while polling
        return
    log.error("Error while handling an update", exc_info=context.error)
    if isinstance(update, Update) and update.effective_chat:
        try:
            await context.bot.send_message(update.effective_chat.id, "😕 Something went wrong. Please try again.")
        except TelegramError:
            pass  # Telegram unreachable; nothing more we can do for this update


async def set_menu(app: Application) -> None:
    await app.bot.set_my_commands([BotCommand(name, text) for name, text in COMMANDS])


def main() -> None:
    global CONN
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN is not set. Copy .env.example to .env and add your token.")
    if not db.db_path().exists():
        log.info("No database found, generating synthetic data...")
        generate()
    CONN = db.connect()

    app = (Application.builder().token(token)
           .connect_timeout(20).read_timeout(30).write_timeout(30).pool_timeout(10)  # patient on slow networks
           .post_init(set_menu).build())
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("offer", offer))
    app.add_handler(CommandHandler("profile", profile))
    app.add_handler(CommandHandler("newpersona", new_persona))
    app.add_handler(CommandHandler("stop", stop))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("myid", my_id))
    app.add_handler(CommandHandler("campaign", campaign))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_error_handler(on_error)

    ai = f"on ({os.getenv('LLM_MODEL', 'claude-opus-5-5')})" if agent.enabled() else "off (rules only)"
    log.info("Bot running. AI: %s. Admins: %s", ai, sorted(ADMINS) or "none")
    app.run_polling()


if __name__ == "__main__":
    main()
