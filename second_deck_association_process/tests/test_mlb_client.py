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

def test_level_stats_flattens_splits_and_keeps_larger_sample() -> None:
    from sda.mlb.client import MLBStatsClient

    client = MLBStatsClient(cache_ttl_seconds=0, request_interval_seconds=0)
    payload = {
        "stats": [
            {
                "splits": [
                    {"player": {"id": 1}, "team": {"name": "A"}, "stat": {"age": 22, "atBats": 10, "obp": ".300"}},
                    {"player": {"id": 1}, "team": {"name": "B"}, "stat": {"age": 22, "atBats": 50, "obp": ".350"}},
                    {"player": {"id": 2}, "team": {"name": "C"}, "stat": {"inningsPitched": "12.2", "era": "-.--"}},
                ]
            }
        ]
    }
    client._get_json = lambda endpoint, params: payload  # type: ignore[method-assign]
    lines = client.get_level_stats(2026, "hitting", "AA")
    assert lines[1]["team_name"] == "B" and lines[1]["ab"] == 50 and lines[1]["obp"] == 0.35
    assert lines[2]["ip"] == 12 + 2 / 3 and lines[2]["era"] is None


def test_refresh_minor_stats_stores_only_rostered_minors(tmp_path: Path) -> None:
    from sda.db.connection import open_database
    from sda.db.ledger import record_roster_snapshot
    from sda.db.schema import initialize_database
    from sda.mlb.client import refresh_minor_stats

    database = tmp_path / "m.duckdb"
    initialize_database(database)
    with open_database(database) as connection:
        connection.execute("INSERT INTO teams (team_id, team_name) VALUES ('t1', 'T')")
        connection.execute("INSERT INTO players (fantrax_id, name, mlbam_id) VALUES ('p1', 'P One', 11)")
    record_roster_snapshot(date(2027, 3, 1), {"t1": {"minors_roster": [{"id": "p1", "roster_level": "minors"}]}}, database)
    client = Mock()
    client.get_career_stats.return_value = {"career_ab": 5, "career_ip": None}
    client.get_bio.return_value = None
    client.get_level_stats.side_effect = lambda season, group, level: (
        {11: {"team_name": "X", "age": 21, "ab": 80, "runs": 9, "home_runs": 3, "stolen_bases": 1,
              "obp": 0.4, "ip": None, "strikeouts": None, "era": None, "whip": None},
         99: {"team_name": "Y", "age": 30, "ab": 1, "runs": 0, "home_runs": 0, "stolen_bases": 0,
              "obp": 0.0, "ip": None, "strikeouts": None, "era": None, "whip": None}}
        if (group, level) == ("hitting", "AA") else {}
    )
    assert refresh_minor_stats(database, 2026, client=client) == 1
    with open_database(database, read_only=True) as connection:
        assert connection.execute("SELECT fantrax_id, level, ab FROM minor_league_stats").fetchall() == [("p1", "AA", 80)]


def test_find_mlbam_id_accepts_last_first_names() -> None:
    client = MLBStatsClient(cache_ttl_seconds=0, request_interval_seconds=0)
    seen: list[dict[str, str]] = []

    def fake(endpoint: str, params: dict[str, str]) -> dict:
        seen.append(params)
        return {"people": [{"id": 7, "fullName": "Young Woong Kim"}]}

    client._get_json = fake  # type: ignore[method-assign]
    assert client.find_mlbam_id("Woong Kim, Young") == 7
    assert seen == [{"names": "Young Woong Kim"}]


def test_refresh_mlb_stats_stores_mlb_level_lines(tmp_path) -> None:
    from sda.db.connection import open_database
    from sda.db.schema import initialize_database
    from sda.mlb.client import refresh_mlb_stats

    database = tmp_path / "sda.duckdb"
    initialize_database(database)
    with open_database(database) as connection:
        connection.execute("INSERT INTO players (fantrax_id, name, mlbam_id) VALUES ('a', 'Doe, Jane', 5)")
        connection.execute(
            "INSERT INTO roster_snapshots (snapshot_date, team_id, fantrax_id, roster_level) "
            "VALUES ('2026-10-01', 't', 'a', 'MLB')"
        )

    class Fake:
        def get_bio(self, mlbam_id): return None
        def get_career_stats(self, mlbam_id): return {"career_ab": None, "career_ip": None}
        def get_level_stats(self, season, group, level):
            line = dict(team_name="X", age=25, ab=1, runs=2, home_runs=3, stolen_bases=4, obp=.3,
                        ip=10.0, strikeouts=9, era=3.0, whip=1.1, saves_holds=2)
            return {5: line} if level == "MLB" and group == "pitching" else {}

    assert refresh_mlb_stats(database, 2026, client=Fake()) == 1
    with open_database(database, read_only=True) as connection:
        assert connection.execute("SELECT ip, saves_holds FROM mlb_player_stats").fetchone() == (10.0, 2)
