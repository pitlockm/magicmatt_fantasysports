# PROMPT 4 — Validation engine (the rules, deterministic)

Copy everything below into Claude Code. Run this after Prompts 2–3. Build inside `second_deck_association_process/sda/validation/`.

---

## Goal

Encode the league's contract and roster rules as **deterministic checks** that run against the DuckDB ledger + the latest Fantrax snapshot. Output: a per-team pass/fail report (also consumed by the HTML reports in Prompt 5 and the Discord bot in Prompt 6). No LLM judgment anywhere in here — pure logic.

## Rule sources

- The league constitution ("Second Deck Association Final" — Articles II, IV, V): §2.4 IL — eight IL spots, only players officially on the MLB Injured List eligible, must be removed once activated in real life, +1 cap-year relief per player on the IL while they remain; contracts 1–7 years for drafted/called-up players, 1–3 for in-season waiver/FA pickups; **78 contract-year cap** (+1 per player on the IL); drop penalty = half the remaining years, applied across subsequent years (no penalty for final-year drops); minors earn no contract years until called up; graduation at MLB roster + 1,000 career AB / 200 IP.
- Commissioner's tracked clarifications (treat as binding):
  - Cap-space trades are legal and adjust the effective cap.
  - Re-acquiring your own dropped player in-season caps at **3 years** (it's a waiver/FA pickup); the annual draft allows 1–7 for anyone.
  - New Fantrax MLB adds must have a matching Discord announcement within **1 day**, or they default to a 1-year deal.
  - Pool freeze: no player with `real_draft_year == current season` may be added after `freeze_date`.
  - Roster minimum: configurable (`min_mlb_roster`, currently unenforced; a future rule may require 26).

## Checks to implement

| ID | Rule | Fail condition |
|---|---|---|
| V1 | Cap | `team_cap_committed(team, season)` > 78 + (# players occupying Fantrax IL slots) + net CAP_TRADE adjustments |
| V2 | Contract bounds | drafted/called-up player with years <1 or >7; in-season waiver/FA pickup with years <1 or >3 |
| V3 | Re-sign tripwire | same team dropped a player and re-added him in-season with a new deal >3 years |
| V4 | Duplicates | one `fantrax_id` on two teams' MLB rosters in the latest snapshot |
| V5 | Coverage | Fantrax MLB-rostered player with no active contract event |
| V6 | FA-year stability | a player's `fa_year` changed without an EXTENDED/signed event explaining it |
| V7 | Drop penalties | DROPPED event missing for a player who left mid-contract; penalty schedule wrong (constitution example: 5 yrs remaining → 2.5 assessed on drop, then 2, 1.5, 1, 0 across subsequent years; final-year drops = 0) |
| V8 | Minors shuttle | contracted player sitting in a minors slot whose years are *not* in the team's cap total (Constitution 4.2: demoted-but-contracted players still count); also flag any player with 2+ MLB↔minors moves in 30 days for commissioner review |
| V9 | Pool freeze | player with `real_draft_year == season` added after `freeze_date` |
| V10 | Roster max | >26 MLB or >15 minors or >8 IL (Fantrax slot occupants) on any team |
| V11 | Roster min | < `min_mlb_roster` (skip entirely while config is null) |
| V12 | One-day signing | new Fantrax MLB add with no matching Discord announcement within 1 day → auto-append `DEFAULTED_1YR` event (`source='system'`) and include it in the report as an action taken |
| V13 | IL eligibility | player occupying a Fantrax IL slot who is **not** on the real-life MLB IL (healthy stash — fail); any minor-league player classified IL (fail — constitution §2.4 bars it); real-life-IL player sitting in an active slot while the team claims IL cap relief for him (flag for commissioner review only — slot-based relief is pending rules-committee ratification, see `rules-committee/il-slot-brief.md`) |

Each check returns: `rule_id`, `team_id`, `fantrax_id` (if player-specific), `passed: bool`, `detail: str`. The engine aggregates into a report object: per-team lists plus a league-wide summary count.

## The old "Legal" flag

The Sheet had a hand-typed Legal/Legal-Minors column. That concept is now **computed**: a player's contract is "legal" iff V1, V2, V7 pass for it. Never store a manual legality flag.

## What to build

- `sda/validation/rules.py` — one function per check (`v1_cap(...)`, …), each pure and independently testable.
- `sda/validation/engine.py` — `run_all(season)` → report dict; CLI `python -m sda.validation --season 2027` prints a readable summary and exits nonzero if any check fails (so the pipeline can alert).
- V12 needs the announcements feed: read from `pending_contracts` + a simple `announcements` table the Discord bot maintains (bot writes every parsed announcement there with its timestamp, even unapproved ones — the 1-day window keys off *announcement time*, not approval time).

## Acceptance criteria

- pytest fixture per rule, **including the constitution's own worked examples** (e.g. the 5→2.5→2→1.5→1→0 penalty schedule; the +1-per-IL-player cap relief).
- A fixture league where every rule passes AND a fixture league where every rule fails exactly once — the engine must produce exactly the expected failures, no more, no fewer.
- CLI exit code is nonzero on any failure.
