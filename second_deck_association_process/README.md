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

## Build order

Implement the project in the order defined by Prompts 0 through 7: 0) scaffolding and shared conventions, 1) Fantrax client, 2) data model and ledger, 3) Sheets migration, 4) validation engine, 5) HTML reports, 6) Discord bot, and 7) pipeline website and nightly orchestration.

## Development

Install dependencies with `pip install -r requirements.txt`, run tests with `pytest`, and inspect pipeline options with `python -m sda.pipeline --help`. The pipeline currently provides a stub CLI and a `--dry-run` option.