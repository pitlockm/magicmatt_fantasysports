"""Tests for Google Sheets migration planning and commit gating."""

import csv
import json
from datetime import datetime
from pathlib import Path

from sda.db.connection import open_database
from sda.db.migrate import (
    REQUIRED_TABS,
    MigrationEvent,
    MigrationPlan,
    _reconcile_cap_sanity,
    build_migration_plan,
    commit_migration,
    find_tab_files,
)
from sda.db.schema import initialize_database


HEADER = [
    "Player Name*",
    "Pos.*",
    "Free Agent Year",
    "Move Type*",
    "IL",
    "Dropped*",
    "Year added*",
    "Player Contract Years*",
    "Drop Penalty Years",
    "Drop Year",
]


def _write_csv(path: Path, rows: list[list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as csv_file:
        csv.writer(csv_file).writerows(rows)


def _build_fixture(
    root: Path,
    *,
    move_type: str = "DFA",
    dropped: str = "No",
    penalty_years: str = "",
    drop_year: str = "",
) -> tuple[Path, Path, Path]:
    source_dir = root / "exports"
    data_dir = root / "data"
    database_path = root / "sda.duckdb"
    initialize_database(database_path)
    with open_database(database_path) as connection:
        for index, tab in enumerate(REQUIRED_TABS[:10]):
            connection.execute(
                "INSERT INTO teams (team_id, team_name, manager) VALUES (?, ?, ?)",
                [f"team-{index}", f"Team {index}", tab],
            )
        connection.execute(
            "INSERT INTO players (fantrax_id, name) VALUES ('player-1', 'Player One')"
        )

    alias_path = data_dir / "player_aliases.json"
    alias_path.parent.mkdir(parents=True, exist_ok=True)
    alias_path.write_text(json.dumps({"player one": "player-1"}), encoding="utf-8")

    for tab in REQUIRED_TABS[:10]:
        path = source_dir / f"SDA - Major League Contract Tracker (March 19 Snapshot) - {tab}.csv"
        rows = [["Tracker export"], [], [], HEADER]
        if tab == "Boe":
            rows.append(
                [
                    "Player One",
                    "OF",
                    "2028",
                    move_type,
                    "No",
                    dropped,
                    "2026",
                    "2",
                    penalty_years,
                    drop_year,
                ]
            )
        _write_csv(path, rows)

    _write_csv(
        source_dir / "SDA - Major League Contract Tracker (March 19 Snapshot) - League History.csv",
        [["intentionally excluded malformed export"]],
    )

    _write_csv(
        source_dir / "SDA - Major League Contract Tracker (March 19 Snapshot) - Salary Cap Tracking.csv",
        [["malformed excluded tab"], ["must not be opened"]],
    )

    roster_dir = data_dir / "raw" / "2026-03-19"
    roster_dir.mkdir(parents=True, exist_ok=True)
    (roster_dir / "team_rosters.json").write_text(
        json.dumps(
            {
                "rosters": {
                    **{
                        f"team-{index}": {
                            "teamName": f"Team {index}",
                            "rosterItems": (
                                [{"id": "player-1", "status": "MINORS", "position": "OF"}]
                                if index == 0
                                else [{"id": "orphan-player", "status": "ACTIVE", "position": "P"}]
                                if index == 1
                                else []
                            ),
                        }
                        for index in range(10)
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    return source_dir, database_path, data_dir


def test_missing_tabs_are_listed_exactly(tmp_path: Path) -> None:
    """Fail preflight with every missing required tab identified."""
    tabs, missing = find_tab_files(tmp_path)

    assert tabs == {}
    assert missing == list(REQUIRED_TABS)


def test_dfa_row_is_audited_without_contract_or_drop_events(tmp_path: Path) -> None:
    """DFA planning placeholders create no ledger events, even when marked dropped."""
    source_dir, database_path, data_dir = _build_fixture(
        tmp_path,
        move_type="DFA",
        dropped="Yes",
        penalty_years="0.5",
        drop_year="2027",
    )
    plan = build_migration_plan(source_dir, database_path, data_dir=data_dir, season=2027)

    assert plan.events == []
    assert plan.minors_audit
    assert "Dropped=Yes ignored" in plan.minors_audit[0]
    assert plan.matched_player_rows == 1
    assert plan.fantrax_orphans == ["Team 1: orphan-player"]
    assert plan.sheet_orphans == []
    assert "PLAYER MATCH RATE" in plan.render()
    assert "DFA / MINORS AUDIT" in plan.render()
    assert "CAP SANITY (78 YEARS + FANTRAX IL RELIEF)" in plan.render()
    assert plan.cap_report[0].startswith("Team 0: 0 committed / 78 allowed")
    assert not plan.cap_errors


def test_commit_rejects_planned_minors_signing(tmp_path: Path) -> None:
    """Defense in depth rejects minors SIGNED/EXTENDED events in hand-built plans."""
    _, database_path, _ = _build_fixture(tmp_path, move_type="Draft")
    plan = MigrationPlan(batch_id="bad-minors", season=2027)
    plan.events = [
        MigrationEvent(
            ts=datetime(2027, 1, 1),
            team_id="team-0",
            fantrax_id="player-1",
            event_type="SIGNED",
            years=2,
            fa_year=2029,
            roster_level="minors",
            note="invalid fixture",
            migration_key="bad-minors:1",
        )
    ]

    import pytest

    with pytest.raises(ValueError, match="cannot sign or extend a minor-league player"):
        commit_migration(plan, database_path)


def test_commit_is_idempotent_by_batch_marker(tmp_path: Path) -> None:
    """Repeated commits of the same source export append each event only once."""
    source_dir, database_path, data_dir = _build_fixture(tmp_path, move_type="Draft")
    plan = build_migration_plan(source_dir, database_path, data_dir=data_dir, season=2027)

    assert plan.can_commit
    assert commit_migration(plan, database_path) == len(plan.events)
    assert commit_migration(plan, database_path) == 0
    with open_database(database_path, read_only=True) as connection:
        assert connection.execute(
            "SELECT count(*) FROM contract_events WHERE source = 'migration'"
        ).fetchone()[0] == len(plan.events)
        assert connection.execute(
            "SELECT positions FROM players WHERE fantrax_id = 'player-1'"
        ).fetchone()[0] == "OF"
        assert connection.execute(
            "SELECT count(*) FROM contract_events WHERE event_type = 'CAP_TRADE'"
        ).fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM league_history").fetchone()[0] == 0


def test_cap_sanity_allows_il_relief_and_blocks_overage() -> None:
    """The cap limit is 78 plus Fantrax IL slots, not a sheet total."""
    plan = MigrationPlan(batch_id="cap-check", season=2027)
    plan.il_slots_by_team = {"team-1": 1}
    plan.events = [
        MigrationEvent(
            ts=datetime(2026, 1, 1),
            team_id="team-1",
            fantrax_id=f"player-{index}",
            event_type="SIGNED",
            years=1,
            fa_year=2028,
            roster_level="MLB",
            note="migration fixture",
            migration_key=f"row-{index}",
        )
        for index in range(79)
    ]

    _reconcile_cap_sanity(plan, {"team-1": "Team One"})

    assert plan.cap_report == ["Team One: 79 committed / 79 allowed (78 base + 1 Fantrax IL slot(s))"]
    assert plan.cap_errors == []
    assert plan.can_commit

    plan.events.append(
        MigrationEvent(
            ts=datetime(2026, 1, 1),
            team_id="team-1",
            fantrax_id="player-79",
            event_type="SIGNED",
            years=1,
            fa_year=2028,
            roster_level="MLB",
            note="migration fixture",
            migration_key="row-79",
        )
    )
    plan.cap_report.clear()
    _reconcile_cap_sanity(plan, {"team-1": "Team One"})

    assert plan.cap_errors == ["Team One: 80 committed years exceeds 79 (78 base + 1 Fantrax IL slot(s))"]
    assert not plan.can_commit


def test_cap_sanity_uses_only_fantrax_il_slots() -> None:
    """Sheet IL flags do not create relief; only Fantrax slot counts do."""
    plan = MigrationPlan(batch_id="il-cap", season=2027)
    plan.events = [
        MigrationEvent(
            ts=datetime(2026, 1, 1),
            team_id="team-1",
            fantrax_id=f"player-{index}",
            event_type="SIGNED",
            years=1,
            fa_year=2028,
            roster_level="MLB",
            note="migration fixture; Sheet IL=Yes",
            migration_key=f"row-{index}",
            sheet_il=True,
        )
        for index in range(79)
    ]
    plan.il_slots_by_team = {"team-1": 0}

    _reconcile_cap_sanity(plan, {"team-1": "Team One"})

    assert plan.cap_errors == ["Team One: 79 committed years exceeds 78 (78 base + 0 Fantrax IL slot(s))"]