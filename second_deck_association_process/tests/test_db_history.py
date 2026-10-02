"""Tests for season and all-time standings history."""

import json
from pathlib import Path
from typing import Any

from sda.db.connection import open_database
from sda.db.history import derive_playoff_champion, finalize_season, update_current_season
from sda.db.schema import initialize_database


def _standings_fixture() -> list[dict[str, Any]]:
    path = Path(__file__).parent / "fixtures" / "standings_history_sample.json"
    with path.open(encoding="utf-8") as fixture_file:
        return json.load(fixture_file)


def test_weekly_updates_upsert_and_finalize_all_time_history(tmp_path: Path) -> None:
    """Repeated cumulative standings updates upsert; finalization populates totals."""
    database_path = tmp_path / "sda.duckdb"
    initialize_database(database_path)
    first_week = _standings_fixture()
    second_week = [
        {**first_week[0], "points": "12-2-0"},
        {**first_week[1], "points": "9-5-0"},
    ]

    assert update_current_season(2027, first_week, database_path) == 2
    assert update_current_season(2027, second_week, database_path) == 2
    finalize_season(2027, "team-1", "team-2", "team-1", database_path)

    with open_database(database_path, read_only=True) as connection:
        all_time = connection.execute(
            "SELECT team_id, all_time_w, all_time_l, championships, runner_ups, "
            "regular_season_firsts, playoff_appearances "
            "FROM team_all_time_history ORDER BY team_id"
        ).fetchall()
    assert all_time == [
        ("team-1", 12, 2, 1, 0, 1, 1),
        ("team-2", 9, 5, 0, 1, 0, 1),
    ]


def test_ambiguous_playoff_shape_does_not_guess_champion() -> None:
    """Matchup results without an explicit champion marker are not enough to infer one."""
    payload = {
        "period": 20,
        "matchups": [
            {"home": {"teamId": "team-1", "score": 5}, "away": {"teamId": "team-2", "score": 4}}
        ],
    }

    assert derive_playoff_champion(payload) is None
    assert derive_playoff_champion({"playoffChampionTeamId": "team-1"}) == "team-1"


def test_finalize_can_leave_ambiguous_champion_null(tmp_path: Path) -> None:
    """A season can be recorded without inventing a champion from ambiguous playoffs."""
    database_path = tmp_path / "sda.duckdb"
    initialize_database(database_path)
    finalize_season(2027, None, None, None, database_path, notes="Champion requires commissioner review")

    with open_database(database_path, read_only=True) as connection:
        row = connection.execute(
            "SELECT champion_team_id, notes FROM league_history WHERE season = 2027"
        ).fetchone()
    assert row == (None, "Champion requires commissioner review")