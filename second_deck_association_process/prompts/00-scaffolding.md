# PROMPT 0 — Project scaffolding & shared conventions

Copy everything below into Claude Code as the first prompt.

---

You are working in the repo `pitlockm/magicmatt_fantasysports`. **All new work goes in `second_deck_association_process/`** — this is a fresh build starting from an empty folder.

The sibling folder `fantasy_baseball_roster_management/` is **REFERENCE ONLY**: my earlier personal experiments doing team-level analysis from public rankings. Do not copy its structure, do not import from it, do not let it shape your design. You may peek at it for ideas only if you are genuinely stuck.

## What we're building

The data pipeline for the **Second Deck Association (SDA)**, a 10-team dynasty fantasy baseball league hosted on Fantrax (league ID `4fyzhujxmk7scnaf`). Today, contracts and the salary cap are tracked in a hand-edited Google Sheet, which is error-prone and has no audit trail. The new system will:

1. Pull league data (rosters, standings, transactions, draft) from Fantrax's official API.
2. Store contracts in DuckDB with an **append-only ledger** (every contract event is a row; nothing is ever edited).
3. Validate league rules deterministically (cap, contract bounds, drop penalties, etc.).
4. Generate static HTML reports (multi-year cap grid, FA projection, waivers, standings, league history).
5. Run a Discord bot that watches for contract announcements, validates them against the rules, and queues them for commissioner approval.

I am the league commissioner. Python 3.11+.

## Create this layout inside `second_deck_association_process/`

```
second_deck_association_process/
  sda/
    __init__.py
    fantrax/        # Prompt 1: API client
    db/             # Prompt 2: data model + ledger
    validation/    # Prompt 4: rule engine
    reports/        # Prompt 5: HTML reports
    discord_bot/    # Prompt 6: Discord bot
    pipeline/       # Prompt 7: nightly orchestration
  config/
    season.yaml
  tests/
    fixtures/
  data/             # gitignored: raw snapshots, sda.duckdb
  .env.example
  .gitignore
  README.md
  requirements.txt
```

## Stack

`requirements.txt`: `requests`, `jinja2`, `discord.py`, `pyyaml`, `pytest`, `duckdb`.

## `config/season.yaml` — create with these 2027 values

```yaml
league_id: 4fyzhujxmk7scnaf
season: 2027
opening_day: 2027-03-25
freeze_date: 2027-04-25   # rule: opening_day + 30 days; must always precede the MLB rookie draft (mid-July)
trade_deadline: null      # TODO: pull Fantrax's default when the 2027 season rolls over (2026 was Aug 12)
draft_start: 2027-03-13
draft_end: 2027-03-18     # finishes one week before Opening Day
min_mlb_roster: null      # no minimum enforced yet; a future rule may require 26
discord_announcements_channel_id: 1496593362135027963
```

Note: MLB's CBA expires Dec 2026 and a lockout may move Opening Day 2027. All dates must live in this one file so they can be re-anchored without code changes.

## `.env.example`

```
FANTRAX_USER_SECRET_ID=
DISCORD_BOT_TOKEN=
```

No real values. `.gitignore` must cover `data/` and `.env`.

## Conventions — apply to every prompt after this one

- Type hints on all functions; docstrings on public functions.
- `pytest` for every validation rule; fixtures under `tests/fixtures/`.
- Structured logging via stdlib `logging`; no `print()` in library code.
- Config comes from `config/season.yaml` + `.env`. Never hardcode IDs, tokens, or dates in code.
- Every module gets a `--help` / dry-run path where it makes sense.

## Acceptance criteria

- `pip install -r requirements.txt` succeeds.
- `pytest` runs clean (zero tests is fine for now; it must not error).
- `python -m sda.pipeline --help` prints help text (a stub is fine at this stage).
- `README.md` explains what the project is, the folder layout, and the build order (Prompts 0→7).
