# Second Deck Association Pipeline

The SDA project is a Python 3.11+ data pipeline for the Second Deck Association dynasty fantasy baseball league. It collects league data from Fantrax, maintains contracts in DuckDB as an append-only event ledger, validates league rules, publishes static HTML reports and Forms data, and uses Google Forms as the only manager intake for long-term signings and cap trades.

## Layout

- `sda/fantrax/`: Fantrax API client and league data ingestion.
- `sda/db/`: DuckDB schema, models, and append-only contract ledger.
- `sda/validation/`: deterministic league rule engine.
- `sda/reports/`: static HTML report generation.
- `sda/discord_bot/`: contract announcement monitoring and approval workflow.
- `sda/forms/`: Google Apps Script Forms, validation, and Discord webhook relay.
- `sda/pipeline/`: nightly orchestration and command-line entry point.
- `config/season.yaml`: season dates and league configuration.
- `tests/fixtures/`: reusable test data.
- `data/`: local raw snapshots and DuckDB database; ignored by Git.

The database initializes with `python -m sda.db init`; `python -m sda.db snapshot` exports a date-stamped CSV snapshot. Raw API data and the live DuckDB file stay ignored, while `data/snapshots/` CSV exports are intentionally trackable. Contract events are append-only; cap-trade rows use signed `years` values and no player ID, and dropped-deal charges follow the configured league penalty schedule.

## Build order

Implement the project in the order defined by Prompts 0 through 8: 0) scaffolding, 1) Fantrax client, 2) data model, 3) Sheets migration, 4) validation, 5) reports, 6) Form-relay commishbot, 7) pipeline/website/Forms API, and 8) Google Forms Apps Script.

## Development

Install dependencies with `pip install -r requirements.txt`, run tests with `pytest`, and inspect pipeline options with `python -m sda.pipeline --help`. Use `python -m sda.pipeline --season 2027 --dry-run` to exercise cached inputs without writing to the live database, Pages output, or Git history.

The one-time Sheets backfill is previewed with `python -m sda.db.migrate`; it requires CSV exports for the ten team tabs only. Salary Cap Tracking, Estimated Free Agent Class, and League History are intentionally excluded. The reconciliation checks migrated contracts against the 78-year cap plus Fantrax IL-slot relief. Resolve all row exceptions and cap errors before using `--commit`. If Fantrax team names/managers do not match the tab names exactly, supply a JSON tab-to-team-ID file with `--team-map`.

Season history is updated from the latest saved standings with `python -m sda.db history-update --season 2027` and finalized with `python -m sda.db history-finalize`. Minor-leaguer bios are refreshed from the latest roster snapshot with `python -m sda.mlb refresh --minors-only`; exact MLB Stats API name matches are required, and unmatched bio fields remain null.

Run deterministic contract and roster checks with `python -m sda.validation --season 2027`. The engine reads the latest DuckDB roster snapshot, reports per-team rule results, and exits nonzero when a rule fails. After a roster add has remained unannounced for 24 hours, it appends one `DEFAULTED_1YR` event and reports the action. Form-originated contract events retain their relay UUID, acquisition type, and submit timestamp. Real-life IL eligibility is stored separately from Fantrax roster-slot status; unknown eligibility is reported for commissioner review rather than inferred.

Build the static report site with `python -m sda.reports --season 2027`. It writes six league reports, three audit views, and `site/api/{cap_state,players,recent_adds,draft_results}.json`. The builder uses saved Fantrax snapshots when available, and accepts `--waiver-csv` / `--transactions-csv` exports for those feeds; it never loads `.env` or embeds credentials.

## Forms and Commishbot

Google Forms is the sole manager input. Apps Script validates submissions and relays accepted ones to the existing announcements channel in strict two-line formats. Managers must not hand-post signing or cap-trade messages there. The commishbot ignores messages that do not match a relay exactly.

Install Apps Script tooling with `npm i -g @google/clasp`, authenticate with `clasp login`, then run `clasp create --title "SDA Forms"` and `clasp push` from `sda/forms/`. In Apps Script Script Properties, set `DISCORD_WEBHOOK_URL`; it must never be stored in this repository. Run `buildAllForms()` and `installSubmitTriggers()`, then distribute the form URLs and generated draft links. See [Forms deployment instructions](sda/forms/README.md).

Set `DISCORD_BOT_TOKEN` in the local `.env` file and enable Message Content Intent. Initialize the DB and Fantrax player/team data before startup. The commishbot supports ✅ / ❌ and `/approve`, `/reject`, `/check`. Test strict relay validation without Discord or writes:

```bash
python -m sda.discord_bot.commish --dry-run --message $'📝 SIGNING: Shohei Ohtani — 4 years — Alpha Owls\ntype: waiver · submitted: 2027-05-01T12:00:00Z · by: Alpha Owls · ref: 123e4567-e89b-12d3-a456-426614174000'
```

The laptop hosts the bot and pipeline; they are offline while it is off. Install a LaunchAgent as `~/Library/LaunchAgents/com.sda.commishbot.plist`, replacing `USERNAME` with the macOS account name:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>Label</key><string>com.sda.commishbot</string>
	<key>ProgramArguments</key>
	<array>
		<string>/Users/USERNAME/Development/repos/magicmatt_fantasysports/.venv/bin/python</string>
		<string>-m</string><string>sda.discord_bot.commish</string>
	</array>
	<key>WorkingDirectory</key><string>/Users/USERNAME/Development/repos/magicmatt_fantasysports/second_deck_association_process</string>
	<key>RunAtLoad</key><true/>
	<key>KeepAlive</key><true/>
	<key>StandardOutPath</key><string>/Users/USERNAME/Development/repos/magicmatt_fantasysports/second_deck_association_process/data/commishbot.log</string>
	<key>StandardErrorPath</key><string>/Users/USERNAME/Development/repos/magicmatt_fantasysports/second_deck_association_process/data/commishbot-error.log</string>
</dict>
</plist>
```

Install/start with `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.sda.commishbot.plist`; stop/uninstall with `launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.sda.commishbot.plist`. The bot resumes from the last processed Discord message ID and writes DB text snapshots after changes. The pipeline publishes the generated site to the repository-root `docs/` directory for GitHub Pages and pushes only when the worktree is clean.

For daily operation and issue handling, see [OPERATIONS.md](OPERATIONS.md) and [LEAGUE_CALENDAR.md](LEAGUE_CALENDAR.md).