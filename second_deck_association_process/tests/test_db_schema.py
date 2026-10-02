"""Schema creation and idempotence tests."""

from pathlib import Path

import pytest
import duckdb

from sda.db.connection import DatabaseBusyError, open_database, writer_lock
from sda.db.schema import create_schema, initialize_database, seed_players, seed_teams, sync_season_config


def test_initialize_database_is_idempotent(tmp_path: Path) -> None:
    """Reinitialization preserves schema and existing rows."""
    database_path = tmp_path / "sda.duckdb"
    initialize_database(database_path)

    with open_database(database_path) as connection:
        connection.execute("INSERT INTO teams (team_id, team_name) VALUES ('t1', 'Team One')")
        create_schema(connection)
        table_names = {
            row[0]
            for row in connection.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
            ).fetchall()
        }
        view_names = {
            row[0]
            for row in connection.execute(
                "SELECT table_name FROM information_schema.views WHERE table_schema = 'main'"
            ).fetchall()
        }
        team_count = connection.execute("SELECT count(*) FROM teams").fetchone()[0]

    assert {
        "teams",
        "players",
        "contract_events",
        "roster_snapshots",
        "pending_contracts",
        "season_config",
        "team_season_history",
        "league_history",
    }.issubset(table_names)
    assert "current_contracts" in view_names
    assert "team_all_time_history" in view_names
    assert team_count == 1
    with open_database(database_path, read_only=True) as connection:
        player_columns = {
            row[0]
            for row in connection.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'players'"
            ).fetchall()
        }
    assert {"birthdate", "career_ab", "career_ip", "mlbam_id", "bio_refreshed_at"}.issubset(player_columns)


def test_seed_teams_players_and_season_config(tmp_path: Path) -> None:
    """Load minimal Fantrax identities and season settings into the schema."""
    database_path = tmp_path / "sda.duckdb"
    initialize_database(database_path)
    config_path = Path(__file__).parents[1] / "config" / "season.yaml"
    with open_database(database_path) as connection:
        assert seed_teams(
            connection,
            {"teamInfo": {"t1": {"id": "t1", "name": "Team One", "managerName": "Manager"}}},
        ) == 1
        assert seed_players(
            connection,
            {"p1": {"fantraxId": "p1", "name": "Player One", "position": "OF", "team": "SEA"}},
            alias_path=tmp_path / "aliases.json",
        ) == 1
        assert sync_season_config(connection, config_path) == 2027
        assert connection.execute("SELECT team_name, manager FROM teams WHERE team_id = 't1'").fetchone() == (
            "Team One",
            "Manager",
        )
        assert connection.execute(
            "SELECT positions, mlb_team FROM players WHERE fantrax_id = 'p1'"
        ).fetchone() == ("OF", "SEA")
        assert connection.execute("SELECT cap_years FROM season_config WHERE season = 2027").fetchone() == (78,)


def test_writer_lock_reports_contention(tmp_path: Path) -> None:
    """Fail clearly when another writer already owns the database lock."""
    database_path = tmp_path / "sda.duckdb"
    with writer_lock(database_path):
        with pytest.raises(DatabaseBusyError, match="Another SDA writer holds"):
            with writer_lock(database_path):
                pass


def test_initialize_replaces_only_empty_legacy_history_table(tmp_path: Path) -> None:
    """Drop the old name-based history schema only when it contains no rows."""
    empty_path = tmp_path / "empty-legacy.duckdb"
    connection = duckdb.connect(str(empty_path))
    connection.execute("CREATE TABLE league_history (season INTEGER PRIMARY KEY, champion VARCHAR)")
    connection.close()

    initialize_database(empty_path)
    with open_database(empty_path, read_only=True) as initialized:
        columns = {
            row[0]
            for row in initialized.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name = 'league_history'"
            ).fetchall()
        }
    assert "champion_team_id" in columns
    assert "champion" not in columns

    populated_path = tmp_path / "populated-legacy.duckdb"
    connection = duckdb.connect(str(populated_path))
    connection.execute("CREATE TABLE league_history (season INTEGER PRIMARY KEY, champion VARCHAR)")
    connection.execute("INSERT INTO league_history VALUES (2025, 'Team One')")
    connection.close()
    with pytest.raises(RuntimeError, match="refusing to discard"):
        initialize_database(populated_path)