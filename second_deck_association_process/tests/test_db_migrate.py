"""Tests for Google Sheets migration planning and commit gating."""

import csv
import json
from datetime import datetime
from pathlib import Path

import pytest

from sda.db.connection import open_database
from sda.db.migrate import (
    REQUIRED_TABS,
    MigrationEvent,
    MigrationPlan,
    _fuzzy_player_match,
    _reconcile_cap_sanity,
    build_migration_plan,
    commit_migration,
    find_tab_files,
)
from sda.db.schema import initialize_database
from sda.fantrax.normalize import normalize_alias


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
    fa_year: str = "2028",
    contract_years: str = "2",
    sheet_player_name: str = "Player One",
    db_player_name: str = "Player One",
    roster_status: str = "ACTIVE",
    roster_team_index: int = 0,
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
            "INSERT INTO players (fantrax_id, name) VALUES (?, ?)",
            ["player-1", db_player_name],
        )

    alias_path = data_dir / "player_aliases.json"
    alias_path.parent.mkdir(parents=True, exist_ok=True)
    aliases = {normalize_alias(db_player_name): "player-1"} if db_player_name == sheet_player_name else {}
    alias_path.write_text(json.dumps(aliases), encoding="utf-8")

    for tab in REQUIRED_TABS[:10]:
        path = source_dir / f"SDA - Major League Contract Tracker (March 19 Snapshot) - {tab}.csv"
        rows = [["Tracker export"], [], [], HEADER]
        if tab == "Boe":
            rows.append(
                [
                    sheet_player_name,
                    "OF",
                    fa_year,
                    move_type,
                    "No",
                    dropped,
                    "2026",
                    contract_years,
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
                                ([{"id": "player-1", "status": roster_status, "position": "OF"}]
                                 if index == roster_team_index else [])
                                + ([{"id": "orphan-player", "status": "ACTIVE", "position": "P"}]
                                   if index == 1 and roster_team_index != 1 else [])
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


def test_commit_accepts_official_multiyear_minors_contract(tmp_path: Path) -> None:
    """An official multi-year deal stays valid while its player is rostered in minors."""
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
    with open_database(database_path) as connection:
        connection.execute(
            "CREATE OR REPLACE VIEW current_contracts AS "
            "SELECT * FROM contract_events WHERE event_type = 'SIGNED' AND roster_level <> 'minors'"
        )

    assert commit_migration(plan, database_path) == 1
    with open_database(database_path, read_only=True) as connection:
        assert connection.execute(
            "SELECT roster_level FROM current_contracts WHERE fantrax_id = 'player-1'"
        ).fetchone() == ("minors",)


def test_default_drop_year_derives_constitution_penalty_without_editing_csv(tmp_path: Path) -> None:
    """Use the commissioner-provided year and half-remaining-years schedule for a missing penalty."""
    source_dir, database_path, data_dir = _build_fixture(
        tmp_path,
        move_type="Draft",
        dropped="Yes",
        roster_team_index=-1,
    )
    plan = build_migration_plan(
        source_dir,
        database_path,
        data_dir=data_dir,
        season=2027,
        drop_year_default=2026,
    )

    dropped = next(event for event in plan.events if event.event_type == "DROPPED")
    assert dropped.ts == datetime(2026, 1, 1)
    assert dropped.years == 1.0
    assert "drop_year_default=2026" in dropped.note
    assert "drop_penalty=constitution half remaining years" in dropped.note
    assert not any("Dropped=Yes but penalty" in item.reason for item in plan.exceptions)
    assert plan.can_commit


def test_final_contract_year_is_excluded_from_multi_year_migration(tmp_path: Path) -> None:
    source_dir, database_path, data_dir = _build_fixture(
        tmp_path,
        move_type="Draft",
        dropped="Yes",
        fa_year="2027",
        contract_years="1",
        roster_team_index=-1,
    )

    plan = build_migration_plan(
        source_dir,
        database_path,
        data_dir=data_dir,
        season=2027,
        drop_year_default=2026,
    )

    assert plan.events == []
    assert any("excluded from multi-year migration" in item for item in plan.manual_resolution_audit)


def test_explicit_drop_year_and_penalty_override_default(tmp_path: Path) -> None:
    source_dir, database_path, data_dir = _build_fixture(
        tmp_path,
        move_type="Draft",
        dropped="Yes",
        penalty_years="0.5",
        drop_year="2025",
        roster_team_index=-1,
    )

    plan = build_migration_plan(
        source_dir,
        database_path,
        data_dir=data_dir,
        season=2027,
        drop_year_default=2026,
    )

    dropped_event = next(event for event in plan.events if event.event_type == "DROPPED")
    assert dropped_event.ts == datetime(2025, 1, 1)
    assert dropped_event.years == 0.5
    assert "drop_year_default=" not in dropped_event.note


def test_drop_year_override_is_part_of_migration_batch_id(tmp_path: Path) -> None:
    source_dir, database_path, data_dir = _build_fixture(
        tmp_path,
        move_type="Draft",
        dropped="Yes",
    )
    first = build_migration_plan(
        source_dir, database_path, data_dir=data_dir, season=2027, drop_year_default=2026
    )
    second = build_migration_plan(
        source_dir, database_path, data_dir=data_dir, season=2027, drop_year_default=2025
    )

    assert first.batch_id != second.batch_id


def test_fuzzy_match_resolves_bischette_to_bichette_and_audits_it(tmp_path: Path) -> None:
    source_dir, database_path, data_dir = _build_fixture(
        tmp_path,
        move_type="Draft",
        sheet_player_name="Bo Bischette",
        db_player_name="Bichette, Bo",
    )

    plan = build_migration_plan(source_dir, database_path, data_dir=data_dir, season=2027)

    signed_event = next(event for event in plan.events if event.event_type == "SIGNED")
    assert signed_event.fantrax_id == "player-1"
    assert "player_match=fuzzy" in signed_event.note
    assert any("Bo Bischette -> Bichette, Bo" in match for match in plan.fuzzy_matches)


def test_fuzzy_match_can_be_limited_to_current_team_roster() -> None:
    players = {
        "player-1": "Bichette, Bo",
        "player-2": "Bichette, Bo Jr.",
    }

    match = _fuzzy_player_match("Bo Bischette", players, allowed_ids={"player-1"})

    assert match is not None
    assert match[0] == "player-1"


def test_current_fantrax_owner_overrides_sheet_tab_and_stale_drop_flag(tmp_path: Path) -> None:
    source_dir, database_path, data_dir = _build_fixture(
        tmp_path,
        move_type="Waiver",
        dropped="Yes",
        roster_status="ACTIVE",
        roster_team_index=1,
    )
    resolutions_path = data_dir / "resolutions.json"
    resolutions_path.write_text(
        json.dumps({"rows": {"Boe:5": {"player_id": "player-1"}}}),
        encoding="utf-8",
    )

    plan = build_migration_plan(
        source_dir,
        database_path,
        data_dir=data_dir,
        season=2027,
        manual_resolutions_path=resolutions_path,
    )

    assert len(plan.events) == 1
    assert plan.events[0].team_id == "team-1"
    assert plan.events[0].event_type == "SIGNED"


def test_manual_confirmed_drop_is_matched_but_excluded_from_ledger(tmp_path: Path) -> None:
    source_dir, database_path, data_dir = _build_fixture(
        tmp_path,
        move_type="Draft",
        sheet_player_name="Historical Player",
        db_player_name="Different Fantrax Player",
    )
    resolutions_path = data_dir / "resolutions.json"
    resolutions_path.write_text(
        json.dumps(
            {
                "rows": {
                    "Boe:5": {
                        "action": "exclude",
                        "reason": "commissioner-confirmed dropped",
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    plan = build_migration_plan(
        source_dir,
        database_path,
        data_dir=data_dir,
        season=2027,
        manual_resolutions_path=resolutions_path,
    )

    assert plan.matched_player_rows == 1
    assert plan.events == []
    assert not plan.exceptions
    assert any("commissioner-confirmed dropped" in item for item in plan.manual_resolution_audit)


def test_fantrax_minor_roster_suppresses_contract_for_nonminor_move_label(tmp_path: Path) -> None:
    source_dir, database_path, data_dir = _build_fixture(
        tmp_path,
        move_type="Waiver - Minors",
        contract_years="1",
        roster_status="MINORS",
    )

    plan = build_migration_plan(source_dir, database_path, data_dir=data_dir, season=2027)

    assert plan.events == []
    assert plan.minors_audit
    assert not plan.exceptions


def test_official_multiyear_deal_on_current_minors_roster_is_migrated(tmp_path: Path) -> None:
    source_dir, database_path, data_dir = _build_fixture(
        tmp_path,
        move_type="Draft",
        contract_years="3",
        roster_status="MINORS",
    )

    plan = build_migration_plan(source_dir, database_path, data_dir=data_dir, season=2027)

    assert len(plan.events) == 1
    assert plan.events[0].event_type == "SIGNED"
    assert plan.events[0].roster_level == "minors"
    assert plan.events[0].years == 3


def test_il_roster_level_uses_mlb_contract_level(tmp_path: Path) -> None:
    source_dir, database_path, data_dir = _build_fixture(
        tmp_path,
        move_type="Draft",
        contract_years="2",
        roster_status="INJURED_RESERVE",
    )

    plan = build_migration_plan(source_dir, database_path, data_dir=data_dir, season=2027)

    assert len(plan.events) == 1
    assert plan.events[0].roster_level == "MLB"


def test_manual_no_contract_override_ignores_minor_placeholder_years(tmp_path: Path) -> None:
    source_dir, database_path, data_dir = _build_fixture(
        tmp_path,
        move_type="NULL",
        contract_years="5",
        roster_status="MINORS",
    )
    resolutions_path = data_dir / "resolutions.json"
    resolutions_path.write_text(
        json.dumps(
            {
                "rows": {
                    "Boe:5": {
                        "player_id": "player-1",
                        "contract_status": "none",
                        "note": "Minor-league placeholder term, not an official contract.",
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    plan = build_migration_plan(
        source_dir,
        database_path,
        data_dir=data_dir,
        season=2027,
        manual_resolutions_path=resolutions_path,
    )

    assert plan.events == []
    assert not plan.exceptions
    assert any("no official multi-year contract" in item for item in plan.minors_audit)


def test_duplicate_contract_rows_without_a_current_owner_block_commit(tmp_path: Path) -> None:
    source_dir, database_path, data_dir = _build_fixture(
        tmp_path,
        move_type="Draft",
        roster_team_index=-1,
    )
    maloun_path = source_dir / "SDA - Major League Contract Tracker (March 19 Snapshot) - Maloun.csv"
    with maloun_path.open("a", encoding="utf-8", newline="") as csv_file:
        csv.writer(csv_file).writerow(
            ["Player One", "OF", "2028", "Draft", "No", "No", "2026", "2", "", ""]
        )

    plan = build_migration_plan(source_dir, database_path, data_dir=data_dir, season=2027)

    duplicate_errors = [item for item in plan.exceptions if "Multiple contract rows resolve" in item.reason]
    assert len(duplicate_errors) == 2
    assert not plan.can_commit


def test_blank_move_type_up_to_three_years_is_waiver_and_fa_year_is_derived(tmp_path: Path) -> None:
    source_dir, database_path, data_dir = _build_fixture(
        tmp_path,
        move_type="",
        contract_years="3",
        fa_year="2035",
    )

    plan = build_migration_plan(source_dir, database_path, data_dir=data_dir, season=2027)

    signed_event = next(event for event in plan.events if event.event_type == "SIGNED")
    assert signed_event.fa_year == 2029
    assert "acquisition_type=waiver" in signed_event.note
    assert "ignored_sheet_fa_year=2035" in signed_event.note
    assert not any("Move Type is blank" in item.reason for item in plan.exceptions)


def test_blank_move_type_over_three_years_remains_an_exception(tmp_path: Path) -> None:
    source_dir, database_path, data_dir = _build_fixture(
        tmp_path,
        move_type="",
        contract_years="4",
    )

    plan = build_migration_plan(source_dir, database_path, data_dir=data_dir, season=2027)

    assert any("blank and contract exceeds" in item.reason for item in plan.exceptions)


@pytest.mark.parametrize("dropped", ["No", "Yes"])
def test_one_year_contract_is_excluded_from_multiyear_migration(
    tmp_path: Path,
    dropped: str,
) -> None:
    source_dir, database_path, data_dir = _build_fixture(
        tmp_path,
        move_type="Draft",
        contract_years="1",
        dropped=dropped,
    )

    plan = build_migration_plan(source_dir, database_path, data_dir=data_dir, season=2027)

    assert plan.events == []
    assert not plan.exceptions
    assert any("excluded from multi-year migration" in item for item in plan.manual_resolution_audit)


def test_missing_contract_years_default_to_one_year_and_are_excluded(tmp_path: Path) -> None:
    source_dir, database_path, data_dir = _build_fixture(
        tmp_path,
        move_type="Draft",
        contract_years="",
    )

    plan = build_migration_plan(source_dir, database_path, data_dir=data_dir, season=2027)

    assert plan.events == []
    assert not plan.exceptions
    assert any("defaulted to 2026 one-year contract" in item for item in plan.manual_resolution_audit)


def test_one_year_duplicate_is_ignored_and_two_year_contract_is_kept(tmp_path: Path) -> None:
    source_dir, database_path, data_dir = _build_fixture(
        tmp_path,
        move_type="Draft",
        contract_years="2",
    )
    maloun_path = source_dir / "SDA - Major League Contract Tracker (March 19 Snapshot) - Maloun.csv"
    with maloun_path.open("a", encoding="utf-8", newline="") as csv_file:
        csv.writer(csv_file).writerow(
            ["Player One", "OF", "2027", "Waiver", "No", "No", "2026", "1", "", ""]
        )

    plan = build_migration_plan(source_dir, database_path, data_dir=data_dir, season=2027)

    assert len(plan.events) == 1
    assert plan.events[0].years == 2
    assert plan.events[0].migration_key.startswith("Boe:")
    assert not plan.exceptions


@pytest.mark.parametrize(("contract_years", "expected_exception"), [("3", False), ("4", True)])
def test_unrostered_active_player_is_limited_to_three_years(
    tmp_path: Path,
    contract_years: str,
    expected_exception: bool,
) -> None:
    source_dir, database_path, data_dir = _build_fixture(
        tmp_path,
        move_type="Draft",
        contract_years=contract_years,
        sheet_player_name="Xander Bogaerts",
        db_player_name="Bogaerts, Xander",
    )
    roster_path = data_dir / "raw" / "2026-03-19" / "team_rosters.json"
    payload = json.loads(roster_path.read_text(encoding="utf-8"))
    payload["rosters"]["team-0"]["rosterItems"] = []
    roster_path.write_text(json.dumps(payload), encoding="utf-8")

    plan = build_migration_plan(source_dir, database_path, data_dir=data_dir, season=2027)

    violations = [item for item in plan.exceptions if "absent from the current Fantrax roster" in item.reason]
    assert bool(violations) is expected_exception


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