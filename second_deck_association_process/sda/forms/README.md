# SDA Google Forms Intake

Google Forms are the sole manager input for long-term signings and cap trades. The Apps Script validates submissions, records acceptance/rejection metadata in the response sheets, and relays accepted records to the Discord webhook. The webhook URL is stored only in Apps Script Script Properties.

## Deploy

1. Install and authenticate clasp: `npm i -g @google/clasp`, then `clasp login`.
2. From this directory, run `clasp create --title "SDA Forms"` and `clasp push`. Keep the generated `.clasp.json` private and out of Git.
3. In Script Properties, set `DISCORD_WEBHOOK_URL` from the announcements channel's Discord Integrations settings. Never add the URL to source or logs.
4. Run `buildAllForms()` once and grant the requested Forms, Sheets, external-request, and email permissions. Re-running reuses the stored form IDs.
5. Run `installSubmitTriggers()` once.
6. Distribute the signing, cap-trade, and team-specific draft links. Keep the Discord announcements channel read-only for managers; Apps Script is the intake writer.
7. Run `runTests()` from the Apps Script editor to execute the pure validation assertions.

Set Script Property `DRY_RUN` to `true` to validate and log relay messages without POSTing. The webhook URL is never logged.

`refreshRecentAdds()` is intentionally not installed. It is deferred until the pipeline's `recent_adds.json` feed is available; see `forms-deferred-enhancements.md`.