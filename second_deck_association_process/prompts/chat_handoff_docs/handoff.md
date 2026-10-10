# Prompt Implementation Handoff

**Status date:** 2026-10-09
**Branch:** `main` at `6352748`, synchronized with `origin/main`; report, prompt, and handoff refinements are local and uncommitted
**Implementation state:** The migration dry-run is clean with official multi-year minors contracts preserved and counted toward cap. The daily V14 promotion validation is documented but not yet implemented. No production contract events, live Discord connection, Apps Script deployment, or pipeline Git publish has been run.

## Summary

Implemented the current Forms → Discord webhook → commishbot intake model and its supporting report/pipeline data contract. Google Forms are the only manager input for long-term signings and cap trades. The Apps Script relay emits strict two-line messages with a UUID `ref`, acquisition type where applicable, and UTC submission timestamp. The commishbot parses those relays, validates proposals, queues them for commissioner approval, and records approved events with Form provenance.

The one-time contract migration has conservative fuzzy player matching scoped first to the Fantrax roster of the mapped owner/team, then a global fuzzy fallback. It supports the commissioner-confirmed 2026 drop-year default, derives FA year from year added plus contract duration, treats blank Move Type as waiver only for 1–3 year contracts, and flags active unrostered contracts over three years. Per-row commissioner decisions are in `config/manual_migration_resolutions.json`; the review record is `data/manual_contract_resolution.md`.

## Implementation Changes

### Ledger and snapshots

- Added `form_ref`, `acquisition_type`, and `announced_at` fields to contract events, pending submissions, and announcement/processed-message records; added unique Form-reference indexes at intake boundaries.
- Changed the accepted ledger source to `form` for commissioner-approved submissions; system defaults remain `system`.
- Extended `append_event` to validate and persist Form provenance. Current-contract views and report ledger data expose the added fields.
- Added queue deduplication by Form UUID and same-deal-within-24-hours checks.
- Added a Form-event test for source, ref, acquisition type, and timestamp persistence.

### Commishbot

- Replaced free-text parsing with strict two-line signing and cap-trade relay parsing. Signing relays resolve player/team IDs and carry `drafted`, `called_up`, or `waiver`; cap trades retain sender/receiver order and fractional years.
- Added shared catch-up/client helpers and the `python -m sda.discord_bot.commish` entry point. Top-level commands are `/approve`, `/reject`, and `/check`.
- Webhook-authored messages in the configured channel are accepted; non-relay messages are ignored. Confirmation cards show rule results and cap changes. Approval writes `source='form'` ledger events and rebuilds reports.
- `called_up` approvals write `CALLED_UP`; other signing approvals write `SIGNED`. Original submission time is retained for the one-day rule.

### Forms Apps Script

Added `sda/forms/` with:

- Idempotent signing, cap-trade, and draft form builders.
- Pure validation for exact/alias player matching, acquisition-specific contract bounds, and cap room.
- Cached, fail-closed fetches of the published player/cap feeds.
- Webhook relay formatting with UTC submission time and UUID ref. Webhook URL is read only from Script Properties; `DRY_RUN` avoids posting.
- Submit triggers that write response status/reason, reject by email, relay accepted submissions, and generate one relay/ref per draft pick.
- Draft duplicate-team protection, test runner, deployment README, and deferred-feature note.

### Reports and pipeline

- Contracts and cap are one page: each team/season cell shows committed/remaining, expandable multi-year and one-year contracts, dropped-player dead cap details, cap-trade events, and IL players. Current IL names use the saved snapshot; future IL is marked unprojected. The minors section has one expandable roster per team listing names and positions; age-by-year is deferred until bio coverage is populated. The standalone Cap Tracker page is retired.
- Current IL and minors detail is read from the saved raw Fantrax roster snapshot, with the DB roster snapshot as fallback. This allows team minors lists to render when the database has no dated roster rows.
- Every report build writes four machine-readable files under `site/api/`: `cap_state.json`, `players.json`, `recent_adds.json`, and `draft_results.json`. Empty inputs produce valid empty JSON values.
- Free-agent projection is one sortable/filterable table with player/team/position/age/FA year, hitter and pitcher stat columns, and latest Fantrax ADP. Redundant Signed Through and Deal columns are removed. Completed-season stats can be supplied with `--player-stats-csv` and `--stats-season`; joins use Fantrax player IDs. ADP is separate from historical stats and labeled with snapshot date.
- Implemented the pipeline stages for cached/live pull, roster diffs, validation/defaults, history, report/API build, DB text snapshot, GitHub Pages copy to repository-root `docs/`, and optional alerts.
- Dry-run uses a temporary database and temporary site/pages directories. It does not pull the network, mutate the live DB, send alerts, or stage Git files.
- Publishing requires a clean worktree on `main`; the pipeline skips a same-day run when input snapshot hashes match.
- Updated README and added `OPERATIONS.md` and `LEAGUE_CALENDAR.md` for Forms, bot, pipeline, and launchd operation.

### Report restructure (latest session)

- Navigation is now: **Contract Cap Tracker** (`index.html`, template `grid.html`, report key `grid`), **Free Agency Forecast** (`free_agents.html`), **Team Roster Projections** (`roster_projections.html`, new), Waivers, Standings, History. Page titles match the tab names; `REPORT_PAGES` in `sda/reports/build.py` and `templates/base.html` hold the labels. Internal keys/filenames were intentionally left unchanged.
- Team Roster Projections: `report_roster_projections()` builds one grid per team (rows = players, columns = the seven seasons from the build season). Rows are grouped by primary position (C, 1B, 2B, 3B, SS, OF, UT, SP, RP; LF/CF/RF→OF, DH→UT) and sorted longest contract first, then name. A season is highlighted when it is before the player's FA year (e.g. FA 2030 from 2027 highlights 2027–2029, matching cap-tracker counting). Contracts with FA year <= build season are omitted.
- Unsigned minor-league players (from the Fantrax minors snapshot, not already contracted) are listed after signed players at their position with a "Minors · unsigned" tag and no highlights. Each year cell carries a `projected` flag (always False now) with a hatched CSS style (`.year-cell.projected`) reserved for future minor-league projection logic.
- Commissioner confirmed (2026-10-09): highlighting years under contract (seasons before FA year) is the desired behavior; no change needed.
- Possible next steps: implement minors projections, add position/team filters to the roster page, and implement the V14 promotion validation and daily scheduling noted below. `site/` is gitignored; regenerate with `python -m sda.reports --season 2027` (use the repo-root `.venv`).

## Verification

- Python suite: **107 passed** (includes the new roster-projection test).
- Latest dry-run after the placeholder-minors ruling: **554/554** player rows matched; **0 exceptions**; **0 cap errors**; **179 planned events**; `can_commit=True`.
- Commissioner resolutions are applied for the 24 reviewed rows: owner/ID overrides, five dropped exclusions, minor-league no-contract treatment, missing-year policy, and removal of the stale O'Hearn duplicate. Jose Ramirez is explicitly mapped to the 3B/CLE player ID `01ub6`, overriding an older incorrect alias.
- Migration eligibility is strictly `contract_years > 1`; rows at one year or less are excluded whether marked dropped or active. Missing non-minor contract years default to a 2026 one-year deal (FA 2027), so those rows are also excluded. When a player appears on multiple sheets, the current Fantrax roster team is authoritative. This resolves the prior duplicate groups: retain Framber Valdez's two-year Pecora row and omit its one-year duplicate; omit the one-year rows for Gore, Nola, Brooks Lee, and Nick Martinez. O'Hearn resolves to Hoffman; Lugo/Ray are one-year rows and are outside this migration scope.
- Commissioner policy is now: one-year contracts count toward future cap calculations/reporting, but prior-season one-year deals are not migrated; the next seven seasons start at 78 base years per team with no prior-season cap-trade carryover; uncontracted minors have no contract/cap entry, while an official active multi-year contract remains cap-bearing when a player is demoted to minors.
- The commissioner reviewed the five candidate minor deals (Jackson Jobe, Hagen Smith, Cam Caminiti, Gage Wood, Noelvi Marte) and confirmed all tracker terms are placeholders, not official contracts. These five rows now have explicit no-contract overrides; the current migration plans zero minors contract events. If a future review confirms an official multi-year contract for a demoted minor, it is preserved and counted against cap. The commit path refreshes the schema so legacy `current_contracts` views include official minors deals.
- No new Fantrax pull is needed for this migration: the commissioner confirmed the saved end-of-season snapshot is the intended roster source.
- The event-by-event migration review file is `data/migration_event_preview_2026-10-06.csv` (179 planned events). No source CSVs or production ledger events have been changed.
- Free-agent page was rebuilt and visually checked at desktop/mobile sizes; search and FA-year filters were exercised. Current saved data has no completed-season stats CSV and the saved ADP snapshot is empty, so those cells render as unavailable; the page clearly labels stats as not loaded and ADP vintage as 2026-10-02. README documents the Fantrax CSV command.
- **Daily promotion validation:** `roster_snapshots` already stores dated MLB/minors roster levels, and the pipeline computes roster-level changes. The new V14 check (minors → MLB/IL with no active official contract) is not implemented yet. The prompt now specifies a configurable annual per-team unsigned-promotion allowance, default 0; allowed moves consume quota, while V12 still applies its one-year default after the normal window. Daily snapshots can detect and report moves but cannot prevent Fantrax from making them; a missed snapshot can hide transitions during the gap.
- The production ledger still contains **0 events**; no migration commit has been performed.
- Apps Script pure validation and exact relay-format tests passed under macOS JavaScriptCore (`osascript -l JavaScript`).
- `python -m sda.discord_bot.commish --help` and `python -m sda.pipeline --help` work.
- `git diff --check` passed at the last verification.
- Tests cover strict parsing, malformed relays, queue-without-ledger, V1 failures, ref/deal dedupe, approval provenance, cap-trade event pairs, report JSON feeds, and pipeline dry-run/idempotency.

## Before Live Use

1. **Existing DuckDB source constraint:** the previous `contract_events` CHECK constraint only allowed `discord`, while new inserts use `form`. `CREATE TABLE IF NOT EXISTS` does not replace a constraint on an existing table. No production DB exists, but any previously initialized local DB must be rebuilt from a fresh schema or migrated before Form approvals can write.
2. **Apps Script deployment:** create the Apps Script project with `clasp`, configure Script Properties (`DISCORD_WEBHOOK_URL`; optionally `DRY_RUN=true`), build the forms, and install triggers. None of these external services have been deployed or authorized here.
3. **Secrets:** `DISCORD_BOT_TOKEN` and `SDA_ALERT_WEBHOOK_URL` must be provided locally. The pipeline currently reads the alert webhook from the process environment; a launchd job will need that variable explicitly set or `.env` loading added before scheduled alerts are expected to work.
4. **GitHub Pages:** enable Pages from the repository-root `/docs` directory. The site copy and pipeline path target that deployment location; no Pages settings were changed.
5. **Forms data availability:** run the pipeline after usable Fantrax snapshots and the initialized DB exist. Apps Script intentionally rejects submissions when required published JSON data cannot be fetched.

## Worktree Notes

The prompt outline and handoff record the confirmed cap/minors decisions and V14 design. Migration code, tests, config, and the readable manual worksheet remain local changes; V14 validation and daily scheduling/alert verification remain outstanding. The saved season-end roster snapshot was intentionally reused without a refresh. Player aliases, team map, database, and generated snapshots are local data. Unrelated `.DS_Store` and `__pycache__` artifacts remain untracked. No production migration has been committed.
