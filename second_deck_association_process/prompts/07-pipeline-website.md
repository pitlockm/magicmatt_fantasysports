# PROMPT 7 — Nightly pipeline, operations & website

Copy everything below into Claude Code. Run this last — it wires together Prompts 1–6. Build inside `second_deck_association_process/sda/pipeline/`.

---

## Goal

One command that runs the whole system end to end, on a schedule, with alerting and a published website.

## The nightly run

`python -m sda.pipeline --season 2027` does, in order:

1. **Pull** — `sda.fantrax.snapshot --all` → fresh JSON under `data/raw/<today>/`.
2. **Detect** — diff the new rosters against the previous `roster_snapshots`: new adds, drops, call-ups, MLB↔minors moves. Record the snapshot.
3. **Validate** — `sda.validation` engine over the ledger + fresh snapshot. On any failure: continue the run (don't abort the reports), but flag for alerting.
4. **One-day rule** — V12: new adds without a matching announcement in 1 day → `DEFAULTED_1YR` events (source='system').
5. **History** — `sda.db history-update --season <season>`: upsert the current season's `team_season_history` rows from the fresh standings snapshot (league history accumulates from the API going forward — never from the old Sheet). Season finalization (champion/runner-up/regular-season-first) is a manual commissioner step at year end (`history-finalize`), not part of the nightly run.
6. **Rebuild** — `sda.reports --season 2027` → fresh `site/`.
6b. **Publish forms data** — write machine-readable JSONs into `site/api/` for the Prompt-8 Google Forms system (fetched by its Apps Script; all derived from the DB/snapshots, no secrets):
   - `cap_state.json`: per team — `effective_cap`, `committed_years`, `remaining` (effective − committed), `season`.
   - `players.json`: name → `fantrax_id` plus the alias map (nicknames included) for the form's name validation.
   - `recent_adds.json`: players added to Fantrax MLB rosters in the last 7 days (`fantrax_id`, name, team, add date) — feeds the phase-2 "recently added" dropdown; publish even before the dropdown exists.
   - `draft_results.json`: the most recent slow-draft results (`team_id` → picks in order) — feeds the post-draft pre-filled form links.
   All four must validate as JSON and be present even when empty (empty list/object, never missing).
7. **Snapshot & commit** — export DB text snapshots to `data/snapshots/<today>/`, `git add` snapshots + `site/` (including `site/api/`), commit with message `nightly: <date> (<n> validation failures)`, push.
8. **Alert** — if validation failed OR any `DEFAULTED_1YR` was applied: DM/post to the commissioner via the Discord bot (a simple webhook or bot message; read the channel ID from config).

Idempotency: re-running the same night must not duplicate events, snapshots, or commits. Use content hashes on snapshots and a "already ran today" marker.

## Scheduling (Mac)

Document a `launchd` plist in the README that runs the pipeline nightly (e.g. 3 AM local) and keeps the Discord bot alive. Include install/uninstall commands. The commissioner's laptop is the server — say so plainly, including the implication: if the laptop is off, the run is skipped and the next run catches up (the bot's startup catch-up + snapshot diffing make this safe).

## Website (GitHub Pages)

The `site/` output is a static site — publish it with GitHub Pages (free, no server):

1. Repo Settings → Pages → Deploy from branch → `main`, folder `/second_deck_association_process/site` — wait: GitHub Pages only serves from repo root, `/docs`, or a `gh-pages` branch. So the pipeline should **copy the built site to `<repo>/docs/`** (or push a `gh-pages` branch — pick one, document it, be consistent).
2. Every pipeline push redeploys automatically. Public URL: `https://pitlockm.github.io/magicmatt_fantasysports/`.
3. Custom domain is optional later (`CNAME` file + DNS); don't set it up now.
4. The repo is public so the site is public — fine for a fantasy league. The reports build (Prompt 5) already guarantees no secrets in the output.

Update the pipeline's commit step to include the Pages publish path.

## Docs to write

- `README.md` (repo-root level for the new folder): what the system is, the 9-prompt build order, setup (`pip install`, `.env`, first backfill via Prompt 3), and how to run everything.
- `OPERATIONS.md`: starting/stopping the bot, re-running the pipeline manually, resolving validation failures, rotating the Discord token, and the forms-data contract (`site/api/*.json` schemas — the Prompt-8 Apps Script depends on them; changing a schema means updating the script).
- `LEAGUE_CALENDAR.md`: the season's key dates from `config/season.yaml` in plain English (freeze date, trade deadline, draft window, Opening Day) — this is the commissioner's cheat sheet.

## Acceptance criteria

- Full dry run on the fixture DB: `python -m sda.pipeline --season 2027 --dry-run` executes all 8 steps without writing to the real DB, committing nothing, sending no alerts — but printing exactly what it *would* do.
- Idempotency test: run twice against fixtures; second run produces zero new events and no new commit.
- The four `site/api/*.json` files are valid JSON and present (possibly empty) after the run.
- The docs are complete enough that a new commissioner could operate the system from README + OPERATIONS.md alone.
