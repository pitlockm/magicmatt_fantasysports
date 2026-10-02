# PATCH — History schema (from scratch) + minor-leaguer bio fields

Copy everything below into Claude Code. Run against the implemented `sda/db/` and `sda/fantrax/` code. Build inside `second_deck_association_process/`.

---

## Context

Two commissioner decisions change the data model. Neither is migrated from the old Google Sheet — both are built from scratch:

1. **League history accumulates from the Fantrax API going forward** (not from the tracker's League History tab). Week-to-week standings snapshots feed per-season rows; the season is finalized at year end.
2. **Minor-leaguer bios**: every rostered minor leaguer should carry age + career MLB AB/IP so managers can project MLB arrival (feeds the Prompt-5 report). Source: the free MLB Stats API (`https://statsapi.mlb.com/api/v1/`, no key needed).

No production data exists yet, so schema changes may be applied with idempotent `CREATE TABLE IF NOT EXISTS` / `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` — no data-preserving migrations needed.

## A. History tables

Keep the existing tables. Add:

```sql
CREATE TABLE IF NOT EXISTS team_season_history (
  season              INTEGER NOT NULL,
  team_id             VARCHAR NOT NULL REFERENCES teams(team_id),
  w                   INTEGER NOT NULL DEFAULT 0,
  l                   INTEGER NOT NULL DEFAULT 0,
  t                   INTEGER NOT NULL DEFAULT 0,
  regular_season_rank INTEGER,
  made_playoffs       BOOLEAN,
  playoff_finish      VARCHAR,   -- champion | runner_up | semifinal | quarterfinal | NULL
  PRIMARY KEY (season, team_id)
);

CREATE OR REPLACE VIEW team_all_time_history AS
SELECT
  team_id,
  COUNT(*) AS seasons_played,
  SUM(w) AS all_time_w, SUM(l) AS all_time_l, SUM(t) AS all_time_t,
  SUM(CASE WHEN playoff_finish = 'champion' THEN 1 ELSE 0 END) AS championships,
  SUM(CASE WHEN playoff_finish = 'runner_up' THEN 1 ELSE 0 END) AS runner_ups,
  SUM(CASE WHEN regular_season_rank = 1 THEN 1 ELSE 0 END) AS regular_season_firsts,
  SUM(CASE WHEN made_playoffs THEN 1 ELSE 0 END) AS playoff_appearances
FROM team_season_history
GROUP BY team_id;
```

Reshape `league_history` to reference teams by ID (drop the old name-VARCHAR columns; nothing is stored yet):
`season PK, champion_team_id → teams, runner_up_team_id → teams, regular_season_first_team_id → teams, prize_champion, prize_runner_up, notes`.
If `init` was already run locally with the old shape, drop and recreate the table — no production data exists, the old shape was never populated.

## B. `sda/db/history.py` (new)

- `update_current_season(season, standings_payload, database_path)` — upsert one `team_season_history` row per team from a `getStandings` payload (normalize with the existing Fantrax normalizer patterns; reuse `seed_teams`-style team matching). Weekly snapshots on disk (`data/raw/<date>/standings.json`) are the audit trail — no weekly staging table needed.
- `finalize_season(season, champion_team_id, runner_up_team_id, regular_season_first_team_id, database_path, prizes...)` — writes `league_history` + sets `playoff_finish` on the team rows.
- CLI: `python -m sda.db history-update --season 2027` (uses latest standings snapshot) and `python -m sda.db history-finalize --season 2027 --champion <team_id> --runner-up <team_id> --regular-season-first <team_id>`. Playoff champion derivation from `getMatchupScores` playoff periods: attempt it, but if the shape is ambiguous leave champion NULL and require the commissioner flags — never guess a champion.

## C. Minor-leaguer bio fields

`ALTER TABLE players ADD COLUMN IF NOT EXISTS`:
`birthdate DATE, career_ab INTEGER, career_ip DOUBLE, mlbam_id INTEGER, bio_refreshed_at TIMESTAMP`.

New `sda/mlb/client.py` (MLB Stats API, polite: cache + pacing like the Fantrax client):
- `find_mlbam_id(name)` — search endpoint, exact normalized-name match only; ambiguous/no match → return None, never guess.
- `get_bio(mlbam_id)` → birthdate; `get_career_stats(mlbam_id)` → career MLB AB (hitters) / IP (pitchers).
- `python -m sda.mlb refresh --minors-only` — fills bio fields for rostered minor leaguers (from latest `roster_snapshots`), stamping `bio_refreshed_at`. Unknown stays NULL — the report shows "—".

## Tests

- History: upsert two weekly standings payloads → per-team rows accumulate; finalize → `team_all_time_history` counts correct; ambiguous playoff shape → champion stays NULL.
- MLB client (mocked HTTP): exact name match resolves; ambiguous names return None; bio refresh stamps `bio_refreshed_at` and leaves unknown fields NULL.

## Acceptance criteria

- Fresh `python -m sda.db init` builds the new tables/columns/view; re-run is idempotent.
- `history-update` + `history-finalize` round-trip on fixtures produces the expected all-time view.
- CSV snapshot export/restore (existing `snapshots.py`) covers the new tables — extend `TABLE_NAMES` if it enumerates them.
