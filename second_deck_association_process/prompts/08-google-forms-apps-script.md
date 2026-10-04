# PROMPT 8 — Google Forms input system (Apps Script)

Copy everything below into Claude Code. This is the **sole input process** for
long-term signings and cap trades (decided 2026-10-04): managers fill out
Google Forms, never post announcements by hand. The Apps Script validates each
submission and relays accepted ones to the Discord announcements channel via
webhook, where the Prompt-6 commishbot picks them up. Build inside
`second_deck_association_process/sda/forms/`.

---

## Goal

An Apps Script project, version-controlled in this repo and deployed with
`clasp`, that:

1. **Builds** three Google Forms from code (idempotent — re-running updates, never duplicates):
   - **Signing form** — long-term contract announcements.
   - **Cap-trade form** — salary-cap space trades.
   - **Draft form** — per-team pre-filled links generated after the slow draft (one years-input per pick).
2. **Validates** every submission server-side before relaying it.
3. **Relays** accepted submissions to Discord in the exact formats Prompt 6 parses.

## Platform constraint (design around this)

Google Forms `onFormSubmit` triggers fire **after** the response is recorded —
a submission cannot be blocked with a friendly inline error. The pattern is:
accept the response → validate in the trigger → on failure, mark the response
row `REJECTED` with a reason, **do not relay**, and email the respondent
explaining the fix (Forms must collect respondent email). Native Forms
"response validation" (number ranges, required fields) handles the trivial
bounds checks pre-submit; the script handles name matching and cap room.

## Project layout (`sda/forms/`)

- `appsscript.json` — manifest: V8 runtime, timeZone (commissioner's), oauthScopes for Forms, Spreadsheets, external fetch, script storage, and Gmail/mail send.
- `.clasp.json` — **gitignored**; created by `clasp create` in the commissioner's account. Document the clasp setup (`npm i -g @google/clasp`, `clasp login`, `clasp create`, `clasp push`).
- `config.js` — constants: `SITE_BASE_URL` (the GitHub Pages site root), the 10 team names, Discord relay formats (must match Prompt 6 exactly). Webhook URL is **not** here — it lives in Script Properties as `DISCORD_WEBHOOK_URL`.
- `forms.js` — `buildSigningForm()`, `buildCapTradeForm()`, `buildAllForms()`; idempotent builders.
- `validation.js` — **pure functions** (no Apps Script services): `matchPlayer(name, playersDb)` (exact then alias-map, returns `{fantrax_id, confidence}`), `checkYears(years, acquisitionType)` (drafted/called_up 1–7; waiver 1–3), `checkCapRoom(team, years, capState)` (remaining = effective_cap − committed; reject if years > remaining). Fully unit-tested.
- `data.js` — `fetchJson(name)` with `CacheService` (1-hour TTL): pulls `players.json` and `cap_state.json` from `SITE_BASE_URL/api/`. Defensive: on fetch failure, validation fails **closed** (mark `REJECTED`, reason `data-unavailable`, do not relay).
- `relay.js` — builds the exact Prompt-6 relay strings (signing + cap trade, with `submitted` ISO-8601 UTC timestamp and a generated `ref` UUID) and POSTs to the webhook URL from Script Properties. Never logs the URL.
- `draft.js` — `generateDraftFormLinks()`: reads `draft_results.json` from the site, builds one pre-filled signing-form URL per team (`?team=<name>` prefill; one number item per pick, native 1–7 response validation), logs the 10 URLs for the commissioner to distribute. One submission per team.
- `triggers.js` — `installSubmitTriggers()` / `onSigningSubmit(e)` / `onCapTradeSubmit(e)` / `onDraftSubmit(e)`: validate → write `status` (`ACCEPTED`/`REJECTED` + reason) to the response sheet → relay if accepted → email respondent on rejection.
- `tests.js` — minimal assertion runner (`assertEq`/`assertThrows`) exercising the pure validation functions; runnable via the editor or `clasp run`. No pytest here — document how to run.
- `README.md` — commissioner deploy checklist (see below).

## Form definitions

**Signing form** fields: team (dropdown, 10 teams, required) · player name (text, required) · contract years (number, required, native response validation: integer 1–7) · acquisition type (dropdown: drafted / called_up / waiver — drives the 1–7 vs 1–3 bound; required) · notes (paragraph, optional). Collect respondent email.

**Cap-trade form** fields: sending team (dropdown, required) · receiving team (dropdown, required; must differ) · years (number, required, native validation: > 0, decimals allowed) · notes (optional). Collect respondent email. No cap-room check on the sender (always legal); the commishbot re-validates team identities.

**Draft form** (generated post-draft): team pre-filled; one required number item per pick ("Pick N: <player> — years", native 1–7 validation). Single submission per team; duplicates flagged by matching team name.

**Response sheets:** one per form (auto-created), with appended columns `status`, `reject_reason`, `relayed_at`, `discord_ref`.

## Phase-2 stub (do NOT build the trigger now)

Document — but do not implement or install — `refreshRecentAdds()`: a time-driven function that would rewrite a "recently added players" dropdown from `recent_adds.json`. Deferred until the pipeline publishes that file. Leave a clearly-marked placeholder and a comment pointing at `forms-deferred-enhancements.md` in the project docs.

## Commissioner deploy checklist (write this into the README)

1. `npm i -g @google/clasp` → `clasp login` (commissioner's Google account).
2. `cd second_deck_association_process/sda/forms` → `clasp create --title "SDA Forms"` → `clasp push`.
3. In the Apps Script editor: Script Properties → set `DISCORD_WEBHOOK_URL` (from the Discord channel's Integrations → Webhooks).
4. Run `buildAllForms()` once; authorize when prompted.
5. Run `installSubmitTriggers()`.
6. Distribute the two form links to managers; set the announcements channel read-only for managers.

## Acceptance criteria

- `buildAllForms()` run twice → exactly one signing form, one cap-trade form (idempotent; no duplicates).
- Validation unit tests: exact name match; alias match (nickname → player); unknown name → no match; year bounds per acquisition type (drafted 1–7, waiver 1–3, 0 and 8 rejected); cap check (years ≤ remaining passes, years > remaining fails with the numbers in the reason); fetch failure → fail-closed.
- Relay format test: generated strings parse under Prompt 6's strict parser (paste the expected outputs into the test).
- Dry-run: a `DRY_RUN` flag validates and logs without POSTing to Discord.
- The webhook URL appears nowhere in code, logs, or the repo (test asserts the relay reads it only from Script Properties).
- README's deploy checklist is complete enough to follow cold.
