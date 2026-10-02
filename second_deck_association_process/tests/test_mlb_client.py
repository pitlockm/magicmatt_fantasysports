"""Tests for MLB Stats API identity and bio refresh behavior."""

from datetime import date
from pathlib import Path
from unittest.mock import Mock

from sda.db.connection import open_database
from sda.db.ledger import record_roster_snapshot
from sda.db.schema import initialize_database
from sda.mlb.client import MLBStatsClient, parse_innings_pitched, refresh_minor_bios


def test_exact_name_match_and_ambiguity(tmp_path: Path) -> None:
    """Resolve a unique normalized exact name and reject ambiguous exact matches."""
    session = Mock()
    exact_response = Mock(status_code=200)
    exact_response.json.return_value = {
        "people": [{"id": 123, "fullName": "Jose Ramirez"}, {"id": 456, "fullName": "José Ramírez Jr."}]
    }
    ambiguous_response = Mock(status_code=200)
    ambiguous_response.json.return_value = {
        "people": [{"id": 123, "fullName": "Jose Ramirez"}, {"id": 456, "fullName": "José Ramírez"}]
    }
    session.get.side_effect = [exact_response, ambiguous_response]
    client = MLBStatsClient(session=session, cache_dir=tmp_path / "cache", request_interval_seconds=0)

    assert client.find_mlbam_id("José Ramírez") == 123
    assert client.find_mlbam_id("Jose Ramirez") is None


def test_career_stats_parses_baseball_innings_notation(tmp_path: Path) -> None:
    """Read career AB and convert .1/.2 innings notation into fractional innings."""
    response = Mock(status_code=200)
    response.json.return_value = {
        "stats": [
            {"group": {"displayName": "hitting"}, "splits": [{"stat": {"atBats": 51}}]},
            {"group": {"displayName": "pitching"}, "splits": [{"stat": {"inningsPitched": "12.2"}}]},
        ]
    }
    session = Mock()
    session.get.return_value = response
    client = MLBStatsClient(session=session, cache_dir=tmp_path / "cache", request_interval_seconds=0)

    assert client.get_career_stats(123) == {"career_ab": 51, "career_ip": 12 + 2 / 3}
    assert parse_innings_pitched("12.1") == 12 + 1 / 3
    assert parse_innings_pitched("12.3") is None


def test_refresh_stamps_unknown_bios_and_leaves_fields_null(tmp_path: Path) -> None:
    """Refresh only latest-snapshot minors and preserve unknown data as NULL."""
    database_path = tmp_path / "sda.duckdb"
    initialize_database(database_path)
    with open_database(database_path) as connection:
        connection.execute("INSERT INTO teams (team_id, team_name) VALUES ('team-1', 'Team One')")
        connection.execute("INSERT INTO players (fantrax_id, name) VALUES ('known', 'Known Prospect'), ('unknown', 'Unknown Prospect')")
    record_roster_snapshot(
        date(2026, 10, 1),
        {"team-1": {"mlb_roster": [], "minors_roster": [{"id": "known"}, {"id": "unknown"}]}},
        database_path,
    )

    client = Mock()
    client.find_mlbam_id.side_effect = [987, None]
    client.get_bio.return_value = date(2004, 2, 3)
    client.get_career_stats.return_value = {"career_ab": 10, "career_ip": 2.1}

    assert refresh_minor_bios(database_path, client=client) == 2
    with open_database(database_path, read_only=True) as connection:
        records = connection.execute(
            "SELECT fantrax_id, mlbam_id, birthdate, career_ab, career_ip, bio_refreshed_at "
            "FROM players ORDER BY fantrax_id"
        ).fetchall()
    assert records[0][:5] == ("known", 987, date(2004, 2, 3), 10, 2.1)
    assert records[0][5] is not None
    assert records[1][:5] == ("unknown", None, None, None, None)
    assert records[1][5] is not None
    client.get_bio.assert_called_once_with(987)
    client.get_career_stats.assert_called_once_with(987)