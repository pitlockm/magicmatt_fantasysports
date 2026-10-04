# PROMPT 2 — Data model (DuckDB + append-only ledger)

Copy everything below into Claude Code. Run this after Prompt 1. Build inside `second_deck_association_process/sda/db/`.

---

## Storage design

- Engine: **DuckDB**. Live database file: `second_deck_association_process/data/sda.duckdb` (gitignored — never commit the binary).
- After every run that changes data, export a **text snapshot** (DuckDB `COPY ... TO 'snapshot.csv'` per table, or a SQL dump) into `second_deck_association_process/data/snapshots/<date>/` and commit *those*. Git history of the snapshots is the audit trail.
- Single-writer discipline: only one process (the nightly pipeline or the Discord bot) writes at a time. Keep a tiny lock file (`data/.db.lock`) — if it's held, the writer waits or exits with a clear message.

## The core idea: append-only contract ledger

Contracts are never updated in place. Every contract event is a row; current state is derived by replaying events. This replaces a hand-edited spreadsheet where anyone could silently change history.

## Schema

```sql
-- Teams (stable; seed from getLeagueInfo)
CREATE TABLE teams (
  team_id      VARCHAR PRIMARY KEY,   -- Fantrax teamId
  team_name    VARCHAR NOT NULL,
  manager      VARCHAR
);

-- Players (identity anchored on Fantrax playerId; see Prompt 1 alias map)
CREATE TABLE players (
  fantrax_id     VARCHAR PRIMARY KEY,
  name           VARCHAR NOT NULL,
  positions      VARCHAR,             -- e.g. '1B,OF'
  mlb_team       VARCHAR,
  real_draft_year INTEGER,           -- year drafted in the real MLB draft; NULL if undrafted/unknown
  aliases        VARCHAR              -- JSON array of alternate names/nicknames
);

-- Contract events: APPEND-ONLY. Never UPDATE or DELETE rows here.
CREATE TABLE contract_events (
  event_id    INTEGER PRIMARY KEY,   -- autoincrement
  ts          TIMESTAMP NOT NULL,    -- when the event happened (not when recorded)
  recorded_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  team_id     VARCHAR NOT NULL REFERENCES teams(team_id),
  fantrax_id  VARCHAR NOT NULL REFERENCES players(fantrax_id),
  event_type  VARCHAR NOT NULL,      -- SIGNED | EXTENDED | DROPPED | EXPIRED | CALLED_UP | CAP_TRADE | DEFAULTED_1YR
  years       DOUBLE NOT NULL,       -- contract years granted (for DROPPED: penalty years assessed)
  fa_year     INTEGER,               -- first year the player becomes a free agent (NULL for CAP_TRADE)
  source      VARCHAR NOT NULL,      -- form | manual | migration | fantrax | system
                                 -- ('form' = via the Google Form intake (Prompt 8); 'discord' is retired)
  note        VARCHAR,
  approved_by VARCHAR,               -- commissioner who approved (NULL for system/migration)
  roster_level VARCHAR NOT NULL DEFAULT 'MLB',  -- MLB | minors
  -- Form-intake metadata (NULL for non-form sources; added 2026-10-04):
  form_ref         VARCHAR,           -- submission UUID from the relay (dedupe key)
  acquisition_type VARCHAR,           -- drafted | called_up | waiver (drives V2 year bounds)
  announced_at     TIMESTAMP          -- form submit time; the one-day rule keys off this
);

-- Nightly Fantrax roster states (for diffing: detects call-ups, drops, adds)
CREATE TABLE roster_snapshots (
  snapshot_date DATE NOT NULL,
  team_id       VARCHAR NOT NULL,
  fantrax_id    VARCHAR NOT NULL,
  roster_level  VARCHAR NOT NULL,    -- MLB | minors | IL (IL = occupying a Fantrax IL slot; minors never IL)
  PRIMARY KEY (snapshot_date, team_id, fantrax_id)
);

-- Commishbot's approval queue (Google Form submissions relayed via Discord)
CREATE TABLE pending_contracts (
  pending_id  INTEGER PRIMARY KEY,
  created_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  team_id     VARCHAR NOT NULL,
  fantrax_id  VARCHAR NOT NULL,
  years       DOUBLE NOT NULL,
  fa_year     INTEGER,
  raw_message VARCHAR NOT NULL,      -- the original Discord announcement text
  status      VARCHAR NOT NULL DEFAULT 'pending'  -- pending | approved | rejected | expired
);

-- League config per season (mirrors config/season.yaml)
CREATE TABLE season_config (
  season            INTEGER PRIMARY KEY,
  freeze_date       DATE,
  trade_deadline    DATE,
  draft_start       DATE,
  draft_end         DATE,
  min_mlb_roster    INTEGER,         -- NULL = not enforced
  cap_years         INTEGER NOT NULL DEFAULT 78
);

-- League history (seeded from the old tracker's League History tab)
CREATE TABLE league_history (
  season          INTEGER PRIMARY KEY,
  champion        VARCHAR,
  runner_up       VARCHAR,
  regular_1st     VARCHAR,
  prize_champion  DOUBLE,
  prize_runner_up DOUBLE,
  notes           VARCHAR
);
```

## Critical modeling rules

1. **`roster_level` ≠ `move_type`.** Whether a player is on the MLB roster or in the minors is a *classification*, not a move type. (The old tracker used Move Type "DFA" as a stand-in for minor-league status — that was a hack; don't reproduce it.)
2. **Cap trades are first-class.** `CAP_TRADE` events move cap *years* between teams (years can be fractional, e.g. 2.5). They adjust a team's effective cap, not any player's contract.
3. **Drop penalties are events.** When a player is dropped mid-contract, append a `DROPPED` event with the penalty years; the penalty then counts against the team's cap in each subsequent year per the schedule (see Prompt 4). Dropped multi-year players = "dead cap" and must stay queryable.
4. **Derived state, not stored state.** "Current contracts" = latest non-expired event per (team, player). `years_remaining = fa_year - current_season`. Build this as a **view** (`current_contracts`), not a table.
5. **One-day default.** If the validation engine (Prompt 4) finds a new Fantrax add with no matching announcement, it appends a `DEFAULTED_1YR` event (source='system'). The schema must support that flow.
6. **IL is slot-based.** `roster_level = 'IL'` means the player occupies a Fantrax IL roster slot (manager action) — never inferred from real-life injury status. Minor-league players can never be `'IL'` (constitution §2.4: only players officially on the MLB Injured List are eligible). A real-life-IL player in an active/bench slot is `'MLB'`.
7. **No contracts for minor leaguers.** The old tracker's DFA rows with contract years were a planning tool, not deals — the migration creates no events for them. The ledger rejects `SIGNED`/`EXTENDED` with `roster_level='minors'`; a minor leaguer's first real deal is a `CALLED_UP` event (1–7 years, `roster_level='MLB'`). The `current_contracts` view excludes `roster_level='minors'` rows so placeholders never touch the cap. Demoted players (signed at MLB level, now in minors) still count per constitution 4.2 — the filter keys on the event's level at signing, not the player's current level.

## What to build

- `sda/db/schema.py` — creates all tables/views from the DDL above (idempotent; safe to re-run).
- `sda/db/ledger.py` — `append_event(...)` (validates fields, never allows UPDATE/DELETE on `contract_events` — enforce by convention + a test), `current_contracts(season)`, `team_cap_committed(team_id, season)` (sums years_remaining + dead cap + cap-trade adjustments), `record_roster_snapshot(date, rosters)`.
- `sda/db/snapshots.py` — `export_text_snapshot()` → CSVs per table into `data/snapshots/<date>/`.
- CLI: `python -m sda.db init` (create schema), `python -m sda.db snapshot`.

## Acceptance criteria

- Fresh `init` builds the whole schema; re-running `init` doesn't error or duplicate.
- Ledger test: append SIGNED (5 yrs) → DROPPED (penalty 2.5) → verify `current_contracts` and `team_cap_committed` reflect the dead cap; verify no UPDATE/DELETE path exists for `contract_events` (test attempts one and fails).
- Snapshot export produces committable text files; a rebuild-from-snapshot test restores an identical DB.
