# CLAUDE.md

Personalised Offer Engine: decides which offer (or none) each synthetic customer gets, lets Claude
personalise it within rule limits, validates every choice, and delivers it via a Telegram bot.
See `plan.md` for the roadmap and `README.md` for setup.

## Commands

- Install: `.venv\Scripts\python -m pip install -r requirements.txt`
- Generate data: `.venv\Scripts\python -m data.generate --customers 500`
- Run bot: `.venv\Scripts\python -m bot.main` (needs `.env`, see `.env.example`)
- Tests: `.venv\Scripts\python -m pytest`

## Architecture

`engine/decide.py` runs one decision: `features.build_profile` → `eligibility.check` (rules) →
`scorer.rank` (rules-only pick) → `agent.choose` (Claude, optional) → `validator.validate` → `render.render`
→ row in `decisions`. Config lives in `config/*.yaml`, loaded and checked by `engine/rules.py` on every decision.

## Rules that must always hold

- Nothing is sent to a customer without passing `validator.validate()`, including rules-only fallback messages.
- The validator re-checks hard limits itself; never make it trust the eligibility step.
- The AI never writes numbers, prices, codes or dates. Those come from placeholders filled in `render.py`.
- The engine never reads `hidden_*` columns. They exist only to evaluate targeting later.
- Business limits belong in `config/rules.yaml`, not in code. Keep its comments plain English for marketers.
- Only synthetic customer data. Secrets live in `.env` (git-ignored); never commit tokens or keys.

## Conventions

- Python 3.13, type hints, small modules with a short docstring explaining their role.
- SQLite via `engine/db.py`; timestamps are ISO strings in India time (`db.now_ist()`).
- Add a test for every new rule, and a red-team case in `tests/test_validator.py` for every new message check.
