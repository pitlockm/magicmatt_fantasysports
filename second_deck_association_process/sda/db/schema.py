"""Idempotent DuckDB schema setup for the SDA data model."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Any

import duckdb
import yaml

from sda.db.connection import DEFAULT_DATABASE_PATH, open_database
from sda.fantrax.normalize import normalize_players

SCHEMA_SQL = """
CREATE SEQUENCE IF NOT EXISTS contract_event_id_seq START 1;
CREATE SEQUENCE IF NOT EXISTS pending_contract_id_seq START 1;
CREATE SEQUENCE IF NOT EXISTS announcement_id_seq START 1;

CREATE TABLE IF NOT EXISTS teams (
    team_id VARCHAR PRIMARY KEY,
    team_name VARCHAR NOT NULL,
    manager VARCHAR
);

CREATE TABLE IF NOT EXISTS players (
    fantrax_id VARCHAR PRIMARY KEY,
    name VARCHAR NOT NULL,
    positions VARCHAR,
    mlb_team VARCHAR,
    real_draft_year INTEGER,
    aliases VARCHAR,
    birthdate DATE,
    career_ab INTEGER,
    career_ip DOUBLE,
    mlbam_id INTEGER,
    bio_refreshed_at TIMESTAMP,
    real_life_il BOOLEAN
);

ALTER TABLE players ADD COLUMN IF NOT EXISTS birthdate DATE;
ALTER TABLE players ADD COLUMN IF NOT EXISTS career_ab INTEGER;
ALTER TABLE players ADD COLUMN IF NOT EXISTS career_ip DOUBLE;
ALTER TABLE players ADD COLUMN IF NOT EXISTS mlbam_id INTEGER;
ALTER TABLE players ADD COLUMN IF NOT EXISTS bio_refreshed_at TIMESTAMP;
ALTER TABLE players ADD COLUMN IF NOT EXISTS real_life_il BOOLEAN;

CREATE TABLE IF NOT EXISTS contract_events (
    event_id INTEGER PRIMARY KEY DEFAULT nextval('contract_event_id_seq'),
    ts TIMESTAMP NOT NULL,
    recorded_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    team_id VARCHAR NOT NULL REFERENCES teams(team_id),
    fantrax_id VARCHAR REFERENCES players(fantrax_id),
    event_type VARCHAR NOT NULL CHECK (
        event_type IN (
            'SIGNED', 'EXTENDED', 'DROPPED', 'EXPIRED', 'CALLED_UP',
            'CAP_TRADE', 'DEFAULTED_1YR'
        )
    ),
    years DOUBLE NOT NULL CHECK (event_type = 'CAP_TRADE' OR years >= 0),
    fa_year INTEGER,
    source VARCHAR NOT NULL CHECK (
        source IN ('form', 'manual', 'migration', 'fantrax', 'system')
    ),
    note VARCHAR,
    approved_by VARCHAR,
    roster_level VARCHAR NOT NULL DEFAULT 'MLB' CHECK (roster_level IN ('MLB', 'minors')),
    form_ref VARCHAR,
    acquisition_type VARCHAR CHECK (acquisition_type IN ('drafted', 'called_up', 'waiver')),
    announced_at TIMESTAMP,
    CHECK (
        (event_type = 'CAP_TRADE' AND fantrax_id IS NULL AND fa_year IS NULL)
        OR (event_type <> 'CAP_TRADE' AND fantrax_id IS NOT NULL AND fa_year IS NOT NULL)
    )
);

ALTER TABLE contract_events ADD COLUMN IF NOT EXISTS form_ref VARCHAR;
ALTER TABLE contract_events ADD COLUMN IF NOT EXISTS acquisition_type VARCHAR;
ALTER TABLE contract_events ADD COLUMN IF NOT EXISTS announced_at TIMESTAMP;

CREATE TABLE IF NOT EXISTS roster_snapshots (
    snapshot_date DATE NOT NULL,
    team_id VARCHAR NOT NULL,
    fantrax_id VARCHAR NOT NULL,
    roster_level VARCHAR NOT NULL CHECK (roster_level IN ('MLB', 'minors', 'IL')),
    PRIMARY KEY (snapshot_date, team_id, fantrax_id)
);

CREATE TABLE IF NOT EXISTS pending_contracts (
    pending_id INTEGER PRIMARY KEY DEFAULT nextval('pending_contract_id_seq'),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    team_id VARCHAR NOT NULL,
    fantrax_id VARCHAR,
    years DOUBLE NOT NULL,
    fa_year INTEGER,
    raw_message VARCHAR NOT NULL,
    status VARCHAR NOT NULL DEFAULT 'pending' CHECK (
        status IN ('pending', 'approved', 'rejected', 'expired')
    )
);

ALTER TABLE pending_contracts ADD COLUMN IF NOT EXISTS kind VARCHAR DEFAULT 'signing';
ALTER TABLE pending_contracts ADD COLUMN IF NOT EXISTS to_team_id VARCHAR;
ALTER TABLE pending_contracts ADD COLUMN IF NOT EXISTS discord_message_id VARCHAR;
ALTER TABLE pending_contracts ADD COLUMN IF NOT EXISTS approval_message_id VARCHAR;
ALTER TABLE pending_contracts ADD COLUMN IF NOT EXISTS form_ref VARCHAR;
ALTER TABLE pending_contracts ADD COLUMN IF NOT EXISTS acquisition_type VARCHAR;
ALTER TABLE pending_contracts ADD COLUMN IF NOT EXISTS announced_at TIMESTAMP;

CREATE TABLE IF NOT EXISTS discord_bot_state (
    state_key VARCHAR PRIMARY KEY,
    state_value VARCHAR NOT NULL,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS discord_processed_messages (
    message_id VARCHAR PRIMARY KEY,
    channel_id VARCHAR NOT NULL,
    processed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    outcome VARCHAR NOT NULL
);

ALTER TABLE discord_processed_messages ADD COLUMN IF NOT EXISTS form_ref VARCHAR;

CREATE TABLE IF NOT EXISTS announcements (
    announcement_id BIGINT PRIMARY KEY DEFAULT nextval('announcement_id_seq'),
    message_id VARCHAR UNIQUE,
    team_id VARCHAR NOT NULL,
    fantrax_id VARCHAR NOT NULL,
    announced_at TIMESTAMP NOT NULL,
    raw_message VARCHAR NOT NULL,
    form_ref VARCHAR,
    acquisition_type VARCHAR,
    kind VARCHAR NOT NULL DEFAULT 'signing'
);

ALTER TABLE announcements ADD COLUMN IF NOT EXISTS form_ref VARCHAR;
ALTER TABLE announcements ADD COLUMN IF NOT EXISTS acquisition_type VARCHAR;
ALTER TABLE announcements ADD COLUMN IF NOT EXISTS kind VARCHAR DEFAULT 'signing';

CREATE TABLE IF NOT EXISTS season_config (
    season INTEGER PRIMARY KEY,
    freeze_date DATE,
    trade_deadline DATE,
    draft_start DATE,
    draft_end DATE,
    min_mlb_roster INTEGER,
    cap_years INTEGER NOT NULL DEFAULT 78
);

CREATE TABLE IF NOT EXISTS team_season_history (
    season INTEGER NOT NULL,
    team_id VARCHAR NOT NULL REFERENCES teams(team_id),
    w INTEGER NOT NULL DEFAULT 0,
    l INTEGER NOT NULL DEFAULT 0,
    t INTEGER NOT NULL DEFAULT 0,
    regular_season_rank INTEGER,
    made_playoffs BOOLEAN,
    playoff_finish VARCHAR CHECK (
        playoff_finish IN ('champion', 'runner_up', 'semifinal', 'quarterfinal')
    ),
    PRIMARY KEY (season, team_id)
);

CREATE TABLE IF NOT EXISTS league_history (
    season INTEGER PRIMARY KEY,
    champion_team_id VARCHAR REFERENCES teams(team_id),
    runner_up_team_id VARCHAR REFERENCES teams(team_id),
    regular_season_first_team_id VARCHAR REFERENCES teams(team_id),
    prize_champion DOUBLE,
    prize_runner_up DOUBLE,
    notes VARCHAR
);

CREATE OR REPLACE VIEW team_all_time_history AS
SELECT
    team_id,
    COUNT(*) AS seasons_played,
    SUM(w) AS all_time_w,
    SUM(l) AS all_time_l,
    SUM(t) AS all_time_t,
    SUM(CASE WHEN playoff_finish = 'champion' THEN 1 ELSE 0 END) AS championships,
    SUM(CASE WHEN playoff_finish = 'runner_up' THEN 1 ELSE 0 END) AS runner_ups,
    SUM(CASE WHEN regular_season_rank = 1 THEN 1 ELSE 0 END) AS regular_season_firsts,
    SUM(CASE WHEN made_playoffs THEN 1 ELSE 0 END) AS playoff_appearances
FROM team_season_history
GROUP BY team_id;

CREATE OR REPLACE VIEW current_contracts AS
WITH ranked_events AS (
    SELECT
        event_id,
        ts,
        recorded_at,
        team_id,
        fantrax_id,
        event_type,
        years,
        fa_year,
        source,
        note,
        approved_by,
        roster_level,
        form_ref,
        acquisition_type,
        announced_at,
        ROW_NUMBER() OVER (
            PARTITION BY team_id, fantrax_id
            ORDER BY ts DESC, recorded_at DESC, event_id DESC
        ) AS event_rank
    FROM contract_events
    WHERE fantrax_id IS NOT NULL
)
SELECT
    event_id, ts, recorded_at, team_id, fantrax_id, event_type, years,
    fa_year, source, note, approved_by, roster_level, form_ref, acquisition_type, announced_at
FROM ranked_events
WHERE event_rank = 1
    AND event_type NOT IN ('DROPPED', 'EXPIRED');
"""


def create_schema(connection: duckdb.DuckDBPyConnection) -> None:
    """Create all SDA tables and views without altering existing rows."""
    _drop_empty_legacy_league_history(connection)
    connection.execute(SCHEMA_SQL)
    _ensure_pending_contract_identity(connection)
    _ensure_form_reference_indexes(connection)


def _ensure_pending_contract_identity(connection: duckdb.DuckDBPyConnection) -> None:
    """Allow team-level trade proposals and enforce unique Discord queue identity."""
    columns = connection.execute("PRAGMA table_info('pending_contracts')").fetchall()
    fantrax_id = next((row for row in columns if row[1] == "fantrax_id"), None)
    if fantrax_id is not None and fantrax_id[3]:
        connection.execute("ALTER TABLE pending_contracts ALTER COLUMN fantrax_id DROP NOT NULL")
    connection.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS pending_contract_message_idx "
        "ON pending_contracts (discord_message_id)"
    )


def _ensure_form_reference_indexes(connection: duckdb.DuckDBPyConnection) -> None:
    """Make relay UUIDs unique at each durable intake boundary."""
    for table_name in ("pending_contracts", "discord_processed_messages", "announcements"):
        connection.execute(
            f"CREATE UNIQUE INDEX IF NOT EXISTS {table_name}_form_ref_idx "
            f"ON {table_name} (form_ref)"
        )


def _drop_empty_legacy_league_history(connection: duckdb.DuckDBPyConnection) -> None:
    tables = connection.execute(
        "SELECT count(*) FROM information_schema.tables "
        "WHERE table_schema = 'main' AND table_name = 'league_history'"
    ).fetchone()[0]
    if not tables:
        return
    columns = {
        row[0]
        for row in connection.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'main' AND table_name = 'league_history'"
        ).fetchall()
    }
    if "champion" not in columns or "champion_team_id" in columns:
        return
    row_count = connection.execute("SELECT count(*) FROM league_history").fetchone()[0]
    if row_count:
        raise RuntimeError(
            "Legacy league_history contains rows; refusing to discard them while changing its schema"
        )
    connection.execute("DROP TABLE league_history")


def initialize_database(database_path: Path = DEFAULT_DATABASE_PATH) -> Path:
    """Create the database file and initialize its schema idempotently."""
    with open_database(database_path) as connection:
        create_schema(connection)
    return Path(database_path)


def seed_teams(connection: duckdb.DuckDBPyConnection, payload: Mapping[str, Any]) -> int:
    """Upsert team identities from a Fantrax getLeagueInfo response."""
    team_info = payload.get("teamInfo", payload.get("teams", {}))
    if isinstance(team_info, Mapping):
        entries = team_info.items()
    elif isinstance(team_info, list):
        entries = ((str(index), team) for index, team in enumerate(team_info))
    else:
        raise ValueError("League info must contain a teamInfo or teams collection")

    seeded = 0
    for fallback_id, team in entries:
        if not isinstance(team, Mapping):
            continue
        team_id = team.get("teamId", team.get("id", fallback_id))
        team_name = team.get("teamName", team.get("name"))
        if team_id is None or not team_name:
            continue
        manager = team.get("manager", team.get("managerName", team.get("ownerName")))
        if isinstance(manager, Mapping):
            manager = manager.get("name")
        connection.execute(
            """INSERT INTO teams (team_id, team_name, manager) VALUES (?, ?, ?)
               ON CONFLICT (team_id) DO UPDATE SET
                   team_name = excluded.team_name,
                   manager = excluded.manager""",
            [str(team_id), str(team_name), manager],
        )
        seeded += 1
    return seeded


def seed_players(
    connection: duckdb.DuckDBPyConnection,
    payload: Any,
    alias_path: Path | None = None,
) -> int:
    """Upsert the Fantrax player directory without overwriting draft-year data."""
    from sda.fantrax.normalize import DEFAULT_ALIAS_PATH, load_aliases

    aliases_by_player: dict[str, list[str]] = {}
    for alias, fantrax_id in load_aliases(alias_path or DEFAULT_ALIAS_PATH).items():
        aliases_by_player.setdefault(fantrax_id, []).append(alias)

    players = normalize_players(payload)
    rows = [
        (
            fantrax_id,
            str(player["name"]),
            ",".join(player["positions"]) or None,
            player["mlb_team"],
            json.dumps(aliases_by_player.get(fantrax_id, []), ensure_ascii=False),
        )
        for fantrax_id, player in players.items()
    ]
    if rows:
        connection.executemany(
            """INSERT INTO players (fantrax_id, name, positions, mlb_team, aliases)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT (fantrax_id) DO UPDATE SET
                   name = excluded.name,
                   positions = excluded.positions,
                   mlb_team = excluded.mlb_team,
                   aliases = CASE
                       WHEN excluded.aliases = '[]' THEN players.aliases
                       ELSE excluded.aliases
                   END""",
            rows,
        )
    return len(rows)


def sync_season_config(
    connection: duckdb.DuckDBPyConnection,
    config_path: Path,
) -> int:
    """Upsert the configured season dates and roster/cap settings."""
    with config_path.open(encoding="utf-8") as config_file:
        config = yaml.safe_load(config_file)
    if not isinstance(config, Mapping) or "season" not in config:
        raise ValueError(f"Season configuration is missing a season value: {config_path}")

    def config_date(key: str) -> date | None:
        value = config.get(key)
        if value is None:
            return None
        if isinstance(value, date):
            return value
        return date.fromisoformat(str(value))

    connection.execute(
        """INSERT INTO season_config (
               season, freeze_date, trade_deadline, draft_start, draft_end,
               min_mlb_roster, cap_years
           ) VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT (season) DO UPDATE SET
               freeze_date = excluded.freeze_date,
               trade_deadline = excluded.trade_deadline,
               draft_start = excluded.draft_start,
               draft_end = excluded.draft_end,
               min_mlb_roster = excluded.min_mlb_roster,
               cap_years = excluded.cap_years""",
        [
            int(config["season"]),
            config_date("freeze_date"),
            config_date("trade_deadline"),
            config_date("draft_start"),
            config_date("draft_end"),
            config.get("min_mlb_roster"),
            int(config.get("cap_years", 78)),
        ],
    )
    return int(config["season"])