# Second Deck Association Pipeline

The SDA project is a Python 3.11+ data pipeline for the Second Deck Association dynasty fantasy baseball league. It will collect league data from Fantrax, maintain contracts in DuckDB as an append-only event ledger, validate league rules, publish static HTML reports, and support commissioner approval of contract announcements through Discord.

## Layout

- `sda/fantrax/`: Fantrax API client and league data ingestion.
- `sda/db/`: DuckDB schema, models, and append-only contract ledger.
- `sda/validation/`: deterministic league rule engine.
- `sda/reports/`: static HTML report generation.
- `sda/discord_bot/`: contract announcement monitoring and approval workflow.
- `sda/pipeline/`: nightly orchestration and command-line entry point.
- `config/season.yaml`: season dates and league configuration.
- `tests/fixtures/`: reusable test data.
- `data/`: local raw snapshots and DuckDB database; ignored by Git.

The database initializes with `python -m sda.db init`; `python -m sda.db snapshot` exports a date-stamped CSV snapshot. Raw API data and the live DuckDB file stay ignored, while `data/snapshots/` CSV exports are intentionally trackable. Contract events are append-only; cap-trade rows use signed `years` values and no player ID, and dropped-deal charges follow the configured league penalty schedule.

## Build order

Implement the project in the order defined by Prompts 0 through 7: 0) scaffolding and shared conventions, 1) Fantrax client, 2) data model and ledger, 3) Sheets migration, 4) validation engine, 5) HTML reports, 6) Discord bot, and 7) pipeline website and nightly orchestration.

## Development

Install dependencies with `pip install -r requirements.txt`, run tests with `pytest`, and inspect pipeline options with `python -m sda.pipeline --help`. The pipeline currently provides a stub CLI and a `--dry-run` option.

The one-time Sheets backfill is previewed with `python -m sda.db.migrate`; it requires CSV exports for the ten team tabs only. Salary Cap Tracking, Estimated Free Agent Class, and League History are intentionally excluded. The reconciliation checks migrated contracts against the 78-year cap plus Fantrax IL-slot relief. Resolve all row exceptions and cap errors before using `--commit`. If Fantrax team names/managers do not match the tab names exactly, supply a JSON tab-to-team-ID file with `--team-map`.

Season history is updated from the latest saved standings with `python -m sda.db history-update --season 2027` and finalized with `python -m sda.db history-finalize`. Minor-leaguer bios are refreshed from the latest roster snapshot with `python -m sda.mlb refresh --minors-only`; exact MLB Stats API name matches are required, and unmatched bio fields remain null.

Run deterministic contract and roster checks with `python -m sda.validation --season 2027`. The engine reads the latest DuckDB roster snapshot, reports per-team rule results, and exits nonzero when a rule fails. After a roster add has remained unannounced for 24 hours, it appends one `DEFAULTED_1YR` event and reports the action. Parsed Discord announcements belong in the `announcements` table with their original timestamp. Real-life IL eligibility is stored separately from the Fantrax roster-slot status; until an injury-status feed populates `players.real_life_il`, unknown IL eligibility is reported for commissioner review rather than inferred.

Build the static report site with `python -m sda.reports --season 2027`. It writes six league reports and three audit views to the ignored `site/` directory. The builder uses saved Fantrax snapshots when available, and accepts `--waiver-csv` / `--transactions-csv` exports for those feeds; it never loads `.env` or embeds credentials.