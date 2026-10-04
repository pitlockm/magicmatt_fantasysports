# SDA Operations

## Commishbot

Managers submit long-term signings and cap trades through Google Forms. Do not accept manually typed relay messages in the announcements channel. The Apps Script validates form submissions and posts strict two-line webhook relays; the commishbot validates again, queues them, and waits for commissioner approval.

Start the local bot from `second_deck_association_process/`:

```bash
python -m sda.discord_bot.commish
```

To stop the LaunchAgent:

```bash
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.sda.commishbot.plist
```

To start it again:

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.sda.commishbot.plist
```

The commishbot resumes from its persisted channel watermark. Replayed messages are deduplicated by Discord message ID and form ref; identical proposals submitted within 24 hours are also suppressed. If a confirmation card shows a failed rule, fix the form submission and submit a new response; failed proposals cannot be approved.

## Google Forms

From `sda/forms/`, install `clasp`, authenticate with the commissioner’s Google account, create the Apps Script project, and push the sources. Set `DISCORD_WEBHOOK_URL` only in Apps Script Script Properties. Run `buildAllForms()` and `installSubmitTriggers()`, then distribute signing and cap-trade URLs. After the slow draft, run `generateDraftFormLinks()` and distribute the team-specific URLs. `runTests()` executes the Apps Script pure-function checks.

If a submission is rejected, read the response sheet’s `reject_reason` and correct the form data. A `data-unavailable` rejection means the Apps Script could not retrieve `site/api/players.json` or `site/api/cap_state.json`; rerun the pipeline and retry after Pages has deployed.

## Nightly Pipeline

Preview the cached-input pipeline without changing the live database or Git tree:

```bash
python -m sda.pipeline --season 2027 --dry-run
```

Run it manually with `python -m sda.pipeline --season 2027`. It updates snapshots, applies one-day defaults, updates standings, rebuilds reports and Forms JSON, copies the site to repository-root `docs/`, exports a DB snapshot, and publishes from `main` only when the worktree is clean. The laptop is the server: if it is off at the scheduled time, the run is skipped; the next roster diff and bot history catch-up resume from saved state.

Validation failures do not stop report generation. Review `site/validation.html`, correct the underlying ledger/config/roster issue, then rerun validation and the pipeline. Alerts are sent only when `SDA_ALERT_WEBHOOK_URL` is configured locally.

The pipeline publishes these versioned inputs for Apps Script:

- `site/api/cap_state.json`: season and per-team effective cap, committed years, remaining space.
- `site/api/players.json`: canonical player name-to-ID mapping and alias-to-ID map.
- `site/api/recent_adds.json`: known MLB roster additions from the recent snapshot window.
- `site/api/draft_results.json`: draft picks grouped by Fantrax team ID.

Update the Apps Script consumers and tests whenever one of these shapes changes.

## Secrets

The Discord token is `DISCORD_BOT_TOKEN` in the private `.env`. Rotate it in the Discord Developer Portal, update `.env`, then restart the LaunchAgent. The Discord webhook URL is stored only in Apps Script Script Properties; never put it in this repository. `SDA_ALERT_WEBHOOK_URL`, if used, belongs only in the local `.env`.