# Personalised Offer Engine

Hackathon 2 · Punjab Jobs AI Bootcamp · J-PAL South Asia

A system that decides **which offer each customer of an online store should get, or none**, lets an AI
personalise it **inside limits set by business rules**, checks every AI choice against those rules, and
delivers it through a Telegram bot. All customer data is synthetic.

**Telegram bot:** [@oooHusanBot](https://t.me/oooHusanBot)

## How it works

```
synthetic data ─► customer profile (recency, frequency, spend, cart, points, coupon habit…)
                        │
config/rules.yaml ─► 1. Eligibility: which offers are allowed, and the allowed range for each
                        │
                     2. AI agent (Claude) picks one allowed offer + value and writes the message
                        │
config/rules.yaml ─► 3. Validator re-checks everything ── fails ─► AI retries once ─► rules-only pick
                        │ passes
                     4. Our code fills in the amount, coupon code and expiry (the AI never writes numbers)
                        │
                     5. Telegram: [Claim] [Not for me] [Why this offer?]  ─► every decision logged
```

- **Rules decide the limits:** max discount %, max ₹, margin floor per category, daily budget, offers per day,
  minimum gap, quiet hours, no discounts for recent buyers or serial returners.
- **The engine can choose "no offer":** it estimates *extra* profit; a discount for someone who would buy
  anyway is lost margin.
- **The AI is optional:** without an API key the bot runs on the rules engine alone.

## Setup

Python 3.11+.

```bash
python -m venv .venv
```

```bash
.venv\Scripts\activate
```

(on macOS/Linux: `source .venv/bin/activate`)

```bash
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and fill in:

| Variable | What |
|---|---|
| `TELEGRAM_BOT_TOKEN` | From @BotFather → `/newbot` |
| `ANTHROPIC_API_KEY` | Optional. Without it, offers are chosen by rules only |
| `LLM_MODEL` | Default `claude-opus-5-5` |
| `ADMIN_CHAT_IDS` | Your chat id (send `/myid` to the bot) for `/campaign` and `/stats` |

Generate the synthetic data (the bot also does this on first start if the database is missing):

```bash
python -m data.generate --customers 500
```

Run the bot:

```bash
python -m bot.main
```

Run the tests:

```bash
python -m pytest
```

## Using the bot

| Command | What it does |
|---|---|
| `/start` | Links you to a synthetic customer and sends your first offer |
| `/offer` | Get an offer now (the rules may say no) |
| `/profile` | The customer you are playing |
| `/newpersona` | Switch to another customer |
| `/stop` | Stop all offers (`/start` to come back) |
| `/campaign` | Admin: push an offer to everyone, rules permitting |
| `/stats` | Admin: users, budget used, decisions by source |

## Marketing portal

A web portal where a marketing team manages offers and rules without touching code:

```bash
python -m streamlit run portal/app.py
```

| Page | What you can do |
|---|---|
| **Overview** | Players, offers sent, claim rate, budget used today, AI vs rules picks, results per offer, latest decisions |
| **Offers** | See every offer; switch one on/off; edit its range, minimum order, who qualifies and fallback message (checked live by the same rule checker the bot uses); create or delete offers |
| **Rules** | Max discount (% and ₹), margin floor, category caps, daily budget, offers per day, quiet hours, who gets no discount, banned phrases |
| **Customer preview** | Pick any customer or Telegram player: their profile, which offers they qualify for (and why not), and the message they would get right now. Nothing is sent |

Saves are validated before they are written and keep the comments in the YAML files. The bot uses them from the
next offer. Set `PORTAL_PASSWORD` in `.env` to require a password.

## Changing the rules by hand

Edit [config/rules.yaml](config/rules.yaml) and [config/offers.yaml](config/offers.yaml). Every number has a
plain-English comment. Changes apply to the next offer without a restart. A typo produces a clear error
message instead of a silently ignored rule.

## Synthetic data

`data/generate.py` creates customers in Punjab cities with a hidden type (deal hunter, loyal, premium,
festival shopper, new, drifting, dormant) that drives their behaviour. The engine never sees the type. It
has to infer it from orders, like a real platform. Patterns checked by `tests/test_data_realism.py`:

- the top 20% of customers bring in most of the revenue
- deal hunters use coupons far more than loyal customers
- fashion is returned more often than electronics
- weekend and payday spikes, festival-season peaks
- more cash-on-delivery in smaller cities
- about a third of customers have an abandoned cart

## Project layout

```
config/    rules.yaml, offers.yaml          ← marketing edits these
data/      generate.py                      ← synthetic data + realism report
engine/    features → eligibility → scorer → agent → validator → render, tied together in decide.py
bot/       main.py                          ← Telegram bot
portal/    app.py (menu), views.py (pages)  ← marketing portal (Streamlit)
tests/     validator, engine, data realism
plan.md    full build plan and roadmap
```
