# Prompt Implementation Handoff

**Status date:** 2026-10-06
**Branch:** `main`, based on `origin/main` at `9858224` before the migration updates in this handoff
**Implementation state:** Migration matching/rule updates, migration tests, and the manual-resolution worksheet are committed on `main`. The production migration remains blocked; no contract events have been committed, and no live Discord connection, Apps Script deployment, or pipeline Git publish has been run.

## Summary

Implemented the current Forms → Discord webhook → commishbot intake model and its supporting report/pipeline data contract. Google Forms are the only manager input for long-term signings and cap trades. The Apps Script relay emits strict two-line messages with a UUID `ref`, acquisition type where applicable, and UTC submission timestamp. The commishbot parses those relays, validates proposals, queues them for commissioner approval, and records approved events with Form provenance.

The one-time contract migration has been extended with conservative fuzzy player matching scoped first to the current Fantrax roster of the mapped owner/team, before a global fuzzy fallback. It also supports the commissioner-confirmed 2026 default drop year, derives FA year from year added plus contract duration, treats blank Move Type as waiver only for 1–3 year contracts, and flags active unrostered contracts over three years. A manual-resolution worksheet is available at `data/manual_contract_resolution.md`.

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

- Every report build writes four machine-readable files under `site/api/`: `cap_state.json`, `players.json`, `recent_adds.json`, and `draft_results.json`. Empty inputs produce valid empty JSON values.
- Implemented the pipeline stages for cached/live pull, roster diffs, validation/defaults, history, report/API build, DB text snapshot, GitHub Pages copy to repository-root `docs/`, and optional alerts.
- Dry-run uses a temporary database and temporary site/pages directories. It does not pull the network, mutate the live DB, send alerts, or stage Git files.
- Publishing requires a clean worktree on `main`; the pipeline skips a same-day run when input snapshot hashes match.
- Updated README and added `OPERATIONS.md` and `LEAGUE_CALENDAR.md` for Forms, bot, pipeline, and launchd operation.

## Verification

- Python suite: **92 passed** after the migration matching updates.
- Latest migration dry-run (with local team/alias mappings and the external replacement CSVs): **534/554** player rows matched; **24 exceptions**; **0 cap errors**; **423 planned events**; commit gate remains blocked. Exceptions include unresolved player aliases, two invalid contract-year rows, one blank Move Type over three years, and one active contract over three years absent from the current roster.
- The review worksheet records the 24 exception rows and the information needed to resolve each. Leodalis De Vries is noted as dropped; this note has not yet been incorporated into a clean reconciliation.
- The dry-run did not commit events. No production contract-event migration has been performed.
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

Migration code and tests are tracked source changes. `data/manual_contract_resolution.md` is normally excluded by `data/*` and must be explicitly included for this handoff. Player aliases, the team map, database, and generated snapshot CSVs are local data under the same ignore rule and are not part of the source publication. Unrelated `.DS_Store` and `__pycache__` artifacts remain untracked and are not included. The migration is not ready to write to the production ledger until all exceptions are resolved and a fresh dry-run opens the gate.
