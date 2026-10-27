# Personalised Offer Engine — Build Plan

Hackathon 2 · Punjab Jobs AI Bootcamp · J-PAL South Asia · individual project

> **One-line goal:** decide *which* offer each customer should get (or none), let an AI personalise it **inside limits set by rules**, check every AI choice against those rules, and deliver it to classmates on Telegram. Then show, with numbers, that the targeting earns more than it costs.

**Deadlines**
| When | What |
|---|---|
| Thu 22 Oct (class, 120 min) | Start building. Get a "hello" bot live and share its link. |
| **Tue 27 Oct, 12:00 noon** | **Submit:** GitHub repo (code + README + CLAUDE.md) and the bot's username |
| Next session | Winners present. **The bot must stay running until judging is over.** |

## Status (22 Oct): MVP built

**Done:** git repo, venv, config files (`rules.yaml`, `offers.yaml` with Pydantic checks), synthetic data
generator with realism report, rules engine (eligibility + expected-profit scorer), Claude agent with
structured output, validator with retry and rules-only fallback, audit log, Telegram bot (`/start`, `/offer`,
`/profile`, `/newpersona`, `/stop`, admin `/campaign` + `/stats`, buttons Claim / Not for me / Why), 30 tests,
README, CLAUDE.md.

**You need to do:** create the bot with @BotFather, put the token (and optionally the Anthropic API key) in
`.env`, run `python -m bot.main`, and share the bot link with classmates.

**Done since:** marketing portal (Streamlit): overview dashboard, offer editor with on/off switch and live
message check, rules editor, customer preview; comment-preserving config saves; win-back free-delivery offer.

**Next (M1 to M3 leftovers):** sessions/browsing data and a past randomised campaign in the generator, holdout
group + strategy simulator, `/shop` with abandoned-cart trigger, always-on hosting.

---

## 1. How we'll be judged → what we build to win it

| Criterion | What we build | Proof we show the judges |
|---|---|---|
| **Core functionality** | Rules engine + AI agent + **validator that checks every AI choice** + Telegram delivery | Audit log: every decision, which rule passed/failed, what fell back. Tests that try to make the AI break the rules. |
| **Data realism** | Generator built on hidden customer "types" with realistic behaviour (heavy-tailed spend, cart abandonment, churn, paydays, festival peaks) | A "data realism report" page: charts compared against target ranges, checked automatically by tests |
| **Usability** | Rules in one plain-English YAML file **plus** a rule editor page with sliders and a "what happens if…" preview | A marketer changes the max discount, sees the effect, without touching code |
| **Design** | Short, friendly offer messages with buttons; clean dashboard | Screenshots of the bot and the dashboard in the README |
| **Advanced functionality** | Uplift targeting (skip people who'd buy anyway), cart-recovery ladder, frequency/fatigue limits, budget pacing, multilingual, fraud and fairness checks | Dashboard sections for each |
| **Relevance** | Every offer has a cost model and a business goal; we measure **incremental** profit with a random holdout group | "Blanket 10% off vs our engine" comparison chart |

**Our pitch angle (J-PAL):** J-PAL is about randomised evaluation. We build an **RCT into the system**: a random holdout group never gets offers, so we can measure the *causal* effect of the offers, not just redemptions.

---

## 2. System overview

```
 synthetic data (customers, products, events, past offers)
            │
            ▼
   customer profile (features: recency, frequency, spend, cart, points, churn risk…)
            │
            ▼
 ┌─ 0. Holdout check ── in holdout? ──► send nothing (control group)
 │          │
 │          ▼
 │  1. Eligibility filter (rules.yaml) ──► list of allowed offers + allowed ranges
 │          │
 │          ▼
 │  2. AI agent (Claude) ──► picks offer + value within range + timing + writes message (JSON)
 │          │
 │          ▼
 │  3. Validator (rules.yaml again) ── fails ──► retry once with errors ──► still fails ──► rules-only fallback
 │          │ passes
 │          ▼
 │  4. Render: our code fills in ₹ amount, %, coupon code, expiry (AI never invents these)
 │          │
 │          ▼
 │  5. Send on Telegram ──► buttons: [Claim] [Not for me] [Why this offer?]
 │          │
 │          ▼
 └─ 6. Log decision + response ──► dashboard, measurement, learning
```

The system works **without** the AI too (steps 1 → rules-only pick → 4). The AI makes offers better, but it is never in charge of the limits.

---

## 3. Components

### 3.1 Synthetic data (`data/generate.py`)

Generating realistic data is part of the brief and a judging criterion, so this gets real effort.

**The key idea:** every synthetic customer has a **hidden type** that drives their behaviour. The engine never sees the type; it has to infer it from behaviour, just like a real platform. Because we know the hidden truth, we can also measure how good our targeting really is.

| Hidden type | Share | Behaviour | What a smart engine should do |
|---|---|---|---|
| Deal hunter | ~25% | Buys mostly with discounts, low order value | Small % off works; watch margin |
| Loyal regular | ~15% | Buys often **anyway** | Don't discount (wasted margin). Points, early access. |
| Premium | ~10% | High order value, not price-sensitive | Early access, free express delivery, not % off |
| Festival shopper | ~20% | Buys in bursts around festivals and paydays | Time offers to festival season |
| New user | ~10% | Signed up recently, 0–1 orders | Welcome / first-order offer |
| Drifting (at risk) | ~10% | Used to buy regularly, gaps getting longer | **Win-back**: the most valuable target |
| Dormant | ~10% | Nothing in 6+ months | Mostly lost; one cheap try at most |
| + "Do not disturb" trait | across types | Annoyed by frequent offers, may opt out | Respect frequency caps |

**Tables (SQLite):**
- `customers`: id, synthetic name (Faker `en_IN`), gender, age band, city (Ludhiana, Amritsar, Jalandhar, Patiala, Mohali, Bathinda, Delhi…), city tier, signup date, acquisition channel, device (mostly Android), language (Punjabi / Hindi / English), preferred payment (UPI / COD / card), marketing opt-in, reward points + expiry date. Hidden: type, price sensitivity, category tastes, churn hazard, response to each offer type.
- `products`: ~200 items in fashion, electronics, beauty, home, grocery, each with price and **margin %** (electronics thin, fashion fat). Margins drive discount limits.
- `sessions`, `product_views`, `cart_events` (add / remove / abandon), `orders`, `order_items`, `returns`, `points_ledger`.
- `past_campaigns`: a past campaign where offers were **randomly** assigned. We use it to train and check the uplift model honestly.

**Realism targets** (approximate, in line with typical e-commerce patterns; checked by `tests/test_data_realism.py`):
- Top 20% of customers bring in roughly 55–75% of revenue (heavy-tailed, log-normal spend)
- Cart abandonment around 65–80%
- Most customers buy once; a minority become repeat buyers; the retention curve decays over time
- Weekly pattern (weekend peaks), payday spikes (1st–5th of month), festival season peak (Oct–Nov: Diwali, Gurpurab)
- COD share higher in smaller cities; returns higher in fashion than electronics
- Some messiness: missing ages, a few duplicate sessions, people who browse and never buy

Size: start with **500 customers** (fast), go to **5,000** once it works.

### 3.2 Customer profile (`engine/features.py`)

Computed per customer from the event tables:
- **RFM:** days since last order, number of orders, total spend
- Average order value, favourite categories, discount share (what % of orders used a coupon)
- **Churn risk:** days since last order compared with this customer's usual gap
- **Live cart:** items, value, minutes since abandoned
- Reward points balance and days to expiry
- Lifecycle stage: new / active / at-risk / lapsed / dormant
- Offer history: offers in the last 7 / 30 days, last response, opted out?
- Return rate (for abuse checks)

### 3.3 Offer catalogue (`config/offers.yaml`)

Each offer has a goal, eligibility, a range the AI may choose within, and a cost model.

| Offer | Goal | AI may choose |
|---|---|---|
| % off next order | Activation / win-back | 5–20% (capped by category margin) |
| Flat ₹ off above a minimum order | Raise order value | ₹50–₹300, min order ₹499–₹1,499 |
| Free delivery | Cart recovery (cheap) | — |
| Cart reminder (no discount) | Cart recovery (free) | timing, wording |
| Bonus reward points (2x / 3x) | Loyalty without cutting price | multiplier |
| Points-expiry reminder | Use points before they expire | — |
| Early access to sale | Premium / loyal (costs nothing) | — |
| UPI cashback (COD → prepaid nudge) | Fewer failed COD deliveries | ₹25–₹100 |
| Welcome / first-order offer | New users | 10–15% |
| Bundle / cross-sell | Raise order value | pick from related products |
| **No offer** | Don't waste margin on sure buyers | always allowed |

### 3.4 Rules (`config/rules.yaml`), written for a marketing team

One file, plain-English comments, every number adjustable. Example of the style:

```yaml
# ─── Money limits ───────────────────────────────────────────────
budget:
  monthly_discount_budget_inr: 200000   # total we can give away this month
  max_share_per_day: 0.10               # don't burn the budget in one day

limits:
  max_discount_percent: 20              # no offer above 20% off, ever
  max_discount_inr: 500                 # no offer worth more than ₹500
  min_margin_left_percent: 5            # after discount we must still earn 5%

category_max_discount_percent:          # thin-margin categories get less
  electronics: 5
  grocery: 8
  fashion: 30

# ─── Don't annoy people ──────────────────────────────────────────
frequency:
  max_offers_per_week: 2
  min_hours_between_offers: 48
  quiet_hours: "21:00-09:00"            # no messages at night
  pause_days_after_redeeming: 7

# ─── Who gets what ───────────────────────────────────────────────
targeting:
  holdout_percent: 10                   # random control group, never gets offers
  win_back_after_days_inactive: 60
  no_discount_if_bought_full_price_within_days: 14
  block_discounts_if_return_rate_above: 0.40
```

A Pydantic schema validates this file on load, so a typo gives a clear error ("`max_discount_percent` must be between 0 and 90") instead of a crash.

### 3.5 Decision pipeline (`engine/`)

1. **`eligibility.py`**: applies rules → list of allowed offers with allowed ranges for this customer (budget left, caps, cooldowns, category margins, opt-out).
2. **`scorer.py`** (rules-only baseline, always available): ranks allowed offers by **expected extra profit**:
   *extra profit ≈ (chance they buy with the offer − chance they'd buy anyway) × margin − expected discount cost.*
   Pick the best; if nothing beats "no offer", send nothing.
3. **`agent.py`** (AI): sends Claude the customer profile + the allowed offers and ranges. Claude returns structured JSON:
   `offer_id`, `value`, `send_at`, `language`, `message_template` (with placeholders like `{discount}`, `{code}`, `{expiry}`), `reason`.
   - Claude API via the official `anthropic` Python SDK, structured output with `client.messages.parse()` and a Pydantic model
   - Model: `claude-opus-5-5` by default, set in `.env` so it's a one-line change
   - Handle `stop_reason == "refusal"`, timeouts, and API errors by falling back to the rules-only pick
4. **`validator.py`**: deterministic, no AI. Checks:
   - offer is in this customer's allowed list; value within range and global limits; margin floor respected
   - budget, frequency cap, cooldown, quiet hours, opt-out, holdout
   - message: only allowed placeholders; **no stray numbers, % or ₹** typed by the AI; length limit; no fake urgency or banned words; correct language; no personal data beyond first name
   - on failure: one retry with the error list → otherwise rules-only fallback. Every outcome is logged.
5. **`render.py`**: our code fills in the real ₹ / % / coupon code / expiry. The AI can never invent a coupon or a price.
6. **`decide.py`**: runs 0 → 5 and writes a row to the `decisions` audit table.

### 3.6 Telegram bot (`bot/`)

Library: `python-telegram-bot` (async, with `[job-queue]` for timed triggers). Uses **long polling**, so no public URL is needed.

**Classmate flow**
1. `/start` → welcome + "all data here is synthetic; send /stop any time" → pick a language → get linked to a **synthetic customer** (random, or pick from 4 persona cards) → see your profile card.
2. Act like a shopper: `/shop` shows products with buttons → view → add to cart → buy, or walk away.
3. Triggers fire offers:
   - **Abandoned cart** (2 minutes in demo mode): reminder → free delivery → small discount, escalating only if needed
   - Points about to expire
   - Win-back for drifting / dormant personas
   - Batch campaign by admin
4. Offer message buttons: **[Claim offer] [Not for me] [Why this offer?]**. "Why" shows the plain-English reason and the rules it passed.
5. `/profile`, `/offers`, `/persona` (switch), `/stop` (opt-out, always respected), `/help`.

**Admin** (only for `ADMIN_CHAT_ID`): `/stats`, `/campaign`, `/reload_rules`.

**Message style** (target):
```
🛒 Your cart misses you, Simran!
The running shoes you picked are still in stock.
🎁 FREE delivery on this order, no minimum.
Code: SHIP-7KQ2 · valid till Sun 25 Oct, 11:59 pm
[ Claim offer ]  [ Not for me ]  [ Why this offer? ]
```

Telegram limits how fast a bot can send (roughly 30 messages/second overall), so batch campaigns send in a throttled loop.

### 3.7 Measuring whether offers work (`sim/`)

The brief asks: *how would you know your offers work?*
- **Random holdout (control group):** 10% of customers never get offers. The effect of offers is treatment minus control.
- **Offline campaign simulator:** run each strategy on all 5,000 synthetic customers, using their hidden true responses:
  1. No offers
  2. Blanket 10% off to everyone
  3. Rules-only targeting
  4. Rules + AI
  Compare **extra orders, extra revenue, discount cost, net extra profit, and opt-outs**. Expected story: blanket discounts lose money on people who'd buy anyway; targeting wins.
- **Uplift breakdown:** since we know the hidden truth, show who we hit: *persuadables* (good), *sure things* (wasted), *lost causes* (wasted), *do-not-disturb* (harmful).
- **Live (classmates):** claim rate, "not for me" rate, opt-outs by offer type. Small numbers, so present it as feedback, not proof.

### 3.8 Dashboard + rule editor (`dashboard/app.py`, Streamlit)

Pages:
1. **Overview**: offers sent, claimed, extra profit, budget used
2. **Strategy comparison**: the four strategies side by side (the headline chart)
3. **Guardrails**: AI decisions vs validator rejections by reason, fallback rate
4. **Rule editor**: sliders and inputs over `rules.yaml` → "preview impact" re-runs the simulator → save
5. **Data realism report**: distributions and checks from 3.1
6. **Live bot**: classmates reached, recent decisions with their "why"

---

## 4. Tech stack

| Need | Choice |
|---|---|
| Language | Python 3.13 (already installed) |
| Bot | `python-telegram-bot[job-queue]` |
| AI | `anthropic` SDK, `claude-opus-5-5`, structured outputs + Pydantic |
| Config + validation | `PyYAML` + `pydantic` |
| Storage | SQLite (`sqlite3`, one file, no server) |
| Data | `numpy`, `pandas`, `Faker` |
| Uplift model (stretch) | `scikit-learn` |
| Dashboard | `streamlit` + `plotly` |
| Tests | `pytest` |
| Secrets | `python-dotenv` + `.env` (git-ignored) |

---

## 5. Repo layout

```
hack2/
├── README.md              # setup, usage, screenshots, bot username
├── CLAUDE.md              # project guide for Claude Code (required)
├── plan.md                # this file
├── .env.example           # TELEGRAM_BOT_TOKEN=, ANTHROPIC_API_KEY=, LLM_MODEL=, ADMIN_CHAT_ID=
├── .gitignore             # .env, .venv/, *.db, __pycache__/
├── requirements.txt
├── config/
│   ├── rules.yaml         # business limits (marketing edits this)
│   ├── offers.yaml        # offer catalogue
│   └── calendar.yaml      # festivals, paydays
├── data/
│   └── generate.py        # synthetic data → offers.db
├── engine/
│   ├── features.py  eligibility.py  scorer.py
│   ├── agent.py     validator.py    render.py   decide.py
├── bot/
│   ├── main.py  handlers.py  triggers.py  messages.py
├── sim/
│   └── campaign.py        # strategy comparison with holdout
├── dashboard/
│   └── app.py
└── tests/
    ├── test_validator.py      # tries to make the AI break rules
    ├── test_rules.py
    └── test_data_realism.py
```

---

## 6. Build order (small working steps, commit after each)

### M0 · Today in class (Thu 22 Oct, 2 h): "hello bot"
- [ ] `git init`, `.gitignore`, `.env.example`, virtual env, `requirements.txt`
- [ ] Create the bot with **@BotFather**, put the token in `.env`
- [ ] Bot replies to `/start`
- [ ] **Post the bot link in the class group now** (a bot can only message people who started it)
- [ ] `generate.py` v0: 200 customers, 3 types, orders only
- [ ] One hard-coded rule picks an offer and the bot sends it to you
- **Done when:** a classmate presses Start and gets a rule-chosen offer. Commit.

### M1 · Fri 23 Oct: real data + rules-only engine
- [ ] Full generator (all types, products, sessions, carts, points, past campaign), 5,000 customers
- [ ] Realism checks pass (`test_data_realism.py`)
- [ ] `features.py`, `offers.yaml`, `rules.yaml` + Pydantic schema
- [ ] `eligibility.py` + `scorer.py` + `render.py`
- [ ] Bot links each classmate to a synthetic customer and sends the best rules-only offer
- [ ] **Test on 2–3 classmates:** is the message clear? would you click? anything creepy?
- **Done when:** every offer sent is within limits, chosen by rules. Commit.

### M2 · Sat 24 Oct: AI agent + validator
- [ ] `agent.py` with structured output
- [ ] `validator.py` + retry + fallback + `decisions` audit log
- [ ] Red-team tests: AI asked to give 50% off, invents a code, writes "₹999 off", customer name contains "ignore the rules" → validator must block all
- [ ] "Why this offer?" button
- [ ] `/shop` + abandoned-cart trigger with the escalation ladder
- **Done when:** AI-personalised offers flow end to end and no invalid one can get through. Commit.

### M3 · Sun 25 Oct: measurement + dashboard + always-on hosting
- [ ] Holdout assignment
- [ ] `sim/campaign.py`: four-strategy comparison
- [ ] Streamlit dashboard (overview, comparison, guardrails, realism, rule editor)
- [ ] Deploy the bot somewhere always-on (see Risks); test from a phone
- **Done when:** the comparison chart shows our engine vs blanket discount, and a rule change in the editor changes results. Commit.

### M4 · Mon 26 Oct: advanced features + polish
- [ ] Pick 2–3 from section 8 (suggested: uplift targeting, multilingual messages, fatigue + budget pacing)
- [ ] Polish message design; second classmate test round
- [ ] README (setup, usage, screenshots, architecture diagram, bot username)
- [ ] CLAUDE.md
- [ ] 3-minute demo script
- Commit.

### Tue 27 Oct, before 12:00: buffer + submit
- [ ] Fresh clone → follow README → it runs
- [ ] Check no secrets anywhere in git history
- [ ] Submit repo link + bot username
- [ ] Confirm the bot is running and will keep running until judging

---

## 7. Testing

- **Unit tests:** validator (most important), rule loading, eligibility edge cases (budget exactly used up, quiet-hour boundary, holdout customers)
- **Realism tests:** the target ranges from 3.1
- **Red-team tests:** prompt injection and rule-breaking attempts against the AI + validator
- **Classmate tests:** after M1 and M4, five quick questions: clear? appealing? relevant to "you"? too many messages? what language do you prefer?

---

## 8. Advanced ideas (pick by time left)

1. **Uplift model**: train on the random past campaign (T-learner, scikit-learn) to predict who is *persuadable*; skip sure buyers and do-not-disturb customers
2. **Learning from live responses**: Thompson-sampling bandit over offer types
3. **Cart-recovery ladder**: free reminder → free delivery → small discount, escalating only when needed
4. **Send-time optimisation**: send at the customer's usual active hour, respecting quiet hours
5. **Budget pacing**: spread spend over the month, slow down if burning too fast
6. **Multilingual**: Punjabi (Gurmukhi), Hindi, English, Hinglish
7. **Festival + payday awareness** from `calendar.yaml`
8. **COD → UPI nudge**: small prepaid cashback to cut failed COD deliveries
9. **Abuse checks**: coupon-only serial buyers, very high return rates, many accounts on one device
10. **Fairness audit**: offer rates and sizes by city tier and gender; flag gaps not explained by behaviour
11. **"Why this offer?"** transparency for every message

---

## 9. Secrets and safety

- `.env` holds `TELEGRAM_BOT_TOKEN`, `ANTHROPIC_API_KEY`, `ADMIN_CHAT_ID`, `LLM_MODEL`; `.env` is in `.gitignore` from the **first** commit
- Commit `.env.example` with empty values only
- If a token ever leaks: revoke it in @BotFather / the Anthropic console straight away
- Only synthetic data; classmates are told so at `/start`. We store only their Telegram chat id and button clicks.
- `/stop` always works and is permanent unless they `/start` again

---

## 10. Risks and fixes

| Risk | Fix |
|---|---|
| Classmates don't start the bot, so it can't message them | Share the link on day 0; send a reminder |
| Laptop sleeps, so the bot dies before judging | Host on an always-on service (e.g. a small cloud VM or a worker on Railway / Render / Fly.io; check current free tiers). Laptop is backup only. |
| AI is slow, costs too much, or is down | Rules-only fallback always works; cache AI output per customer; cap calls per day |
| AI breaks a rule | Validator is deterministic and runs on **every** AI output; AI never writes numbers or codes |
| Project sits in **OneDrive** | Keep `.venv` out of git; if OneDrive syncing the venv gets slow, create the venv outside OneDrive |
| Scope creep | Each milestone ends in a working, committed system; advanced features only after M3 |

---

## 11. Open decisions (yours)

1. **AI key:** do you have an Anthropic API key (or a bootcamp-provided one)? The plan defaults to `claude-opus-5-5`; a cheaper model such as `claude-haiku-5-5` is a one-line change in `.env` if cost matters, your call.
2. **Hosting** for the bot until judging: which service?
3. **Persona assignment:** random, or let classmates pick?
4. **Languages:** English only, or English + Punjabi + Hindi?
5. **Names:** project name and bot username.

---

## 12. Submission checklist

- [ ] GitHub repo is public (or shared with judges)
- [ ] README: what it does, setup, usage, screenshots, architecture, bot username
- [ ] CLAUDE.md
- [ ] Small, meaningful commits throughout
- [ ] No secrets in the repo or its history
- [ ] Bot username submitted; bot running until judging is complete
