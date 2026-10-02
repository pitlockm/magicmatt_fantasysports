"""Fixture-backed tests for the static reports site and report data functions."""

import json
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


def test_report_functions_show_separate_dead_cap_and_fa_year_convention() -> None:
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
    free_agents = report_free_agents(contracts, teams, 2027)
    assert [row["year"] for row in free_agents["years"]] == [2027, 2029]
    player = free_agents["years"][1]["players"][0]
    assert (player["signed_through"], player["fa_year"]) == (2028, 2029)


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
    for snapshot_name, fixture_name in (
        ("standings", "reports_standings.json"),
        ("matchup_scores", "reports_matchups.json"),
        ("adp", "reports_adp.json"),
    ):
        payload = json.loads((FIXTURES / fixture_name).read_text(encoding="utf-8"))
        save_snapshot(snapshot_name, payload, snapshot_date=date(2026, 9, 30), root=snapshot_root)
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "report-secret-token")
    monkeypatch.setenv("FANTRAX_USER_SECRET_ID", "report-secret-user-id")

    assert reports_main(
        [
            "--season", "2027", "--database", str(database), "--output", str(output),
            "--snapshot-root", str(snapshot_root), "--waiver-csv", str(FIXTURES / "waiver_wire.csv"),
            "--transactions-csv", str(FIXTURES / "transactions.csv"),
        ]
    ) == 0

    expected_pages = {
        "index.html", "free_agents.html", "waivers.html", "standings.html", "history.html",
        "cap_tracker.html", "ledger.html", "validation.html", "transactions.html",
    }
    assert expected_pages.issubset({path.name for path in output.glob("*.html")})
    assert (output / "static" / "style.css").is_file()
    assert (output / "static" / "sort.js").is_file()

    grid_html = (output / "index.html").read_text(encoding="utf-8")
    assert "3 + 2 dead" in grid_html
    assert "trade +2" in grid_html
    assert "IL +1" in grid_html
    assert "76 space" in grid_html
    assert "Nico Prospect" in grid_html
    assert "Career MLB AB / IP" in grid_html
    assert "22" in grid_html
    assert "15 / —" in grid_html

    cap_html = (output / "cap_tracker.html").read_text(encoding="utf-8")
    assert "81" in cap_html
    assert "76" in cap_html
    assert "-1.5" in cap_html

    history_html = (output / "history.html").read_text(encoding="utf-8")
    assert "2025" in history_html and "2026" in history_html
    assert "Alpha Owls" in history_html and "Copperheads" in history_html
    assert "champion" not in history_html.lower() or "Champion" in history_html

    free_agents_html = (output / "free_agents.html").read_text(encoding="utf-8")
    assert "Signed through" in free_agents_html or "signed through" in free_agents_html
    assert "2028" in free_agents_html and "2029" in free_agents_html
    assert "TOP RANK" in free_agents_html
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