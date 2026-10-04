"""Proposal validation, durable Discord queueing, and commissioner approvals."""

from __future__ import annotations

import logging
import math
from collections.abc import Mapping
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from sda.db.connection import DEFAULT_DATABASE_PATH, open_database
from sda.db.ledger import _validate_event, team_cap_committed
from sda.db.snapshots import export_text_snapshot
from sda.discord_bot.parser import ParsedCapTrade, ParsedSigning
from sda.validation import rules

LOGGER = logging.getLogger(__name__)


class ApprovalError(RuntimeError):
    """Raised when a pending proposal cannot safely be approved or rejected."""


def team_resolver(database_path: Path = DEFAULT_DATABASE_PATH) -> dict[str, str]:
    """Map normalized team names and manager names to stable team IDs."""
    with open_database(database_path, read_only=True) as connection:
        rows = connection.execute("SELECT team_id, team_name, manager FROM teams").fetchall()
    return {
        str(label): str(team_id)
        for team_id, team_name, manager in rows
        for label in (team_name, manager)
        if label
    }


def player_aliases(
    database_path: Path = DEFAULT_DATABASE_PATH,
    alias_path: Path | None = None,
) -> dict[str, str]:
    """Combine persisted aliases and current player names into normalized identities."""
    from sda.fantrax.normalize import DEFAULT_ALIAS_PATH, load_aliases, normalize_alias

    aliases = load_aliases(alias_path or DEFAULT_ALIAS_PATH)
    with open_database(database_path, read_only=True) as connection:
        rows = connection.execute("SELECT fantrax_id, name FROM players").fetchall()
    for player_id, name in rows:
        normalized = normalize_alias(str(name))
        if normalized and normalized not in aliases:
            aliases[normalized] = str(player_id)
        if name:
            aliases[str(name)] = str(player_id)
    return aliases


def validate_signing(
    proposal: ParsedSigning,
    database_path: Path = DEFAULT_DATABASE_PATH,
    *,
    now: datetime | None = None,
    acquisition_type: str | None = None,
) -> dict[str, Any]:
    """Apply contract bounds, cap, roster-limit, and re-sign rules to a proposal."""
    database_path = Path(database_path)
    proposed_at = now or datetime.now()
    acquisition_type = acquisition_type or proposal.acquisition_type
    with open_database(database_path, read_only=True) as connection:
        config = connection.execute(
            """SELECT season, cap_years, min_mlb_roster, draft_start, draft_end
               FROM season_config ORDER BY season DESC LIMIT 1"""
        ).fetchone()
        if config is None:
            season, cap_years, min_roster, draft_start, draft_end = proposed_at.year, 78, None, None, None
        else:
            season, cap_years, min_roster, draft_start, draft_end = config
        known = connection.execute(
            """SELECT EXISTS(SELECT 1 FROM teams WHERE team_id = ?),
                      EXISTS(SELECT 1 FROM players WHERE fantrax_id = ?)""",
            [proposal.team_id, proposal.player_id],
        ).fetchone()
        latest_snapshot = connection.execute("SELECT max(snapshot_date) FROM roster_snapshots").fetchone()[0]
        roster_rows = connection.execute(
            """SELECT fantrax_id, roster_level FROM roster_snapshots
               WHERE snapshot_date = ? AND team_id = ?""",
            [latest_snapshot, proposal.team_id],
        ).fetchall() if latest_snapshot else []
        all_teams_roster = connection.execute(
            """SELECT team_id, fantrax_id, roster_level FROM roster_snapshots
               WHERE snapshot_date = ?""",
            [latest_snapshot],
        ).fetchall() if latest_snapshot else []
        event_cursor = connection.execute(
            """SELECT event_id, ts, team_id, fantrax_id, event_type, years, fa_year,
                      source, note, roster_level
               FROM contract_events ORDER BY ts, recorded_at, event_id"""
        )
        columns = [column[0] for column in event_cursor.description]
        events = [dict(zip(columns, row, strict=True)) for row in event_cursor.fetchall()]

    results: list[dict[str, Any]] = []
    results.append(
        {
            "rule_id": "IDENTITY",
            "team_id": proposal.team_id,
            "fantrax_id": proposal.player_id,
            "passed": bool(known[0] and known[1]),
            "detail": "Team and player IDs are known." if known[0] and known[1] else "Team or player ID is unknown.",
            "review_required": False,
        }
    )
    if not (known[0] and known[1]):
        return _validation_summary(results)

    player_level = next(
        (str(level) for player_id, level in roster_rows if str(player_id) == proposal.player_id),
        None,
    )
    event_type = "CALLED_UP" if acquisition_type == "called_up" else "SIGNED"

    candidate = {
        "team_id": proposal.team_id,
        "fantrax_id": proposal.player_id,
        "event_type": event_type,
        "years": proposal.years,
        "fa_year": int(season) + int(proposal.years),
        "ts": proposed_at,
        "note": f"acquisition_type={acquisition_type}",
        "roster_level": "MLB",
    }
    bounds = rules.v2_contract_bounds(candidate, acquisition_type)
    if not float(proposal.years).is_integer():
        bounds = {**bounds, "passed": False, "detail": "Contract duration must be a whole number of years."}
    results.append(bounds)

    il_count = sum(1 for _player_id, level in roster_rows if str(level) == "IL")
    committed = team_cap_committed(proposal.team_id, int(season), database_path)
    results.append(rules.v1_cap(proposal.team_id, committed + proposal.years, il_count, int(cap_years)))

    mlb_count = sum(1 for _player_id, level in roster_rows if str(level) in {"MLB", "IL"})
    minors_count = sum(1 for _player_id, level in roster_rows if str(level) == "minors")
    already_mlb = any(
        str(player_id) == proposal.player_id and str(level) in {"MLB", "IL"}
        for player_id, level in roster_rows
    )
    already_minors = player_level == "minors"
    roster_result = rules.v10_roster_max(
        proposal.team_id,
        mlb_count + (0 if already_mlb else 1),
        minors_count - (1 if already_minors else 0),
        il_count,
    )
    results.append(roster_result)

    if min_roster is not None:
        results.append(rules.v11_roster_min(proposal.team_id, mlb_count + (0 if already_mlb else 1), int(min_roster)))
    duplicate_teams = {
        str(team_id)
        for team_id, player_id, level in all_teams_roster
        if str(player_id) == proposal.player_id and str(level) in {"MLB", "IL"}
    }
    results.append(
        {
            "rule_id": "V4",
            "team_id": proposal.team_id,
            "fantrax_id": proposal.player_id,
            "passed": not (duplicate_teams - {proposal.team_id}),
            "detail": "No duplicate MLB roster placement." if not duplicate_teams - {proposal.team_id}
            else "Player is already on another team's MLB roster.",
            "review_required": False,
        }
    )
    resign_candidate = {**candidate, "event_id": max((int(event.get("event_id") or 0) for event in events), default=0) + 1}
    results.extend(rules.v3_resign_tripwire(
        [*events, resign_candidate], int(season), draft_start, draft_end
    ))
    if not any(result["rule_id"] == "V3" for result in results):
        results.append({
            "rule_id": "V3", "team_id": proposal.team_id, "fantrax_id": proposal.player_id,
            "passed": True, "detail": "Re-sign tripwire passed.", "review_required": False,
        })
    return _validation_summary(results)


def validate_cap_trade(
    proposal: ParsedCapTrade,
    database_path: Path = DEFAULT_DATABASE_PATH,
    *,
    season: int | None = None,
) -> dict[str, Any]:
    """Check known teams and positive finite trade amount; report new cap totals."""
    if not math.isfinite(proposal.years) or proposal.years <= 0:
        return _validation_summary([{
            "rule_id": "CAP_TRADE", "team_id": proposal.from_team_id, "fantrax_id": None,
            "passed": False, "detail": "Cap trade years must be positive and finite.", "review_required": False,
        }])
    with open_database(database_path, read_only=True) as connection:
        season_row = connection.execute(
            "SELECT season, cap_years FROM season_config ORDER BY season DESC LIMIT 1"
        ).fetchone()
        target_season = season or (int(season_row[0]) if season_row else date.today().year)
        cap_years = int(season_row[1]) if season_row else 78
        known = {
            str(team_id): str(team_name)
            for team_id, team_name in connection.execute("SELECT team_id, team_name FROM teams").fetchall()
        }
        il_counts = {
            str(team_id): int(count)
            for team_id, count in connection.execute(
                """SELECT team_id, count(*) FROM roster_snapshots
                   WHERE snapshot_date = (SELECT max(snapshot_date) FROM roster_snapshots)
                     AND roster_level = 'IL' GROUP BY team_id"""
            ).fetchall()
        }
    valid = (
        proposal.from_team_id in known
        and proposal.to_team_id in known
        and proposal.from_team_id != proposal.to_team_id
    )
    results = [{
        "rule_id": "CAP_TRADE",
        "team_id": proposal.from_team_id,
        "fantrax_id": None,
        "passed": valid,
        "detail": "Both teams are known and distinct." if valid else "Cap trade team identity is invalid.",
        "review_required": False,
    }]
    if valid:
        for team_id, delta in ((proposal.from_team_id, -proposal.years), (proposal.to_team_id, proposal.years)):
            net_trades = _net_cap_trades(team_id, target_season, database_path)
            adjusted_commitment = team_cap_committed(team_id, target_season, database_path)
            player_commitment = adjusted_commitment + net_trades
            effective = cap_years + il_counts.get(team_id, 0) + net_trades + delta
            remaining = effective - player_commitment
            results.append({
                "rule_id": "CAP_TRADE_CAP",
                "team_id": team_id,
                "fantrax_id": None,
                "passed": True,
                "detail": (
                    f"Effective cap after trade: {effective:g}; committed: "
                    f"{player_commitment:g}; remaining: {remaining:g}."
                ),
                "review_required": False,
            })
    return _validation_summary(results)


def queue_signing(
    proposal: ParsedSigning,
    raw_message: str,
    message_id: str,
    channel_id: str,
    database_path: Path = DEFAULT_DATABASE_PATH,
) -> tuple[int | None, bool]:
    """Atomically queue the signing, timestamp its announcement, and advance catch-up state."""
    row = None
    duplicate_deal = False
    with open_database(database_path) as connection:
        connection.execute("BEGIN TRANSACTION")
        if _was_processed(connection, message_id, proposal.form_ref):
            existing = connection.execute(
                "SELECT pending_id FROM pending_contracts WHERE form_ref = ?", [proposal.form_ref]
            ).fetchone()
            connection.execute("ROLLBACK")
            return (int(existing[0]) if existing else None), True
        if _duplicate_signing(connection, proposal):
            _mark_processed(connection, message_id, channel_id, "duplicate_signing", proposal.form_ref)
            _advance_cursor(connection, channel_id, message_id)
            connection.execute("COMMIT")
            duplicate_deal = True
        else:
            season_row = connection.execute(
                "SELECT season FROM season_config ORDER BY season DESC LIMIT 1"
            ).fetchone()
            season = int(season_row[0]) if season_row else datetime.now().year
            row = connection.execute(
                """INSERT INTO pending_contracts (
                       created_at, team_id, fantrax_id, years, fa_year, raw_message, kind,
                       discord_message_id, form_ref, acquisition_type, announced_at
                   ) VALUES (?, ?, ?, ?, ?, ?, 'signing', ?, ?, ?, ?) RETURNING pending_id""",
                [
                    _naive_utc(proposal.submitted_at), proposal.team_id, proposal.player_id,
                    proposal.years, season + int(proposal.years), raw_message, message_id,
                    proposal.form_ref, proposal.acquisition_type, _naive_utc(proposal.submitted_at),
                ],
            ).fetchone()
            connection.execute(
                """INSERT INTO announcements (
                       message_id, team_id, fantrax_id, announced_at, raw_message,
                       form_ref, acquisition_type, kind
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, 'signing')""",
                [
                    message_id, proposal.team_id, proposal.player_id,
                    _naive_utc(proposal.submitted_at), raw_message, proposal.form_ref,
                    proposal.acquisition_type,
                ],
            )
            _mark_processed(connection, message_id, channel_id, "queued_signing", proposal.form_ref)
            _advance_cursor(connection, channel_id, message_id)
            connection.execute("COMMIT")
    export_text_snapshot(database_path)
    return (int(row[0]) if row else None), duplicate_deal


def queue_cap_trade(
    proposal: ParsedCapTrade,
    raw_message: str,
    message_id: str,
    channel_id: str,
    database_path: Path = DEFAULT_DATABASE_PATH,
) -> tuple[int | None, bool]:
    """Atomically queue a cap trade and mark its source announcement processed."""
    row = None
    duplicate_deal = False
    with open_database(database_path) as connection:
        connection.execute("BEGIN TRANSACTION")
        if _was_processed(connection, message_id, proposal.form_ref):
            existing = connection.execute(
                "SELECT pending_id FROM pending_contracts WHERE form_ref = ?", [proposal.form_ref]
            ).fetchone()
            connection.execute("ROLLBACK")
            return (int(existing[0]) if existing else None), True
        if _duplicate_cap_trade(connection, proposal):
            _mark_processed(connection, message_id, channel_id, "duplicate_cap_trade", proposal.form_ref)
            _advance_cursor(connection, channel_id, message_id)
            connection.execute("COMMIT")
            duplicate_deal = True
        else:
            row = connection.execute(
                """INSERT INTO pending_contracts (
                       created_at, team_id, fantrax_id, years, fa_year, raw_message, kind,
                       to_team_id, discord_message_id, form_ref, announced_at
                   ) VALUES (?, ?, NULL, ?, NULL, ?, 'cap_trade', ?, ?, ?, ?) RETURNING pending_id""",
                [
                    _naive_utc(proposal.submitted_at), proposal.from_team_id, proposal.years,
                    raw_message, proposal.to_team_id, message_id, proposal.form_ref,
                    _naive_utc(proposal.submitted_at),
                ],
            ).fetchone()
            _mark_processed(connection, message_id, channel_id, "queued_cap_trade", proposal.form_ref)
            _advance_cursor(connection, channel_id, message_id)
            connection.execute("COMMIT")
    export_text_snapshot(database_path)
    return (int(row[0]) if row else None), duplicate_deal


def mark_message_processed(
    message_id: str,
    channel_id: str,
    outcome: str,
    database_path: Path = DEFAULT_DATABASE_PATH,
) -> bool:
    """Persist ignored/malformed messages so startup history never processes them twice."""
    with open_database(database_path) as connection:
        connection.execute("BEGIN TRANSACTION")
        if _was_processed(connection, message_id):
            connection.execute("ROLLBACK")
            return False
        _mark_processed(connection, message_id, channel_id, outcome)
        _advance_cursor(connection, channel_id, message_id)
        connection.execute("COMMIT")
    export_text_snapshot(database_path)
    return True


def approve_pending(
    pending_id: int,
    approved_by: str,
    database_path: Path = DEFAULT_DATABASE_PATH,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Revalidate and atomically append a signing or two-sided cap-trade approval."""
    database_path = Path(database_path)
    with open_database(database_path, read_only=True) as connection:
        row = connection.execute(
            """SELECT pending_id, team_id, fantrax_id, years, fa_year, raw_message,
                      status, kind, to_team_id, discord_message_id, created_at,
                      form_ref, acquisition_type, announced_at
               FROM pending_contracts WHERE pending_id = ?""",
            [pending_id],
        ).fetchone()
    if row is None:
        raise ApprovalError(f"Pending proposal {pending_id} does not exist")
    if row[6] != "pending":
        raise ApprovalError(f"Pending proposal {pending_id} is already {row[6]}")
    kind = row[7] or "signing"
    if kind == "cap_trade":
        teams = team_resolver(database_path)
        sender_name = next((name for name, team_id in teams.items() if team_id == str(row[1])), str(row[1]))
        receiver_name = next((name for name, team_id in teams.items() if team_id == str(row[8])), str(row[8]))
        submitted_at = row[13] or row[10]
        if submitted_at.tzinfo is None:
            submitted_at = submitted_at.replace(tzinfo=timezone.utc)
        trade = ParsedCapTrade(
            str(row[1]), sender_name, str(row[8]), receiver_name, float(row[3]),
            submitted_at, sender_name, str(row[11]),
        )
        validation = validate_cap_trade(trade, database_path)
        event_specs = [
            (str(row[1]), None, "CAP_TRADE", -float(row[3]), None, row[11], None, submitted_at),
            (str(row[8]), None, "CAP_TRADE", float(row[3]), None, row[11], None, submitted_at),
        ]
    else:
        with open_database(database_path, read_only=True) as connection:
            details = connection.execute(
                """SELECT p.name, t.team_name FROM players p, teams t
                   WHERE p.fantrax_id = ? AND t.team_id = ?""",
                [row[2], row[1]],
            ).fetchone()
        if details is None:
            raise ApprovalError("Pending player or team no longer exists")
        announcement_time = row[13] or row[10]
        if announcement_time.tzinfo is None:
            announcement_time = announcement_time.replace(tzinfo=timezone.utc)
        proposal = ParsedSigning(
            str(row[2]), str(details[0]), float(row[3]), str(row[1]), str(details[1]),
            str(row[12]), announcement_time, str(details[1]), str(row[11]),
        )
        validation = validate_signing(
            proposal,
            database_path,
            now=announcement_time,
            acquisition_type=row[12],
        )
        event_type = "CALLED_UP" if row[12] == "called_up" else "SIGNED"
        event_specs = [(
            str(row[1]), str(row[2]), event_type, float(row[3]), int(row[4]),
            row[11], row[12], announcement_time,
        )]
    if not validation["passed"]:
        failed = "; ".join(result["detail"] for result in validation["checks"] if not result["passed"])
        raise ApprovalError(f"Proposal no longer passes validation: {failed}")
    if kind == "cap_trade":
        event_time = _naive_utc(submitted_at)
    else:
        event_time = _naive_utc(announcement_time)
    with open_database(database_path) as connection:
        connection.execute("BEGIN TRANSACTION")
        current = connection.execute(
            "SELECT status FROM pending_contracts WHERE pending_id = ?", [pending_id]
        ).fetchone()
        if current is None or current[0] != "pending":
            connection.execute("ROLLBACK")
            raise ApprovalError(f"Pending proposal {pending_id} has already been processed")
        event_ids = []
        for team_id, fantrax_id, event_type, years, fa_year, form_ref, acquisition_type, announced_at in event_specs:
            roster_level = "MLB"
            _validate_event(
                team_id, fantrax_id, event_type, years, fa_year, "form", roster_level,
                form_ref=form_ref, acquisition_type=acquisition_type, announced_at=announced_at,
            )
            event_id = connection.execute(
                """INSERT INTO contract_events (
                       ts, team_id, fantrax_id, event_type, years, fa_year, source,
                       note, approved_by, roster_level, form_ref, acquisition_type, announced_at
                   ) VALUES (?, ?, ?, ?, ?, ?, 'form', ?, ?, ?, ?, ?, ?) RETURNING event_id""",
                [event_time, team_id, fantrax_id, event_type, years, fa_year,
                 f"approved pending proposal {pending_id}", approved_by, roster_level,
                 form_ref, acquisition_type, _naive_utc(announced_at)],
            ).fetchone()[0]
            event_ids.append(int(event_id))
        connection.execute(
            "UPDATE pending_contracts SET status = 'approved' WHERE pending_id = ?",
            [pending_id],
        )
        connection.execute("COMMIT")
    export_text_snapshot(database_path)
    return {"pending_id": pending_id, "event_ids": event_ids, "kind": kind, "validation": validation}


def reject_pending(
    pending_id: int,
    database_path: Path = DEFAULT_DATABASE_PATH,
) -> None:
    """Reject a still-pending proposal without writing any contract event."""
    with open_database(database_path) as connection:
        row = connection.execute(
            "UPDATE pending_contracts SET status = 'rejected' WHERE pending_id = ? AND status = 'pending' RETURNING pending_id",
            [pending_id],
        ).fetchone()
        if row is None:
            raise ApprovalError(f"Pending proposal {pending_id} is missing or already processed")
    export_text_snapshot(database_path)


def _validation_summary(checks: list[dict[str, Any]]) -> dict[str, Any]:
    return {"passed": all(result["passed"] for result in checks), "checks": checks}


def _naive_utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value


def _was_processed(connection: Any, message_id: str, form_ref: str | None = None) -> bool:
    if form_ref:
        query = "SELECT 1 FROM discord_processed_messages WHERE message_id = ? OR form_ref = ?"
        parameters = [message_id, form_ref]
    else:
        query = "SELECT 1 FROM discord_processed_messages WHERE message_id = ?"
        parameters = [message_id]
    return connection.execute(query, parameters).fetchone() is not None


def _mark_processed(
    connection: Any,
    message_id: str,
    channel_id: str,
    outcome: str,
    form_ref: str | None = None,
) -> None:
    connection.execute(
        "INSERT INTO discord_processed_messages (message_id, channel_id, outcome, form_ref) VALUES (?, ?, ?, ?)",
        [message_id, channel_id, outcome, form_ref],
    )


def _duplicate_signing(connection: Any, proposal: ParsedSigning) -> bool:
    submitted_at = _naive_utc(proposal.submitted_at)
    return connection.execute(
        """SELECT 1 FROM pending_contracts
           WHERE kind = 'signing' AND team_id = ? AND fantrax_id = ? AND years = ?
             AND created_at BETWEEN ? - INTERVAL '24 hours' AND ? + INTERVAL '24 hours'
           LIMIT 1""",
        [proposal.team_id, proposal.player_id, proposal.years, submitted_at, submitted_at],
    ).fetchone() is not None


def _duplicate_cap_trade(connection: Any, proposal: ParsedCapTrade) -> bool:
    submitted_at = _naive_utc(proposal.submitted_at)
    return connection.execute(
        """SELECT 1 FROM pending_contracts
           WHERE kind = 'cap_trade' AND team_id = ? AND to_team_id = ? AND years = ?
             AND created_at BETWEEN ? - INTERVAL '24 hours' AND ? + INTERVAL '24 hours'
           LIMIT 1""",
        [proposal.from_team_id, proposal.to_team_id, proposal.years, submitted_at, submitted_at],
    ).fetchone() is not None


def _advance_cursor(connection: Any, channel_id: str, message_id: str) -> None:
    key = f"last_message_id:{channel_id}"
    previous = connection.execute("SELECT state_value FROM discord_bot_state WHERE state_key = ?", [key]).fetchone()
    cursor_id = max(int(previous[0]), int(message_id)) if previous else int(message_id)
    connection.execute(
        """INSERT INTO discord_bot_state (state_key, state_value, updated_at)
           VALUES (?, ?, CURRENT_TIMESTAMP)
           ON CONFLICT (state_key) DO UPDATE SET
               state_value = excluded.state_value, updated_at = excluded.updated_at""",
        [key, str(cursor_id)],
    )


def _net_cap_trades(team_id: str, season: int, database_path: Path) -> float:
    with open_database(database_path, read_only=True) as connection:
        value = connection.execute(
            """SELECT COALESCE(SUM(years), 0) FROM contract_events
               WHERE team_id = ? AND event_type = 'CAP_TRADE' AND YEAR(ts) = ?""",
            [team_id, season],
        ).fetchone()[0]
    return float(value)