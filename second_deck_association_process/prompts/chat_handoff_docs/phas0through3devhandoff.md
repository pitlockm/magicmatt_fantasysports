# Phases 0–3 Development Handoff

**Status date:** 2026-10-02
**Branch:** `main`, synchronized with `origin/main` at `7e8207e`

## Executive Summary

The project has a working scaffold, Fantrax ingestion client, DuckDB ledger, revised history/minor-bio model, and a revised one-time Sheets migration. Prompts 00 and 01 are committed. The Prompt 02 base is committed; its later IL/history/minor-bio extensions and the Prompt 03 migration are local, uncommitted work. The latest full test run passed: **28 tests**.

No production DuckDB file was created or modified. The real migration has not been committed or run against a production database.

## Phase Status

### Phase 0: Scaffold

Implemented the Python package layout, config, environment template, ignore rules, dependencies, README, and pipeline help stub. Committed with the Prompt 01 work in `8c3caba`.

### Phase 1: Fantrax Client

Implemented endpoint wrappers, request retries/timeouts, pacing and response caching, dated raw snapshots, player/roster normalization, persistent aliases, and `python -m sda.fantrax.snapshot --all`. Verified against the public league API; the live payload identified 10 teams and 10,711 players. Committed in `8c3caba`.

### Phase 2: Data Model

The committed base provides the DuckDB schema, append-only contract ledger, writer lock, cap calculations, roster snapshots, CSV export/restore, and `python -m sda.db` initialization/snapshot commands (`6f57a15`). Local extensions add:

- Slot-based IL classification and cross-check tests.
- `team_season_history`, `team_all_time_history`, and team-ID-based `league_history`.
- Minor bio columns and a cached, paced MLB Stats API client for exact-name identity, birthdate, career AB/IP, and minors-only refresh.
- History update/finalize commands.

These extensions have targeted coverage; the local complete test run passed.

### Phase 3: Sheets Migration

Implemented locally in `sda/db/migrate.py`. It requires only the ten team CSVs; resolves players through aliases; reconstructs sign/drop events; reports unmatched players, roster orphans, IL discrepancies, minors rows, and cap usage; applies the 78-year base plus Fantrax IL relief; and gates an idempotent batch commit. It does not import historical cap trades or League History.

The configured source folder currently contains all 10 required team CSVs. Their header is on row 4, and the contract duration field is `Player Contract Years*`. No current rows have `Dropped=Yes`; the available export does not show explicit drop-year/penalty columns, so such rows will remain exceptions unless those values are provided.

## Notable Plan Changes

- **IL means the Fantrax fantasy slot.** The verified `status` enum is the manager’s placement: `INJURED_RESERVE` is IL, `MINORS` is minors, and `ACTIVE`/`RESERVE` are MLB. `position` is never used as a slot. Minors take precedence; only Fantrax IL slots provide relief, up to the eight-slot league maximum.
- **Free-agent year convention was clarified:** `fa_year = signing season + contract years`.
- **League history is built forward from Fantrax standings**, not migrated from the old sheet. The schema now stores season/team records and computes all-time summaries.
- **Minor-leaguer bios are new:** birthdate, MLBAM ID, career AB/IP, and refresh timestamp come from the no-key MLB Stats API; exact-name ambiguity is not guessed.
- **Migration scope was narrowed:** only the ten team tabs are required. Salary Cap Tracking, Estimated Free Agent Class, and League History are excluded; cap starts at 78 per team plus Fantrax IL relief, and no historical cap trades are imported.

## Verification and Current Worktree

- Full suite: `28 passed`.
- Migration tests cover missing team tabs, DFA-to-minors, drop reconstruction, batch idempotency, ignored legacy tabs, and cap/IL limits.
- Source discovery found all 10 required CSVs. The reconciliation itself has not been run against the default DB because no production DB has been initialized and team-tab-to-Fantrax team mapping may require `--team-map`.
- `main` has no local commits ahead of `origin/main`. Phase 2 extensions, migration code/tests, and README changes remain uncommitted. Unrelated `.DS_Store` and `__pycache__` artifacts are also present and should be left alone.

## Next Steps

1. Confirm or provide an exact team-tab-to-Fantrax-team-ID mapping if stored manager/team names do not match the ten tab labels.
2. Initialize a local database from the Fantrax snapshots, run the migration dry run, and review unmatched aliases, orphans, IL discrepancies, drop exceptions, and cap errors.
3. Resolve the review items, then run `python -m sda.db.migrate --commit` only after the report is clean.
4. Commit the Phase 2 extensions and revised Phase 3 code after review.
5. Continue with Prompt 04 validation; Prompts 04–07 have not been built yet.