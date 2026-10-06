"""Append-only ledger and cap calculation tests."""

from datetime import datetime
from pathlib import Path

import pytest

from sda.db.connection import open_database
from sda.db.ledger import (
    AppendOnlyViolation,
    append_event,
    current_contracts,
    delete_contract_event,
    team_cap_committed,
    update_contract_event,
)
from sda.db.schema import initialize_database
from sda.db.snapshots import export_text_snapshot, restore_text_snapshot


def test_drop_leaves_scheduled_dead_cap_but_no_current_contract(tmp_path: Path) -> None:
    """Replay a signed deal and drop through the constitution's dead-cap schedule."""
    database_path = tmp_path / "sda.duckdb"
    initialize_database(database_path)
    with open_database(database_path) as connection:
        connection.execute("INSERT INTO teams (team_id, team_name) VALUES ('t1', 'Team One')")
        connection.execute("INSERT INTO players (fantrax_id, name) VALUES ('p1', 'Player One')")

    append_event(
        "t1", "p1", "SIGNED", 5, 2032, "manual",
        ts=datetime(2027, 2, 1), database_path=database_path,
    )
    assert team_cap_committed("t1", 2027, database_path) == 5.0
    append_event(
        "t1", "p1", "DROPPED", 2.5, 2032, "manual",
        ts=datetime(2027, 6, 1), database_path=database_path,
    )

    assert current_contracts(2027, database_path) == []
    assert [team_cap_committed("t1", year, database_path) for year in range(2027, 2033)] == [
        2.5,
        2.0,
        1.5,
        1.0,
        0.0,
        0.0,
    ]


def test_cap_trade_adjustments_change_room_and_are_season_scoped(tmp_path: Path) -> None:
    """A negative sent-cap event reduces effective room for that season."""
    database_path = tmp_path / "sda.duckdb"
    initialize_database(database_path)
    with open_database(database_path) as connection:
        connection.execute("INSERT INTO teams (team_id, team_name) VALUES ('t1', 'Team One')")

    append_event(
        "t1", None, "CAP_TRADE", -2.5, None, "manual",
        ts=datetime(2027, 4, 1), database_path=database_path,
    )

    assert team_cap_committed("t1", 2027, database_path) == 2.5
    assert team_cap_committed("t1", 2028, database_path) == 0.0


def test_form_event_persists_submission_provenance(tmp_path: Path) -> None:
    """Retain Forms UUID, acquisition type, and submitted time on the immutable ledger row."""
    database_path = tmp_path / "form-event.duckdb"
    initialize_database(database_path)
    with open_database(database_path) as connection:
        connection.execute("INSERT INTO teams (team_id, team_name) VALUES ('t1', 'Team One')")
        connection.execute("INSERT INTO players (fantrax_id, name) VALUES ('p1', 'Player One')")

    append_event(
        "t1",
        "p1",
        "SIGNED",
        3,
        2030,
        "form",
        ts=datetime(2027, 3, 15, 12),
        form_ref="123e4567-e89b-12d3-a456-426614174000",
        acquisition_type="drafted",
        announced_at=datetime(2027, 3, 15, 12),
        database_path=database_path,
    )

    with open_database(database_path, read_only=True) as connection:
        assert connection.execute(
            "SELECT source, form_ref, acquisition_type, announced_at FROM current_contracts"
        ).fetchone() == (
            "form",
            "123e4567-e89b-12d3-a456-426614174000",
            "drafted",
            datetime(2027, 3, 15, 12),
        )


def test_official_minors_contract_counts_toward_cap_and_dead_cap(tmp_path: Path) -> None:
    """An official multi-year deal remains cap-bearing in a minors slot."""
    database_path = tmp_path / "sda.duckdb"
    initialize_database(database_path)
    with open_database(database_path) as connection:
        connection.execute("INSERT INTO teams (team_id, team_name) VALUES ('t1', 'Team One')")
        connection.execute("INSERT INTO players (fantrax_id, name) VALUES ('p1', 'Official Deal')")

    append_event(
        "t1", "p1", "SIGNED", 5, 2032, "manual",
        ts=datetime(2027, 1, 1), roster_level="minors", database_path=database_path,
    )

    contracts = current_contracts(2027, database_path)
    assert [(contract["fantrax_id"], contract["roster_level"]) for contract in contracts] == [("p1", "minors")]
    assert team_cap_committed("t1", 2027, database_path) == 5.0
    from sda.db.ledger import record_roster_snapshot

    record_roster_snapshot(
        datetime(2027, 4, 1).date(),
        {"t1": {"minors_roster": [{"id": "p1", "roster_level": "minors"}]}},
        database_path,
    )
    assert team_cap_committed("t1", 2027, database_path) == 5.0

    append_event(
        "t1", "p1", "DROPPED", 2.5, 2032, "manual",
        ts=datetime(2027, 6, 1), roster_level="minors", database_path=database_path,
    )
    assert current_contracts(2027, database_path) == []
    assert team_cap_committed("t1", 2027, database_path) == 2.5


def test_contract_events_have_no_update_or_delete_path() -> None:
    """The ledger API explicitly rejects in-place mutation and deletion."""
    with pytest.raises(AppendOnlyViolation):
        update_contract_event(1, note="rewritten")
    with pytest.raises(AppendOnlyViolation):
        delete_contract_event(1)


def test_text_snapshot_rebuilds_identical_tables(tmp_path: Path) -> None:
    """CSV snapshots restore table contents and leave IDs ready for appends."""
    source_path = tmp_path / "source.duckdb"
    rebuilt_path = tmp_path / "rebuilt.duckdb"
    initialize_database(source_path)
    with open_database(source_path) as connection:
        connection.execute("INSERT INTO teams (team_id, team_name) VALUES ('t1', 'Team One')")
        connection.execute("INSERT INTO players (fantrax_id, name) VALUES ('p1', 'Player One')")
        connection.execute(
            "INSERT INTO announcements (team_id, fantrax_id, announced_at, raw_message) "
            "VALUES ('t1', 'p1', TIMESTAMP '2027-02-01 12:00:00', 'signing')"
        )

    event_id = append_event(
        "t1", "p1", "SIGNED", 2, 2029, "manual",
        ts=datetime(2027, 2, 1), note="", database_path=source_path,
    )
    snapshot_dir = export_text_snapshot(source_path, snapshot_root=tmp_path / "snapshots")
    restore_text_snapshot(snapshot_dir, rebuilt_path)

    with open_database(source_path, read_only=True) as source:
        with open_database(rebuilt_path, read_only=True) as rebuilt:
            for table_name in (
                "teams",
                "players",
                "contract_events",
                "roster_snapshots",
                "pending_contracts",
                "discord_bot_state",
                "discord_processed_messages",
                "announcements",
                "season_config",
                "team_season_history",
                "league_history",
            ):
                source_rows = source.execute(f"SELECT * FROM {table_name} ORDER BY ALL").fetchall()
                rebuilt_rows = rebuilt.execute(f"SELECT * FROM {table_name} ORDER BY ALL").fetchall()
                assert rebuilt_rows == source_rows, table_name

    next_event_id = append_event(
        "t1", "p1", "EXTENDED", 1, 2030, "manual",
        ts=datetime(2028, 2, 1), database_path=rebuilt_path,
    )
    assert next_event_id > event_id


def test_roster_snapshot_replaces_date_and_preserves_il_level(tmp_path: Path) -> None:
    """Record the normalized MLB, minor, and IL roster classifications."""
    database_path = tmp_path / "sda.duckdb"
    initialize_database(database_path)
    roster = {
        "t1": {
            "team_id": "t1",
            "mlb_roster": [{"id": "p1", "roster_level": "IL"}],
            "minors_roster": [{"id": "p2", "roster_level": "minors"}],
        }
    }

    from sda.db.ledger import record_roster_snapshot

    assert record_roster_snapshot(datetime(2027, 3, 1).date(), roster, database_path) == 2
    roster["t1"]["mlb_roster"] = [{"id": "p1", "roster_level": "MLB"}]
    assert record_roster_snapshot(datetime(2027, 3, 1).date(), roster, database_path) == 2
    with open_database(database_path, read_only=True) as connection:
        rows = connection.execute(
            "SELECT fantrax_id, roster_level FROM roster_snapshots ORDER BY fantrax_id"
        ).fetchall()
    assert rows == [("p1", "MLB"), ("p2", "minors")]