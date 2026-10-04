"""Discord approval service tests with no network or live credentials."""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from sda.db.connection import open_database
from sda.db.schema import initialize_database
from sda.discord_bot.approvals import (
    ApprovalError,
    approve_pending,
    queue_cap_trade,
    queue_signing,
    team_resolver,
    validate_cap_trade,
    validate_signing,
)
from sda.discord_bot.bot import _confirmation_embed, main as bot_main
from sda.discord_bot.parser import ParsedCapTrade, ParsedSigning


def _signing(
    player_id: str,
    player_name: str,
    years: float,
    acquisition_type: str,
    form_ref: str,
    *,
    submitted_at: datetime = datetime(2027, 5, 1, 12, tzinfo=timezone.utc),
) -> ParsedSigning:
    return ParsedSigning(
        player_id, player_name, years, "team-a", "Alpha Owls", acquisition_type,
        submitted_at, "Alpha Owls", form_ref,
    )


def _cap_trade(form_ref: str, years: float = 2.5) -> ParsedCapTrade:
    return ParsedCapTrade(
        "team-a", "Alpha Owls", "team-b", "Copperheads", years,
        datetime(2027, 5, 1, 12, tzinfo=timezone.utc), "Alpha Owls", form_ref,
    )


def _database(path: Path, *, player_count: int = 1, active_deals: int = 0) -> None:
    initialize_database(path)
    with open_database(path) as connection:
        connection.executemany(
            "INSERT INTO teams (team_id, team_name, manager) VALUES (?, ?, ?)",
            [("team-a", "Alpha Owls", "Manager A"), ("team-b", "Copperheads", "Manager B")],
        )
        connection.execute(
            """INSERT INTO season_config (
                   season, freeze_date, draft_start, draft_end, min_mlb_roster, cap_years
               ) VALUES (2027, DATE '2027-04-25', DATE '2027-03-13', DATE '2027-03-18', NULL, 78)"""
        )
        players = [(f"p{index}", f"Player {index}") for index in range(1, max(player_count, active_deals) + 1)]
        connection.executemany("INSERT INTO players (fantrax_id, name) VALUES (?, ?)", players)
        if active_deals:
            connection.executemany(
                """INSERT INTO contract_events (
                       ts, team_id, fantrax_id, event_type, years, fa_year, source
                   ) VALUES (TIMESTAMP '2027-01-01', 'team-a', ?, 'SIGNED', 1, 2028, 'manual')""",
                [(f"p{index}",) for index in range(1, active_deals + 1)],
            )


def test_over_cap_proposal_can_be_queued_but_never_written_to_ledger(tmp_path: Path) -> None:
    database = tmp_path / "over-cap.duckdb"
    _database(database, player_count=79, active_deals=78)
    proposal = _signing("p79", "Player 79", 1, "waiver", "123e4567-e89b-12d3-a456-426614174001")
    checks = validate_signing(proposal, database, now=datetime(2027, 5, 1))
    assert checks["passed"] is False
    assert next(check for check in checks["checks"] if check["rule_id"] == "V1")["passed"] is False
    card = _confirmation_embed(0, "Signing proposal", "1 year", checks)
    assert any(field.name == "FAIL / V1" for field in card.fields)

    pending_id, already_processed = queue_signing(
        proposal,
        "raw signing",
        "1001",
        "channel-1",
        database,
    )
    assert pending_id is not None and not already_processed
    repeated_id, duplicate = queue_signing(
        proposal, "raw signing", "1001", "channel-1", database
    )
    assert (repeated_id, duplicate) == (pending_id, True)
    with pytest.raises(ApprovalError, match="no longer passes validation"):
        approve_pending(pending_id, "commissioner-1", database, now=datetime(2027, 5, 2))
    with open_database(database, read_only=True) as connection:
        assert connection.execute("SELECT count(*) FROM contract_events").fetchone()[0] == 78
        assert connection.execute("SELECT count(*) FROM pending_contracts").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM announcements").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM discord_processed_messages").fetchone()[0] == 1
        assert connection.execute(
            "SELECT state_value FROM discord_bot_state WHERE state_key = 'last_message_id:channel-1'"
        ).fetchone()[0] == "1001"
        announced_at = connection.execute("SELECT announced_at FROM announcements").fetchone()[0]
    assert announced_at == datetime(2027, 5, 1, 12)


def test_dry_run_validation_has_no_database_side_effects(tmp_path: Path) -> None:
    database = tmp_path / "dry-run.duckdb"
    _database(database)
    proposal = _signing("p1", "Player 1", 2, "waiver", "123e4567-e89b-12d3-a456-426614174002")

    result = validate_signing(proposal, database, now=datetime(2027, 5, 1))

    assert result["passed"] is True
    with open_database(database, read_only=True) as connection:
        assert connection.execute("SELECT count(*) FROM contract_events").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM pending_contracts").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM announcements").fetchone()[0] == 0


def test_same_signing_with_different_ref_within_24_hours_is_deduplicated(tmp_path: Path) -> None:
    database = tmp_path / "duplicate-deal.duckdb"
    _database(database)
    submitted_at = datetime(2027, 5, 1, 12, tzinfo=timezone.utc)
    original = _signing(
        "p1", "Player 1", 2, "waiver", "123e4567-e89b-12d3-a456-426614174010",
        submitted_at=submitted_at,
    )
    retry = _signing(
        "p1", "Player 1", 2, "waiver", "123e4567-e89b-12d3-a456-426614174011",
        submitted_at=datetime(2027, 5, 1, 13, tzinfo=timezone.utc),
    )

    first_id, first_duplicate = queue_signing(original, "original", "1010", "channel-1", database)
    retry_id, retry_duplicate = queue_signing(retry, "retry", "1011", "channel-1", database)

    assert first_id is not None and not first_duplicate
    assert retry_id is None and retry_duplicate
    with open_database(database, read_only=True) as connection:
        assert connection.execute("SELECT count(*) FROM pending_contracts").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM discord_processed_messages").fetchone()[0] == 2


def test_approved_signing_appends_one_event_and_updates_queue(tmp_path: Path) -> None:
    database = tmp_path / "approve-signing.duckdb"
    _database(database)
    proposal = _signing("p1", "Player 1", 2, "waiver", "123e4567-e89b-12d3-a456-426614174003")
    pending_id, _ = queue_signing(proposal, "raw", "1002", "channel-1", database)
    assert pending_id is not None

    result = approve_pending(pending_id, "commissioner-1", database, now=datetime(2027, 5, 1))

    assert len(result["event_ids"]) == 1
    with open_database(database, read_only=True) as connection:
        event = connection.execute(
            "SELECT event_type, years, fa_year, source, approved_by, form_ref, acquisition_type, announced_at FROM contract_events"
        ).fetchone()
        status = connection.execute(
            "SELECT status FROM pending_contracts WHERE pending_id = ?", [pending_id]
        ).fetchone()[0]
    assert event == (
        "SIGNED", 2.0, 2029, "form", "commissioner-1",
        "123e4567-e89b-12d3-a456-426614174003", "waiver", datetime(2027, 5, 1, 12),
    )
    assert status == "approved"


def test_draft_announcement_uses_message_time_when_approved_later(tmp_path: Path) -> None:
    database = tmp_path / "draft-signing.duckdb"
    _database(database)
    proposal = _signing(
        "p1", "Player 1", 7, "drafted", "123e4567-e89b-12d3-a456-426614174004",
        submitted_at=datetime(2027, 3, 15, 15, tzinfo=timezone.utc),
    )
    pending_id, _ = queue_signing(
        proposal,
        "draft signing",
        "1004",
        "channel-1",
        database,
    )
    assert pending_id is not None

    approve_pending(pending_id, "commissioner-1", database, now=datetime(2027, 5, 1))

    with open_database(database, read_only=True) as connection:
        event = connection.execute("SELECT event_type, years, ts FROM contract_events").fetchone()
    assert event == ("SIGNED", 7.0, datetime(2027, 3, 15, 15))


def test_approved_cap_trade_appends_two_signed_adjustments(tmp_path: Path) -> None:
    database = tmp_path / "approve-trade.duckdb"
    _database(database)
    proposal = _cap_trade("123e4567-e89b-12d3-a456-426614174005")
    assert set(team_resolver(database).values()) == {"team-a", "team-b"}
    assert validate_cap_trade(proposal, database)["passed"] is True
    pending_id, _ = queue_cap_trade(proposal, "raw cap trade", "1003", "channel-1", database)
    assert pending_id is not None

    result = approve_pending(pending_id, "commissioner-1", database, now=datetime(2027, 5, 1))

    assert len(result["event_ids"]) == 2
    with open_database(database, read_only=True) as connection:
        trades = connection.execute(
            "SELECT team_id, years, event_type, source, approved_by, form_ref, announced_at FROM contract_events ORDER BY team_id"
        ).fetchall()
    assert trades == [
        ("team-a", -2.5, "CAP_TRADE", "form", "commissioner-1", "123e4567-e89b-12d3-a456-426614174005", datetime(2027, 5, 1, 12)),
        ("team-b", 2.5, "CAP_TRADE", "form", "commissioner-1", "123e4567-e89b-12d3-a456-426614174005", datetime(2027, 5, 1, 12)),
    ]


def test_cli_dry_run_shows_cap_failure_and_writes_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    database = tmp_path / "dry-run-cli.duckdb"
    _database(database, player_count=79, active_deals=78)
    fake_token = "secret-" + "value-not-for-logs"
    monkeypatch.setenv("DISCORD_BOT_TOKEN", fake_token)

    exit_code = bot_main(
        [
            "--dry-run",
            "--message",
            "📝 SIGNING: Player 79 — 1 years — Alpha Owls\n"
            "type: waiver · submitted: 2027-05-01T12:00:00Z · by: Alpha Owls · ref: 123e4567-e89b-12d3-a456-426614174006",
            "--database",
            str(database),
            "--config",
            str(Path(__file__).parents[1] / "config" / "season.yaml"),
            "--aliases",
            str(tmp_path / "aliases.json"),
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 1
    assert "FAIL / V1" in output
    assert fake_token not in output
    assert fake_token not in caplog.text
    with open_database(database, read_only=True) as connection:
        assert connection.execute("SELECT count(*) FROM pending_contracts").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM announcements").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM contract_events").fetchone()[0] == 78
        string_columns = connection.execute(
            """SELECT table_name, column_name FROM information_schema.columns
               WHERE table_schema = 'main' AND data_type = 'VARCHAR'"""
        ).fetchall()
        for table_name, column_name in string_columns:
            values = connection.execute(f'SELECT "{column_name}" FROM "{table_name}"').fetchall()
            assert all(fake_token not in str(value[0]) for value in values if value[0] is not None)