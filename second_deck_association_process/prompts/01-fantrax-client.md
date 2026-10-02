# PROMPT 1 — Fantrax API client

Copy everything below into Claude Code. Run this after Prompt 0. Build inside `second_deck_association_process/sda/fantrax/`.

---

Build a Python client for **Fantrax's official API** (docs: https://www.fantrax.com/developer, "API Documentation v1.8 (Beta)").

## Key facts (verified)

- Base URL: `https://www.fantrax.com/fxea/general/`
- REST/JSON. Requests work as **query strings** (e.g. `GET {base}/getDraftResults?leagueId=4fyzhujxmk7scnaf`) — I have verified this returns JSON with no authentication.
- **No API key needed.** The league ID (`4fyzhujxmk7scnaf`, from `config/season.yaml`) is the only credential for every endpoint below.
- The single exception is `getLeagues`, which requires `userSecretId` (found on the Fantrax profile page; lives in `.env` as `FANTRAX_USER_SECRET_ID`). Treat it as optional — everything else works without it.
- Be polite: cache aggressively, retry with backoff on 5xx, and never hammer the API in a loop.

## Endpoints to wrap

| Endpoint | Params | Use |
|---|---|---|
| `getPlayerIds` | — | Master player-ID list. **The identity anchor for the whole system** — every other call references these IDs. |
| `getLeagueInfo` | `leagueId` | Teams, config, scoring periods, playoff config. |
| `getTeamRosters` | `leagueId`, optional `period` | All 10 teams' rosters **including minor-league rosters**. |
| `getStandings` | `leagueId` | Standings. |
| `getMatchupScores` | `leagueId`, optional `period` | Per-category matchup breakdowns (our league is H2H categories: R, HR, RBI, SB, OBP / QS, K, ERA, WHIP, SVH). |
| `getDraftPicks` | `leagueId` | Future draft picks owned (shown in Fantrax as 2027–2036). |
| `getDraftResults` | `leagueId` | Full draft results. Observed shape includes `draftDate`, `startDate`, `endDate`, `draftType` ("snake"), `draftState`, and `draftPicks[]` with `round`, `pick`, `teamId`, `time` (epoch ms), `pickInRound`, `playerId`. |
| `getAdp` | `sport=MLB`, optional `position`, `start`, `limit` | Average draft position data. |
| `getLeagues` | `userSecretId` | Optional. Lists leagues on the account. |

## What to build

`second_deck_association_process/sda/fantrax/`:
- `client.py` — `FantraxClient` class: takes `league_id` (and optional `user_secret_id`), one method per endpoint above, returns plain Python dicts/lists. Handles retries, timeouts, and HTTP errors with clear messages.
- `snapshots.py` — `save_snapshot(name)` / `load_snapshot(name)`: writes each pull as timestamped JSON under `second_deck_association_process/data/raw/` (e.g. `data/raw/2027-03-01/team_rosters.json`). Loading the latest snapshot must work offline.
- `normalize.py` — converts raw API payloads into clean internal structures:
  - `players`: keyed by Fantrax `playerId`; fields: id, name, positions, mlb_team.
  - `rosters`: per team: team_id, team_name, mlb_roster[], minors_roster[], il_slots used.
  - **Name-alias map**: a persistent JSON (`data/player_aliases.json`) mapping alternate spellings/nicknames → Fantrax player ID. This is how Google Sheet rows and Discord announcements (which use nicknames!) get matched to real players. Seed it from `getPlayerIds`; make it appendable.
- CLI: `python -m sda.fantrax.snapshot --all` pulls everything and saves snapshots.

## Notes for later prompts

- Player identity must **always** resolve through the Fantrax player ID, never raw name strings.
- The `time` field on draft picks (epoch ms) is how we compute draft durations.
- If an endpoint's shape differs from the docs, adapt the normalizer and note the difference in a comment — don't crash the whole pull on one odd field.

## Acceptance criteria

- `python -m sda.fantrax.snapshot --all` completes and writes one JSON file per endpoint under `data/raw/<today>/`.
- Re-running is idempotent (overwrites today's files, doesn't duplicate).
- `pytest` includes at least: retry-on-500 test (mocked), alias-map round-trip test, and a normalizer test on a small fixture payload.
