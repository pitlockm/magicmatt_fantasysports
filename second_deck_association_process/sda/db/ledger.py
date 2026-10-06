"""Append-only contract event ledger and derived cap queries."""

from __future__ import annotations

import logging
import math
from collections.abc import Mapping
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from sda.db.connection import DEFAULT_DATABASE_PATH, open_database

LOGGER = logging.getLogger(__name__)
EVENT_TYPES = {
    "SIGNED",
    "EXTENDED",
    "DROPPED",
    "EXPIRED",
    "CALLED_UP",
    "CAP_TRADE",
    "DEFAULTED_1YR",
}
EVENT_SOURCES = {"form", "manual", "migration", "fantrax", "system"}
ROSTER_LEVELS = {"MLB", "minors"}


class AppendOnlyViolation(RuntimeError):
    """Raised when code attempts to alter or remove a contract event."""


def append_event(
    team_id: str,
    fantrax_id: str | None,
    event_type: str,
    years: float,
    fa_year: int | None,
    source: str,
    *,
    ts: datetime | None = None,
    note: str | None = None,
    approved_by: str | None = None,
    roster_level: str = "MLB",
    form_ref: str | None = None,
    acquisition_type: str | None = None,
    announced_at: datetime | None = None,
    database_path: Path = DEFAULT_DATABASE_PATH,
) -> int:
    """Append a validated contract event and return its event ID."""
    _validate_event(
        team_id, fantrax_id, event_type, years, fa_year, source, roster_level,
        form_ref=form_ref, acquisition_type=acquisition_type, announced_at=announced_at,
    )
    event_time = ts or datetime.now(timezone.utc)
    if event_time.tzinfo is not None:
        event_time = event_time.astimezone(timezone.utc).replace(tzinfo=None)
    submitted_at = announced_at
    if submitted_at is not None and submitted_at.tzinfo is not None:
        submitted_at = submitted_at.astimezone(timezone.utc).replace(tzinfo=None)

    with open_database(database_path) as connection:
        row = connection.execute(
            """INSERT INTO contract_events (
                   ts, team_id, fantrax_id, event_type, years, fa_year, source,
                   note, approved_by, roster_level, form_ref, acquisition_type, announced_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               RETURNING event_id""",
            [
                event_time,
                team_id,
                fantrax_id,
                event_type,
                float(years),
                fa_year,
                source,
                note,
                approved_by,
                roster_level,
                form_ref,
                acquisition_type,
                submitted_at,
            ],
        ).fetchone()
    _export_after_write(database_path)
    return int(row[0])


def current_contracts(
    season: int,
    database_path: Path = DEFAULT_DATABASE_PATH,
) -> list[dict[str, Any]]:
    """Replay the ledger view and return active deals with years remaining."""
    with open_database(database_path, read_only=True) as connection:
        cursor = connection.execute(
            """SELECT *, (fa_year - ?)::DOUBLE AS years_remaining
               FROM current_contracts
               WHERE fa_year > ?
               ORDER BY team_id, fantrax_id""",
            [season, season],
        )
        columns = [description[0] for description in cursor.description]
        return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def team_cap_committed(
    team_id: str,
    season: int,
    database_path: Path = DEFAULT_DATABASE_PATH,
) -> float:
    """Sum active contract years, scheduled dead cap, and that season's cap trades."""
    with open_database(database_path, read_only=True) as connection:
        active_years = connection.execute(
            """SELECT COALESCE(SUM(fa_year - ?), 0)
               FROM current_contracts
               WHERE team_id = ? AND fa_year > ?""",
            [season, team_id, season],
        ).fetchone()[0]
        dead_cap = connection.execute(
            """SELECT COALESCE(SUM(
                   CASE
                       WHEN ? = fa_year - 1 THEN 0
                       ELSE GREATEST(years - 0.5 * (? - YEAR(ts)), 0)
                   END
               ), 0)
               FROM contract_events
               WHERE team_id = ?
                 AND event_type = 'DROPPED'
                 AND ? >= YEAR(ts)
                 AND ? < fa_year""",
            [season, season, team_id, season, season],
        ).fetchone()[0]
        cap_trade_adjustment = connection.execute(
            """SELECT COALESCE(SUM(years), 0)
               FROM contract_events
               WHERE team_id = ?
                 AND event_type = 'CAP_TRADE'
                 AND YEAR(ts) = ?""",
            [team_id, season],
        ).fetchone()[0]
    return float(active_years + dead_cap - cap_trade_adjustment)


def record_roster_snapshot(
    snapshot_date: date,
    rosters: Mapping[str, Mapping[str, Any]],
    database_path: Path = DEFAULT_DATABASE_PATH,
) -> int:
    """Replace one date's roster rows from normalized Fantrax team rosters."""
    rows: set[tuple[date, str, str, str]] = set()
    for team_key, roster in rosters.items():
        team_id = str(roster.get("team_id", team_key))
        for collection_key, default_level in (
            ("mlb_roster", "MLB"),
            ("minors_roster", "minors"),
        ):
            entries = roster.get(collection_key, [])
            if not isinstance(entries, list):
                raise ValueError(f"{collection_key} must be a list for team {team_id}")
            for player in entries:
                if isinstance(player, str):
                    player_id = player
                    roster_level = default_level
                elif isinstance(player, Mapping):
                    player_id = player.get("id", player.get("fantrax_id", player.get("playerId")))
                    roster_level = str(player.get("roster_level", default_level))
                else:
                    continue
                if player_id is None:
                    LOGGER.warning("Skipping roster snapshot entry without player ID for team %s", team_id)
                    continue
                if roster_level not in {"MLB", "minors", "IL"}:
                    raise ValueError(f"Invalid roster level {roster_level!r} for player {player_id}")
                rows.add((snapshot_date, team_id, str(player_id), roster_level))

    with open_database(database_path) as connection:
        connection.execute("BEGIN TRANSACTION")
        connection.execute("DELETE FROM roster_snapshots WHERE snapshot_date = ?", [snapshot_date])
        if rows:
            connection.executemany(
                "INSERT INTO roster_snapshots VALUES (?, ?, ?, ?)",
                sorted(rows),
            )
        connection.execute("COMMIT")
    _export_after_write(database_path)
    return len(rows)


def update_contract_event(*args: Any, **kwargs: Any) -> None:
    """Reject mutation attempts; append a correcting event instead."""
    raise AppendOnlyViolation("contract_events is append-only; append a compensating event")


def delete_contract_event(*args: Any, **kwargs: Any) -> None:
    """Reject deletion attempts; append a correcting event instead."""
    raise AppendOnlyViolation("contract_events is append-only; events cannot be deleted")


def _validate_event(
    team_id: str,
    fantrax_id: str | None,
    event_type: str,
    years: float,
    fa_year: int | None,
    source: str,
    roster_level: str,
    *,
    form_ref: str | None = None,
    acquisition_type: str | None = None,
    announced_at: datetime | None = None,
) -> None:
    if not team_id:
        raise ValueError("team_id must not be empty")
    if event_type not in EVENT_TYPES:
        raise ValueError(f"Unsupported contract event type: {event_type}")
    if source not in EVENT_SOURCES:
        raise ValueError(f"Unsupported event source: {source}")
    if source == "form" and not form_ref:
        raise ValueError("Form-sourced events require a submission ref")
    if source == "form" and announced_at is None:
        raise ValueError("Form-sourced events require the submission timestamp")
    if acquisition_type is not None and acquisition_type not in {"drafted", "called_up", "waiver"}:
        raise ValueError(f"Unsupported acquisition type: {acquisition_type}")
    if not math.isfinite(float(years)):
        raise ValueError("years must be finite")
    if event_type == "CAP_TRADE":
        if fantrax_id is not None or fa_year is not None:
            raise ValueError("CAP_TRADE events are team-level and must not reference a player or FA year")
    else:
        if not fantrax_id:
            raise ValueError(f"{event_type} events require a Fantrax player ID")
        if fa_year is None:
            raise ValueError(f"{event_type} events require fa_year")
        if years < 0:
            raise ValueError("Contract and penalty years must not be negative")
        if event_type in {"SIGNED", "EXTENDED", "CALLED_UP", "DEFAULTED_1YR"} and years <= 0:
            raise ValueError(f"{event_type} events require a positive number of years")
    if roster_level not in ROSTER_LEVELS:
        raise ValueError(f"Unsupported contract roster level: {roster_level}")


def _export_after_write(database_path: Path) -> None:
    from sda.db.snapshots import export_text_snapshot

    try:
        export_text_snapshot(database_path)
    except (OSError, RuntimeError) as error:
        LOGGER.exception("Database write succeeded but its text snapshot failed: %s", error)
        raise