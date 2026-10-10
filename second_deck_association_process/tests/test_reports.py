"""Fixture-backed tests for the static reports site and report data functions."""

import json
import csv
from datetime import date
from pathlib import Path

import pytest

from sda.db.connection import open_database
from sda.db.schema import initialize_database
from sda.fantrax.snapshots import save_snapshot
from sda.reports.build import (
    main as reports_main,
    report_free_agents,
    report_multiyear_grid,
    report_standings,
)

PROJECT_ROOT = Path(__file__).parents[1]
FIXTURES = Path(__file__).parent / "fixtures"


def _fixture_database(path: Path) -> None:
    initialize_database(path)
    with open_database(path) as connection:
        connection.execute((FIXTURES / "reports_fixture.sql").read_text(encoding="utf-8"))


def test_report_functions_show_separate_dead_cap_and_enriched_free_agent_rows() -> None:
    teams = [{"team_id": "t1", "team_name": "Team One", "manager": "Manager"}]
    contracts = [
        {
            "team_id": "t1", "fantrax_id": "p1", "player_name": "Player One",
            "ts": date(2026, 3, 1), "fa_year": 2029, "roster_level": "MLB",
        },
        {
            "team_id": "t1", "fantrax_id": "p2", "player_name": "One-year Player",
            "ts": date(2026, 5, 1), "fa_year": 2027, "roster_level": "MLB",
        },
    ]
    events = [
        {
            "team_id": "t1", "event_type": "DROPPED", "ts": date(2026, 6, 1),
            "years": 2.5, "fa_year": 2031,
        },
        {
            "team_id": "t1", "event_type": "CAP_TRADE", "ts": date(2027, 1, 1),
            "years": 2,
        },
    ]
    grid = report_multiyear_grid(teams, contracts, events, {"t1": 1}, 2027)
    current = grid["teams"][0]["cells"][0]
    assert (current["active_years"], current["dead_cap"], current["remaining"]) == (2, 2.0, 77.0)
    assert current["cap_trade"] == 2
    free_agents = report_free_agents(
        contracts,
        teams,
        2027,
        players={
            "p1": {"positions": "OF, RF", "birthdate": date(2001, 5, 3)},
            "p2": {"positions": "SP", "birthdate": date(2000, 11, 11)},
        },
        stats_by_id={
            "p1": {"runs": 90, "obp": 0.355, "home_runs": 24, "stolen_bases": 8},
            "p2": {"strikeouts": 175, "era": 3.25, "whip": 1.12, "quality_starts": 18, "saves_holds": 2},
        },
        stats_season=2026,
        adp_by_id={"p1": 38, "p2": 72.5},
        adp_as_of="2026-09-30",
    )
    assert [row["fa_year"] for row in free_agents["players"]] == [2027, 2029]
    player = free_agents["players"][1]
    assert player["positions"] == ["OF", "RF"]
    assert player["age"] == 25
    assert player["adp"] == 38
    assert player["obp"] == 0.355
    assert free_agents["adp_as_of"] == "2026-09-30"
    assert free_agents["stats_available"] is True


def test_contract_grid_expands_multi_year_one_year_cap_trades_and_current_il() -> None:
    teams = [{"team_id": "t1", "team_name": "Team One", "manager": "Manager"}]
    contracts = [
        {"team_id": "t1", "fantrax_id": "p-multi", "player_name": "Multi Deal", "positions": "CF", "ts": date(2026, 1, 1), "fa_year": 2030},
        {"team_id": "t1", "fantrax_id": "p-single", "player_name": "One Year", "positions": "3B", "ts": date(2027, 1, 1), "fa_year": 2028},
    ]
    events = [
        {"team_id": "t1", "event_type": "CAP_TRADE", "ts": date(2027, 2, 1), "years": 2, "note": "Received from Team Two"},
    ]

    report = report_multiyear_grid(
        teams,
        contracts,
        events,
        {"t1": 1},
        2027,
        il_players={"t1": [{"player_name": "IL Player", "fantrax_id": "p-il"}]},
        il_roster_available=True,
    )

    current, future = report["teams"][0]["cells"][:2]
    assert current["committed"] == 4
    assert current["effective_cap"] == 81
    assert [row["player_name"] for row in current["contracts"]] == ["Multi Deal"]
    assert current["contracts"][0]["position"] == "CF"
    assert [row["player_name"] for row in current["one_year_contracts"]] == ["One Year"]
    assert current["one_year_contracts"][0]["position"] == "3B"
    assert current["cap_trade_events"][0]["years"] == 2
    assert current["il_roster_known"] is True
    assert [row["player_name"] for row in current["il_players"]] == ["IL Player"]
    assert [row["player_name"] for row in future["one_year_contracts"]] == []
    assert future["il_roster_known"] is False
    assert future["il_players"] == []


def test_contract_grid_details_include_dead_cap_player_and_minor_positions() -> None:
    teams = [{"team_id": "t1", "team_name": "Team One", "manager": "Manager"}]
    contracts = [
        {"team_id": "t1", "fantrax_id": "p-drop", "player_name": "Dropped Player", "positions": "2B", "ts": date(2026, 1, 1), "fa_year": 2031},
    ]
    events = [
        {"team_id": "t1", "fantrax_id": "p-drop", "player_name": "Dropped Player", "positions": "2B", "event_type": "DROPPED", "ts": date(2027, 1, 1), "years": 2.5, "fa_year": 2031},
    ]
    minors = [
        {"team_id": "t1", "player_name": "Minor Player", "fantrax_id": "p-minor", "positions": "SS", "birthdate": date(2005, 2, 17)},
    ]

    report = report_multiyear_grid(teams, contracts, events, {}, 2027, minors=minors)

    current, next_year = report["teams"][0]["cells"][:2]
    assert current["dead_cap"] == 2.5
    assert current["dead_cap_players"][0]["position"] == "2B"
    assert next_year["dead_cap"] == 2.0
    assert next_year["dead_cap_players"][0]["player_name"] == "Dropped Player"
    assert next_year["dead_cap_players"][0]["position"] == "2B"
    minor = report["teams"][0]["minor_players"][0]
    assert minor["position"] == "SS"
    assert "ages" not in minor


def test_player_stats_csv_selects_latest_completed_season_by_fantrax_id(tmp_path: Path) -> None:
    stats_csv = tmp_path / "fantrax_stats.csv"
    with stats_csv.open("w", encoding="utf-8", newline="") as output:
        writer = csv.writer(output)
        writer.writerow(["Fantrax ID", "Season", "R", "OBP", "HR", "SB", "K", "ERA", "WHIP", "QS", "SV/HLD"])
        writer.writerow(["p1", 2025, 80, 0.330, 20, 5, "", "", "", "", ""])
        writer.writerow(["p1", 2026, 92, 0.371, 29, 11, "", "", "", "", ""])

    from sda.reports.build import _load_player_stats_csv

    stats, selected_season = _load_player_stats_csv(stats_csv, 2026)

    assert selected_season == 2026
    assert stats["p1"]["runs"] == 92
    assert stats["p1"]["obp"] == 0.371
    assert stats["p1"]["home_runs"] == 29
    assert stats["p1"]["stolen_bases"] == 11


def test_standings_combines_category_totals_and_accumulates_matchup_points() -> None:
    teams = [{"team_id": "t1", "team_name": "Team One"}]
    standings = [
        {"teamId": "t1", "teamName": "Team One", "categories": {"R": {"total": 420}}}
    ]
    matchups = [
        {"teamId": "t1", "teamName": "Team One", "categories": {"R": {"points": 3}}},
        {"teamId": "t1", "teamName": "Team One", "categories": {"R": {"points": 4}}},
    ]

    report = report_standings(standings, matchups, teams)

    assert report["teams"][0]["totals"]["R"] == 420
    assert report["teams"][0]["points"]["R"] == 7


def test_build_site_renders_all_pages_cap_history_fallbacks_and_no_secrets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "reports.duckdb"
    output = tmp_path / "site"
    snapshot_root = tmp_path / "raw"
    _fixture_database(database)
    with open_database(database) as connection:
        connection.execute("DELETE FROM roster_snapshots")
    stats_csv = tmp_path / "fantrax_stats.csv"
    with stats_csv.open("w", encoding="utf-8", newline="") as output_file:
        writer = csv.writer(output_file)
        writer.writerow(["Fantrax ID", "Season", "R", "OBP", "HR", "SB", "K", "ERA", "WHIP", "QS", "SV/HLD"])
        writer.writerow(["p-active", 2026, 101, 0.361, 31, 7, "", "", "", "", ""])
        writer.writerow(["p-beta", 2026, "", "", "", "", 188, 3.18, 1.09, 20, 5])
    for snapshot_name, fixture_name in (
        ("standings", "reports_standings.json"),
        ("matchup_scores", "reports_matchups.json"),
        ("adp", "reports_adp.json"),
    ):
        payload = json.loads((FIXTURES / fixture_name).read_text(encoding="utf-8"))
        save_snapshot(snapshot_name, payload, snapshot_date=date(2026, 9, 30), root=snapshot_root)
    save_snapshot(
        "team_rosters",
        {
            "rosters": {
                "team-a": {
                    "teamName": "Alpha Owls",
                    "rosterItems": [
                        {"id": "p-active", "status": "INJURED_RESERVE", "position": "OF"},
                        {"id": "p-minor", "status": "MINORS", "position": "SS"},
                    ],
                },
                "team-b": {
                    "teamName": "Copperheads",
                    "rosterItems": [{"id": "p-beta", "status": "ACTIVE", "position": "SP"}],
                },
            }
        },
        snapshot_date=date(2026, 10, 2),
        root=snapshot_root,
    )
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "report-secret-token")
    monkeypatch.setenv("FANTRAX_USER_SECRET_ID", "report-secret-user-id")

    assert reports_main(
        [
            "--season", "2027", "--database", str(database), "--output", str(output),
            "--snapshot-root", str(snapshot_root), "--waiver-csv", str(FIXTURES / "waiver_wire.csv"),
            "--transactions-csv", str(FIXTURES / "transactions.csv"),
                "--player-stats-csv", str(stats_csv),
        ]
    ) == 0

    expected_pages = {
        "index.html", "free_agents.html", "waivers.html", "standings.html", "history.html",
        "ledger.html", "validation.html", "transactions.html",
    }
    assert expected_pages.issubset({path.name for path in output.glob("*.html")})
    assert (output / "static" / "style.css").is_file()
    assert (output / "static" / "sort.js").is_file()
    api_files = {
        "cap_state.json", "players.json", "recent_adds.json", "draft_results.json"
    }
    api_dir = output / "api"
    assert api_files.issubset({path.name for path in api_dir.glob("*.json")})
    api_data = {
        path.name: json.loads(path.read_text(encoding="utf-8"))
        for path in api_dir.glob("*.json")
    }
    assert api_data["cap_state.json"]["teams"]["team-a"]["effective_cap"] == 81
    assert api_data["cap_state.json"]["teams"]["team-a"]["committed_years"] == 5
    assert api_data["cap_state.json"]["teams"]["team-a"]["remaining"] == 76
    assert api_data["players.json"]["players"]["Nico Prospect"] == "p-minor"
    assert api_data["recent_adds.json"] == []
    assert api_data["draft_results.json"] == {}

    grid_html = (output / "index.html").read_text(encoding="utf-8")
    assert "3 + 2 dead" in grid_html
    assert "trade +2" in grid_html
    assert "IL +1" in grid_html
    assert "76 space" in grid_html
    assert "Nico Prospect" in grid_html
    assert "Alpha Owls · 1 players" in grid_html
    assert "Minor league players" in grid_html
    assert "Elliot Slugger" in grid_html and "1B" in grid_html
    assert "Multi-year deals" not in grid_html
    assert "2027 age" not in grid_html
    assert "Career MLB AB / IP" not in grid_html
    assert "Dropped Player" not in grid_html

    assert not (output / "cap_tracker.html").exists()
    assert "1 cap trade" in grid_html
    assert "1 IL player" in grid_html
    assert "multi-year deal" in grid_html
    assert "IL roster not projected" in grid_html

    history_html = (output / "history.html").read_text(encoding="utf-8")
    assert "2025" in history_html and "2026" in history_html
    assert "Alpha Owls" in history_html and "Copperheads" in history_html
    assert "champion" not in history_html.lower() or "Champion" in history_html

    free_agents_html = (output / "free_agents.html").read_text(encoding="utf-8")
    assert "Signed through" not in free_agents_html
    assert "Deal</th>" not in free_agents_html
    assert "data-filter=\"fa-year\"" in free_agents_html
    assert "data-filter=\"position\"" in free_agents_html
    assert "data-filter=\"team\"" in free_agents_html
    assert "Most recent completed season" in free_agents_html or "STATS 2026" in free_agents_html
    assert "ADP" in free_agents_html
    assert "2026-09-30" in free_agents_html
    assert "Mason Hitter" in free_agents_html and "Riley Pitcher" in free_agents_html
    assert "0.361" in free_agents_html and "3.18" in free_agents_html
    assert "Player" in free_agents_html and "Pos." in free_agents_html
    assert "OBP" in free_agents_html and "SV/HLD" in free_agents_html
    assert "25" in free_agents_html
    assert "Showing 2 players" in free_agents_html
    assert "Reset" in free_agents_html
    assert "2029" in free_agents_html and "2030" in free_agents_html
    assert "TOP RANK" not in free_agents_html
    waiver_html = (output / "waivers.html").read_text(encoding="utf-8")
    assert "Waiver Star" in waiver_html
    assert "Available Free Agent" not in waiver_html
    assert "36.4%" in waiver_html
    standings_html = (output / "standings.html").read_text(encoding="utf-8")
    assert "810" in standings_html
    assert "SVH" in standings_html
    assert "sort.js" in standings_html
    assert "CLICK HEADERS TO SORT" not in standings_html
    assert "Riley Pitcher" in (output / "transactions.html").read_text(encoding="utf-8")

    built_site = "\n".join(path.read_text(encoding="utf-8") for path in output.rglob("*.*"))
    assert "report-secret-token" not in built_site
    assert "report-secret-user-id" not in built_site

def test_roster_projections_group_by_position_longest_contract_first_and_list_minors() -> None:
    from sda.reports.build import report_roster_projections

    teams = [{"team_id": "t1", "team_name": "Team One"}]
    contracts = [
        {"team_id": "t1", "fantrax_id": "a", "player_name": "Short C", "positions": "C", "fa_year": 2028},
        {"team_id": "t1", "fantrax_id": "b", "player_name": "Long C", "positions": "C", "fa_year": 2029},
        {"team_id": "t1", "fantrax_id": "c", "player_name": "Ace", "positions": "SP", "fa_year": 2027},
    ]
    minors = [
        {"team_id": "t1", "fantrax_id": "d", "player_name": "Prospect C", "positions": "C"},
        {"team_id": "t1", "fantrax_id": "a", "player_name": "Short C", "positions": "C"},
    ]
    report = report_roster_projections(teams, contracts, minors, 2026)
    players = report["teams"][0]["players"]
    assert [p["player_name"] for p in players] == ["Long C", "Short C", "Prospect C", "Ace"]
    assert [y["year"] for y in players[0]["years"] if y["on_roster"]] == [2026, 2027, 2028]
    assert not any(y["on_roster"] for y in players[2]["years"])
    assert players[2]["signed"] is False
