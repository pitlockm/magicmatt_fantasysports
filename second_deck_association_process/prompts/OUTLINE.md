# SDA Tracking System — Claude Code Prompt Outline

**League:** Second Deck Association (10-team dynasty fantasy baseball, Fantrax)
**Goal:** Replace the editable Google Sheet contract tracker with a Python/SQLite pipeline producing read-only HTML reports, plus a Discord bot for contract announcements.
**Workflow:** These are broad-stroke specs. Each section expands into one self-contained prompt for Claude Code. The assistant QCs generated code afterward.

## Prompt 0 — Project scaffolding & shared conventions

- Repo layout: `sda/` package with `sda/fantrax/`, `sda/db/`, `sda/validation/`, `sda/reports/`, `sda/discord/`, `sda/pipeline/`; `config/` for YAML; `tests/`; `data/` for local runtime files (gitignored).
- Python 3.11+; dependencies: `requests`, `jinja2`, `discord.py`, `pyyaml`, `pytest`, `duckdb` (DuckDB is the storage engine — already in use in the repo).
- Config: `config/season.yaml` holds `league_id` (4fyzhujxmk7scnaf), season year, `freeze_date`, `trade_deadline`, draft dates, `min_mlb_roster`. Secrets (Fantrax `userSecretId`, Discord bot token) live in `.env`, never committed.
- **2027 recommended values (documented):** `freeze_date: 2027-04-25` — rule is `freeze_date = opening_day + 30 days` (Opening Day 2027 is March 25); must always precede the MLB rookie draft (mid-July, All-Star week). **Caveat:** the MLB CBA expires 2026-12-01 and a lockout is widely expected — if Opening Day moves, re-anchor `freeze_date` and the draft window, don't hardcode. `trade_deadline:` TODO — pull Fantrax's default when the 2027 league season rolls over in Fantrax (2026's was Aug 12; unconfirmed whether that's the default). Slow draft window: **2027-03-13 → 2027-03-18** (see below).
- Conventions: type hints, docstrings, pytest for every validation rule, structured logging.
- Acceptance: `pytest` passes; `python -m sda.pipeline --help` works.

## Prompt 1 — Fantrax API client

- Official API: https://www.fantrax.com/developer (v1.8 Beta), base `https://www.fantrax.com/fxea/general/`. REST/JSON, no auth needed except `getLeagues` (uses `userSecretId` from the Fantrax profile page, pasted into `.env`).
- Endpoints to wrap: `getPlayerIds` (master player-ID map — the identity anchor for everything), `getLeagueInfo`, `getTeamRosters` (includes minors), `getStandings`, `getMatchupScores`, `getDraftPicks`, `getDraftResults`, `getAdp`.
- Daily JSON snapshots to `data/raw/`; retries and polite rate-limiting; idempotent re-runs.
- Normalization: players keyed on Fantrax player ID; maintain a name-alias map for matching Sheet rows and Discord announcements (nicknames!) to IDs.
- Acceptance: one command pulls full league state into snapshot files.

## Prompt 2 — Data model (DuckDB + append-only ledger)

- Live DB is a local DuckDB file (`data/sda.duckdb`), gitignored. After each run, commit a **text snapshot** (SQL dump or Parquet→CSV export) so git history is the audit trail.
- Tables: `teams`, `players` (`fantrax_id` PK, name aliases, `real_draft_year`, positions), `contract_events` (**append-only**: timestamp, team, player, event type — SIGNED / EXTENDED / DROPPED / EXPIRED / CALLED_UP / CAP_TRADE — years, FA year, source [discord / manual / migration / fantrax], note, approved_by), `roster_snapshots`, `pending_contracts` (Discord approval queue), `season_config`, `league_history` (season records, champions, prize winnings).
- **Roster level vs move type:** `roster_level` (MLB / minors) is a classification separate from `move_type` (Draft / Waiver / …). The tracker's Move Type value `DFA` was only ever a stand-in for minor-league status — migrate those rows to `roster_level=minors`, never as a move type.
- Current state is always derived by replaying `contract_events`; `years_remaining = fa_year − current_season`.
- Acceptance: rebuild the DB from snapshots alone; ledger-replay unit test.

## Prompt 3 — Sheets migration (one-time backfill)

- Source: "SDA - Major League Contract Tracker" (CSV export or Sheets API). Commissioner's local copy lives at `/Users/matthewpitlock/Development/SDACommishprocess/data/contractmigrationdata/` — **one CSV per tab is needed** (every team tab, Salary Cap Tracking, Estimated Free Agent Class). Note: the file shared so far (`... - Intro Page.csv`) appears to be only the Intro Page tab; the migration needs all of them. In Google Sheets: File → Download → Comma-separated values, once per tab (switch tabs via the bottom bar).
- Map columns — Player, Pos, FA Year, Move Type (Draft / Waiver / DFA / NULL), Legal flag, IL, Dropped, Years, Year added — into `contract_events` with `source=migration`. Move Type `DFA` → `roster_level=minors` (not a move type).
- Produce a **reconciliation report**: every Sheet player matched to a Fantrax roster entry; orphans listed; cap totals recomputed vs the Sheet's Salary Cap Tracking tab; every discrepancy flagged for commissioner review before cutover.
- Import the Cap Trade Tracker rows as first-class CAP_TRADE events (supported per commissioner; recommend committee ratification since the constitution is silent).
- Seed `league_history` from the tracker's League History tab (champions, records, prize winnings); gaps filled manually.
- After cutover the Sheet becomes a read-only reference.
- Acceptance: reconciliation report with zero *unexplained* discrepancies.

## Prompt 4 — Validation engine (the rules, deterministic)

Encode Constitution Art. IV plus the tracked clarifications. Output: per-team pass/fail report.

- **V1 cap:** Σ contract years ≤ 78 + (# of IL players), adjusted by CAP_TRADE events (cap years traded in/out). IL from Fantrax roster (Inj Res slots) reconciled with the tracker's IL flag.
- **V2 bounds:** drafted / called-up players 1–7 years; in-season waiver/FA pickups 1–3 years.
- **V3 re-sign tripwire:** same-team drop → re-add in-season with a new deal **>3 years** = violation. (Annual draft allows 1–7 for anyone.)
- **V4 duplicates:** no player on two MLB rosters.
- **V5 coverage:** every Fantrax MLB-rostered player has an active contract event.
- **V6 FA-year consistency:** FA years stable across migration and derivation.
- **V7 drop penalties:** dropped players carry the penalty schedule (constitution example: 5 yrs remaining → 2.5 on drop, then 2, 1.5, 1, 0 across subsequent years; no penalty for final-year drops). Verify applied. Dropped multi-year players' dead cap must appear in the grid report.
- **V8 minors shuttle:** a contracted player in a minors slot must still count cap years (per 4.2); log every MLB↔minors move; flag 2+ moves in 30 days for review.
- **V9 pool freeze:** no player with `real_draft_year == current season` added after `freeze_date`. (TODO: confirm whether Fantrax has a pool-lock date setting or the freeze is manual/system-side.)
- **V10 roster max:** ≤26 MLB + ≤15 minors.
- **V11 roster minimum (future rule):** configurable `min_mlb_roster` (default: no minimum — teams may currently carry fewer than 26). The commissioner intends to require a full 26-man roster in a future season; the system must support flipping this to an enforced minimum without code changes. → Rules committee to adopt.
- **V12 one-day signing rule:** any player newly added to a Fantrax MLB roster (waiver/FA add, minor-league call-up) must have a matching Discord signing announcement within 1 day of the add date. No announcement in the window → the system auto-appends a 1-year SIGNED contract event (`source=system`, note="no announcement within 1 day — defaulted") and the bot posts a notice. Applies to in-season adds; draft picks are signed at draft time; trades are assumed to carry the player's existing contract (confirm with commissioner).
- The tracker's hand-entered "Legal" flag becomes **computed**, not typed.
- Acceptance: pytest fixtures for every rule, including the constitution's own worked examples.

## Prompt 5 — HTML reports (read-only; separate pages, one site)

Static Jinja2 pages on the league website (see Prompt 7 / website notes). Mobile-friendly; client-side sorting where needed; no JS framework required.

- **Report 1 — Multi-year grid.** The spreadsheet-style visualization: teams × next 7 years, committed cap years per cell, drill-down to players. Includes **dead cap** — dropped multi-year players whose penalties still hit the cap. Minor leaguers live in a **separate section**, never in the contract grid (planning-only, as in the Doc today).
- **Report 2 — Free-agent projection.** FA class by year (from the Estimated Free Agent Class concept); multi-year expirations highlighted.
- **Report 3 — Waiver wire.** Top 10 highest-rostered players currently on waivers (recently dropped — not free agents); fewer than 10 is fine. Source: Fantrax player-pool feed filtered by waiver status (confirm exact feed during build; fallback is the players CSV export).
- **Report 4 — Standings.** Sortable table: every team ranked by category totals and category points (R, HR, RBI, SB, OBP / QS, K, ERA, WHIP, SVH). Click-to-sort columns.
- **Report 5 — League history.** Season records, champions, prize-money winnings (seeded from migration; appended each season).
- Supporting views: contract ledger (audit trail), validation report, transaction log.
- Scope rule (per commissioner): contract views cover multi-year players only; 1-year/expiring players live in the FA projection.
- Distribution: Discord bot posts the report link after each regeneration (link unfurl); archive copy to Google Drive.
- Acceptance: full site builds from a fixture DB.

## Prompt 6 — Discord bot (propose → validate → approve)

- `discord.py`, long-running on the commissioner's laptop (launchd service for auto-start); token in `.env`.
- Watches the announcements channel (ID `1496593362135027963`, in config as `discord_announcements_channel_id`); parses signings. League adopts a standard format — `📝 SIGNING: <player> — <N> years — <team>` — with free-text fallback parsing.
- **Pre-recording validation** against the constitution: contract bounds by acquisition type, cap room including IL relief and cap trades, roster limits. Nothing is recorded until it validates.
- Posts a confirmation card (parsed details + rule pass/fail) and queues in `pending_contracts`.
- Approval: commissioner ✅ reaction or `/contract approve`. On approval → append ledger event → regenerate HTML → post the link.
- **One-day signing rule:** the bot matches each new Fantrax MLB addition against Discord announcements within a 1-day window. No announcement in time → auto-append the 1-year default (ledger event, `source=system`) and post a notice in the channel. The announcement format's timestamp is the source of truth for the window.
- **Startup catch-up:** on launch, scan channel history since the last processed message ID (laptop sleep is safe).
- v1 may ship the slash-command path (`/contract propose`) before free-text parsing.
- Acceptance: dry-run mode that validates without writing anything.

## Prompt 7 — Nightly pipeline, operations & website

- Orchestration: pull Fantrax → snapshot → validate → regenerate HTML → commit snapshots + HTML → push.
- Scheduling: cron/launchd on the laptop; idempotent runs; run log; Discord DM to the commissioner on validation failures.
- **Website (GitHub Pages):** free, no server. Setup: repo Settings → Pages → deploy from branch (`main`, `/docs` folder or `gh-pages` branch); the pipeline's push already regenerates the site — each push redeploys automatically. Address `https://pitlockm.github.io/magicmatt_fantasysports/` (or a custom domain via `CNAME` + DNS, optional). The repo is public so the site is public — fine for a fantasy league; the build must strip secrets (no tokens in HTML). Effort: ~15 minutes once; the pipeline does the rest forever. Alternatives (Cloudflare Pages, Netlify) are also free but unnecessary for v1.
- Docs: README (setup, `.env`, first backfill), OPERATIONS.md (common tasks), LEAGUE_CALENDAR.md.
- Acceptance: end-to-end dry run on fixture data.

## Appendix — Open questions & gaps (from league document review)

1. **Cap-space trading:** supported per commissioner — build it in. The constitution is silent, so recommend committee ratification to codify it.
2. **Pool freeze mechanics:** TODO — confirm whether Fantrax exposes a player-pool lock date or the freeze is enforced manually/system-side.
3. **Annual dates** for `season_config`: SDA slow-draft date, contract expiry point, waiver-budget reset, pool `freeze_date`. 2027 slow draft: last year's took **11.14 days** (Feb 25 12:15 ET → Mar 8 15:40 ET, 410 picks); 60% reduction → **~4.5 days**, backdated to **finish one week before Opening Day** → recommended window **Mar 13–18, 2027** (~92 picks/day vs ~37 last year — the per-pick clock gets much tighter).
4. **IL relief source of truth:** Fantrax Inj Res slots vs the tracker's IL column — pick one.
5. **Drop-and-re-sign:** needs 7/10 vote. Brief drafted at `../rules-committee/drop-and-resign-brief.md`; the 3-year tripwire is the key tracking point.
6. **26-man roster requirement (future):** currently teams may carry fewer; commissioner wants to require 26 → rules committee. System supports via `min_mlb_roster` config.
7. **Approval UX edge:** commissioner approving his own team's signings — note in the bot prompt (suggest a league-visible log).
8. **Out of scope for v1:** two-way player lineup handling, the GS band (3–10 starts) for ERA/WHIP — scoring concerns, not contract concerns.
9. **Phase 2 — minors module:** call-up detection, MLB↔minors shuttle flagging, graduation tracking (MLB roster + 1,000 AB / 200 IP) with warnings; MLB Stats API as the likely career-stat feed.
