"""Focused tests for the deterministic league validation rules and engine."""

from datetime import date, datetime
from pathlib import Path

import pytest

from sda.db.connection import open_database
from sda.db.ledger import append_event, record_roster_snapshot
from sda.db.schema import initialize_database
from sda.validation import engine
from sda.validation.engine import run_all
from sda.validation.rules import (
    expected_dead_cap,
    v1_cap,
    v2_contract_bounds,
    v3_resign_tripwire,
    v4_duplicates,
    v5_coverage,
    v6_fa_year_stability,
    v7_drop_penalties,
    v8_minors_shuttle,
    v9_pool_freeze,
    v10_roster_max,
    v11_roster_min,
    v12_one_day_signing,
    v13_il_eligibility,
)


def _event(
    event_type: str,
    *,
    team_id: str = "t1",
    fantrax_id: str = "p1",
    ts: datetime = datetime(2027, 3, 1),
    years: float = 1,
    fa_year: int = 2028,
    note: str = "",
) -> dict[str, object]:
    return {
        "event_type": event_type,
        "team_id": team_id,
        "fantrax_id": fantrax_id,
        "ts": ts,
        "years": years,
        "fa_year": fa_year,
        "note": note,
    }


def test_v1_cap_relief_and_cap_trade_are_in_committed_total() -> None:
    assert v1_cap("t1", 79, 1)["passed"] is True
    assert v1_cap("t1", 80, 1)["passed"] is False


@pytest.mark.parametrize(
    ("acquisition", "years", "expected"),
    [("draft", 7, True), ("draft", 7.5, False), ("waiver", 3, True), ("waiver", 4, False)],
)
def test_v2_contract_bounds(acquisition: str, years: float, expected: bool) -> None:
    assert v2_contract_bounds(_event("SIGNED", years=years), acquisition)["passed"] is expected


def test_v3_re_sign_tripwire_exempts_annual_draft() -> None:
    events = [
        _event("SIGNED", ts=datetime(2026, 3, 1), years=5, fa_year=2031),
        _event("DROPPED", ts=datetime(2027, 5, 1), years=2, fa_year=2031),
        _event("SIGNED", ts=datetime(2027, 6, 1), years=4, fa_year=2031),
    ]
    assert len(v3_resign_tripwire(events, 2027, date(2027, 3, 13), date(2027, 3, 18))) == 1
    events[-1] = _event("SIGNED", ts=datetime(2027, 3, 15), years=4, fa_year=2031)
    assert v3_resign_tripwire(events, 2027, date(2027, 3, 13), date(2027, 3, 18)) == []


def test_v4_duplicate_and_v5_coverage() -> None:
    rosters = [
        {"team_id": "t1", "fantrax_id": "p1", "roster_level": "MLB"},
        {"team_id": "t2", "fantrax_id": "p1", "roster_level": "IL"},
    ]
    duplicate = v4_duplicates(rosters)
    assert len(duplicate) == 1 and duplicate[0]["passed"] is False
    assert v5_coverage("t1", "p1", {("t1", "p1")})["passed"] is True
    assert v5_coverage("t1", "p2", {("t1", "p1")})["passed"] is False


def test_v6_fa_year_changes_need_signing_or_extension_event() -> None:
    events = [
        _event("SIGNED", ts=datetime(2026, 1, 1), fa_year=2030),
        _event("DROPPED", ts=datetime(2027, 1, 1), years=1.5, fa_year=2031),
    ]
    assert len(v6_fa_year_stability(events)) == 1


def test_v7_constitution_drop_penalty_example_and_schedule() -> None:
    assert [expected_dead_cap(2.5, 2027, 2032, year) for year in range(2027, 2033)] == [
        2.5,
        2.0,
        1.5,
        1.0,
        0.0,
        0.0,
    ]
    correct = [
        _event("SIGNED", years=5, fa_year=2032),
        _event("DROPPED", ts=datetime(2027, 6, 1), years=2.5, fa_year=2032),
    ]
    incorrect = [correct[0], {**correct[1], "years": 2.0}]
    assert v7_drop_penalties(correct) == []
    assert len(v7_drop_penalties(incorrect)) == 1


def test_v8_minors_contract_counts_and_shuttle_review() -> None:
    result = v8_minors_shuttle(
        "t1",
        "p1",
        has_active_contract=True,
        included_in_cap=False,
        shuttle_dates=[date(2027, 4, 1), date(2027, 4, 8), date(2027, 4, 15)],
    )
    assert result["passed"] is False
    assert result["review_required"] is True


def test_v9_pool_freeze_and_roster_limits() -> None:
    assert v9_pool_freeze("t1", "p1", 2027, date(2027, 4, 26), 2027, date(2027, 4, 25))["passed"] is False
    assert v10_roster_max("t1", 26, 15, 8)["passed"] is True
    assert v10_roster_max("t1", 27, 15, 9)["passed"] is False
    assert v11_roster_min("t1", 25, 26)["passed"] is False


def test_v12_matches_timestamped_announcement_within_one_day() -> None:
    added_at = datetime(2027, 5, 2, 12)
    announcement = {"team_id": "t1", "fantrax_id": "p1", "announced_at": datetime(2027, 5, 3, 12)}
    assert v12_one_day_signing("t1", "p1", added_at, [announcement])["passed"] is True
    assert v12_one_day_signing("t1", "p1", added_at, [])["passed"] is False


def test_v13_slot_eligibility_and_unknown_data_handling() -> None:
    assert v13_il_eligibility("t1", "p1", "IL", False)["passed"] is False
    assert v13_il_eligibility("t1", "p2", "IL", True)["passed"] is True
    unknown = v13_il_eligibility("t1", "p3", "IL", None)
    assert unknown["passed"] is True and unknown["review_required"] is True
    assert v13_il_eligibility("t1", "p4", "IL", True, is_minor=True)["passed"] is False


def test_fixture_league_fails_each_rule_exactly_once() -> None:
    violations = [
        v1_cap("t1", 80, 1),
        v2_contract_bounds(_event("SIGNED", years=4), "waiver"),
        *v3_resign_tripwire(
            [
                _event("SIGNED", ts=datetime(2026, 1, 1), years=5, fa_year=2032),
                _event("DROPPED", ts=datetime(2027, 4, 1), years=2.5, fa_year=2032),
                _event("SIGNED", ts=datetime(2027, 5, 1), years=4, fa_year=2031),
            ],
            2027,
            date(2027, 3, 13),
            date(2027, 3, 18),
        ),
        *v4_duplicates(
            [
                {"team_id": "t1", "fantrax_id": "p1", "roster_level": "MLB"},
                {"team_id": "t2", "fantrax_id": "p1", "roster_level": "MLB"},
            ]
        ),
        v5_coverage("t1", "p1", set()),
        *v6_fa_year_stability(
            [
                _event("SIGNED", ts=datetime(2026, 1, 1), fa_year=2030),
                _event("DROPPED", ts=datetime(2027, 1, 1), years=1.5, fa_year=2031),
            ]
        ),
        *v7_drop_penalties(
            [
                _event("SIGNED", years=5, fa_year=2032),
                _event("DROPPED", ts=datetime(2027, 6, 1), years=2, fa_year=2032),
            ]
        ),
        v8_minors_shuttle("t1", "p1", has_active_contract=True, included_in_cap=False),
        v9_pool_freeze("t1", "p1", 2027, date(2027, 4, 26), 2027, date(2027, 4, 25)),
        v10_roster_max("t1", 27, 15, 8),
        v11_roster_min("t1", 25, 26),
        v12_one_day_signing("t1", "p1", datetime(2027, 5, 2), []),
        v13_il_eligibility("t1", "p1", "IL", False),
    ]
    failed_rule_ids = [result["rule_id"] for result in violations if not result["passed"]]
    assert failed_rule_ids == [f"V{number}" for number in range(1, 14)]


def test_validation_cli_returns_nonzero_on_any_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        engine,
        "run_all",
        lambda _season, _database: {
            "per_team": {"t1": [{"rule_id": "V1", "passed": False, "detail": "over cap"}]},
            "league_results": [],
            "summary": {"passed": 0, "failed": 1, "actions_taken": []},
        },
    )
    assert engine.main(["--season", "2027", "--database", "unused.duckdb"]) == 1


def _engine_database(path: Path, *, real_life_il: bool | None = False) -> None:
    initialize_database(path)
    with open_database(path) as connection:
        connection.execute("INSERT INTO teams (team_id, team_name) VALUES ('t1', 'Team One')")
        connection.execute(
            "INSERT INTO players (fantrax_id, name, real_draft_year, real_life_il) "
            "VALUES ('p1', 'Player One', 2026, ?)",
            [real_life_il],
        )
        connection.execute(
            """INSERT INTO season_config (
                   season, freeze_date, draft_start, draft_end, min_mlb_roster, cap_years
               ) VALUES (2027, DATE '2027-04-25', DATE '2027-03-13', DATE '2027-03-18', NULL, 78)"""
        )


def test_engine_all_pass_fixture_reports_every_enabled_rule(tmp_path: Path) -> None:
    database = tmp_path / "pass.duckdb"
    _engine_database(database)
    record_roster_snapshot(
        date(2027, 3, 13),
        {"t1": {"mlb_roster": [{"id": "baseline", "roster_level": "MLB"}]}},
        database,
    )
    record_roster_snapshot(
        date(2027, 3, 14),
        {"t1": {"mlb_roster": [{"id": "p1", "roster_level": "MLB"}]}},
        database,
    )
    append_event(
        "t1", "p1", "SIGNED", 1, 2028, "manual",
        ts=datetime(2027, 3, 13), note="move_type=Draft", database_path=database,
    )
    with open_database(database) as connection:
        connection.execute(
            "INSERT INTO announcements (team_id, fantrax_id, announced_at, raw_message) "
            "VALUES ('t1', 'p1', TIMESTAMP '2027-03-14 00:00:00', 'signed')"
        )

    report = run_all(2027, database, now=datetime(2027, 3, 16, 12))
    assert report["summary"]["failed"] == 0
    assert report["summary"]["actions_taken"] == []
    rule_ids = {result["rule_id"] for result in report["per_team"]["t1"]}
    assert rule_ids == {f"V{number}" for number in range(1, 11)} | {"V12", "V13"}


def test_engine_defaults_unannounced_add_once_and_exits_nonzero_summary(tmp_path: Path) -> None:
    database = tmp_path / "default.duckdb"
    _engine_database(database)
    record_roster_snapshot(
        date(2027, 3, 13),
        {"t1": {"mlb_roster": [{"id": "baseline", "roster_level": "MLB"}]}},
        database,
    )
    record_roster_snapshot(
        date(2027, 3, 14),
        {"t1": {"mlb_roster": [{"id": "p1", "roster_level": "MLB"}]}},
        database,
    )

    first = run_all(2027, database, now=datetime(2027, 3, 16, 12))
    second = run_all(2027, database, now=datetime(2027, 3, 16, 12))
    assert first["summary"]["failed"] == 1
    assert first["summary"]["actions_taken"][0]["event_type"] == "DEFAULTED_1YR"
    assert second["summary"]["actions_taken"] == []
    with open_database(database, read_only=True) as connection:
        defaults = connection.execute(
            "SELECT count(*) FROM contract_events WHERE event_type = 'DEFAULTED_1YR'"
        ).fetchone()[0]
    assert defaults == 1


def test_engine_does_not_apply_one_day_default_to_minors_add(tmp_path: Path) -> None:
    database = tmp_path / "minors.duckdb"
    _engine_database(database)
    record_roster_snapshot(
        date(2027, 3, 13),
        {"t1": {"minors_roster": [{"id": "baseline", "roster_level": "minors"}]}},
        database,
    )
    record_roster_snapshot(
        date(2027, 3, 14),
        {"t1": {"minors_roster": [{"id": "p1", "roster_level": "minors"}]}},
        database,
    )

    report = run_all(2027, database, now=datetime(2027, 3, 16, 12))
    assert report["summary"]["actions_taken"] == []
    assert not any(result["rule_id"] == "V12" and not result["passed"] for result in report["per_team"]["t1"])


def test_engine_failure_fixture_reports_each_rule_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    database = tmp_path / "failures.duckdb"
    initialize_database(database)
    with open_database(database) as connection:
        connection.executemany(
            "INSERT INTO teams (team_id, team_name) VALUES (?, ?)",
            [("t1", "Team One"), ("t2", "Team Two"), ("t3", "Team Three")],
        )
        player_ids = [f"p{number}" for number in range(1, 30)] + [
            "q3",
            "q6",
            "q7",
            *(f"q{number}" for number in range(10, 22)),
        ]
        connection.executemany(
            "INSERT INTO players (fantrax_id, name, real_draft_year, real_life_il) VALUES (?, ?, ?, ?)",
            [
                (player_id, player_id, 2027 if player_id == "p9" else 2026, player_id == "p1")
                for player_id in player_ids
            ],
        )
        connection.execute(
            """INSERT INTO season_config (
                   season, freeze_date, draft_start, draft_end, min_mlb_roster, cap_years
               ) VALUES (2027, DATE '2027-04-25', DATE '2027-03-13', DATE '2027-03-18', 2, 78)"""
        )
        events: list[tuple[datetime, str, str, str, float, int, str]] = []
        for number in range(1, 29):
            if number in {5, 12}:
                continue
            events.append(
                (datetime(2027, 3, 1), "t1", f"p{number}", "SIGNED", 1, 2028, "move_type=Draft")
            )
        events.append((datetime(2027, 3, 1), "t2", "p1", "SIGNED", 1, 2028, "move_type=Draft"))
        events.append((datetime(2027, 3, 1), "t2", "p29", "SIGNED", 1, 2028, "move_type=Draft"))
        events.extend(
            [
                (datetime(2026, 3, 1), "t1", "q3", "SIGNED", 6, 2032, "move_type=Draft"),
                (datetime(2027, 5, 1), "t1", "q3", "DROPPED", 2.5, 2032, ""),
                (datetime(2027, 6, 1), "t1", "q3", "SIGNED", 4, 2031, ""),
                (datetime(2026, 3, 1), "t1", "q6", "SIGNED", 4, 2030, "move_type=Draft"),
                (datetime(2027, 5, 1), "t1", "q6", "DROPPED", 2, 2031, ""),
                (datetime(2026, 3, 1), "t1", "q7", "SIGNED", 5, 2031, "move_type=Draft"),
                (datetime(2027, 5, 1), "t1", "q7", "DROPPED", 1.5, 2031, ""),
            ]
        )
        for number in range(10, 22):
            events.append(
                (datetime(2026, 3, 1), "t1", f"q{number}", "SIGNED", 7, 2033, "move_type=Draft")
            )
        connection.executemany(
            """INSERT INTO contract_events (
                   ts, team_id, fantrax_id, event_type, years, fa_year, source, note
               ) VALUES (?, ?, ?, ?, ?, ?, 'manual', ?)""",
            events,
        )

        announcement_rows = []
        for team_id, ids in (("t1", [f"p{number}" for number in range(1, 29)]), ("t2", ["p1", "p29"])):
            for player_id in ids:
                if player_id == "p12":
                    continue
                announcement_rows.append(
                    (f"{team_id}-{player_id}", team_id, player_id, datetime(2027, 5, 1), "signing")
                )
        connection.executemany(
            """INSERT INTO announcements (
                   message_id, team_id, fantrax_id, announced_at, raw_message
               ) VALUES (?, ?, ?, ?, ?)""",
            announcement_rows,
        )

    record_roster_snapshot(
        date(2027, 4, 30),
        {
            "t1": {"mlb_roster": [{"id": "baseline1", "roster_level": "MLB"}]},
            "t2": {"mlb_roster": [{"id": "baseline2", "roster_level": "MLB"}]},
        },
        database,
    )
    team_one_roster = [
        {"id": f"p{number}", "roster_level": "MLB"}
        for number in range(1, 29)
        if number not in {8, 13}
    ]
    team_one_roster.extend(
        [
            {"id": "p8", "roster_level": "minors"},
            {"id": "p13", "roster_level": "IL"},
        ]
    )
    team_two_roster = [
        {"id": "p1", "roster_level": "IL"},
        {"id": "p29", "roster_level": "MLB"},
    ]
    record_roster_snapshot(
        date(2027, 5, 1),
        {"t1": {"mlb_roster": team_one_roster}, "t2": {"mlb_roster": team_two_roster}},
        database,
    )

    original_v8 = engine.rules.v8_minors_shuttle

    def exclude_one_minor_from_cap(
        team_id: str,
        fantrax_id: str,
        **kwargs: object,
    ) -> dict[str, object]:
        if fantrax_id == "p8":
            kwargs["included_in_cap"] = False
        return original_v8(team_id, fantrax_id, **kwargs)

    monkeypatch.setattr(engine.rules, "v8_minors_shuttle", exclude_one_minor_from_cap)
    report = run_all(2027, database, now=datetime(2027, 5, 4, 12))
    failures = [
        result
        for team_results in report["per_team"].values()
        for result in team_results
        if not result["passed"]
    ]
    failed_rule_ids = sorted(result["rule_id"] for result in failures)
    assert failed_rule_ids == sorted(f"V{number}" for number in range(1, 14))