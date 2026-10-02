"""Standings accumulation and season-finalization helpers."""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from sda.db.connection import DEFAULT_DATABASE_PATH, open_database
from sda.db.snapshots import export_text_snapshot

LOGGER = logging.getLogger(__name__)
_STANDING_COLLECTIONS = ("standings", "teams", "teamStandings", "teamInfo")


class StandingsPayloadError(ValueError):
    """Raised when a Fantrax standings payload has no usable team records."""


def update_current_season(
    season: int,
    standings_payload: Any,
    database_path: Path = DEFAULT_DATABASE_PATH,
) -> int:
    """Upsert current cumulative standings into one row per team and season."""
    records = _extract_standing_records(standings_payload)
    if not records:
        raise StandingsPayloadError("No team records found in standings payload")

    written = 0
    with open_database(database_path) as connection:
        connection.execute("BEGIN TRANSACTION")
        for team_id, team_name, wins, losses, ties, rank, made_playoffs in records:
            connection.execute(
                """INSERT INTO teams (team_id, team_name) VALUES (?, ?)
                   ON CONFLICT (team_id) DO UPDATE SET team_name = excluded.team_name""",
                [team_id, team_name],
            )
            connection.execute(
                """INSERT INTO team_season_history (
                       season, team_id, w, l, t, regular_season_rank, made_playoffs
                   ) VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT (season, team_id) DO UPDATE SET
                       w = excluded.w,
                       l = excluded.l,
                       t = excluded.t,
                       regular_season_rank = excluded.regular_season_rank,
                       made_playoffs = COALESCE(
                           excluded.made_playoffs,
                           team_season_history.made_playoffs
                       )""",
                [season, team_id, wins, losses, ties, rank, made_playoffs],
            )
            written += 1
        connection.execute("COMMIT")
    export_text_snapshot(database_path)
    LOGGER.info("Updated %d team standings row(s) for %s", written, season)
    return written


def finalize_season(
    season: int,
    champion_team_id: str | None,
    runner_up_team_id: str | None,
    regular_season_first_team_id: str | None,
    database_path: Path = DEFAULT_DATABASE_PATH,
    *,
    prize_champion: float | None = None,
    prize_runner_up: float | None = None,
    notes: str | None = None,
) -> None:
    """Store season results and mark known champion/runner-up team finishes."""
    with open_database(database_path) as connection:
        known_ids = {
            str(row[0])
            for row in connection.execute("SELECT team_id FROM teams").fetchall()
        }
        for label, team_id in (
            ("champion", champion_team_id),
            ("runner-up", runner_up_team_id),
            ("regular-season first", regular_season_first_team_id),
        ):
            if team_id is not None and team_id not in known_ids:
                raise ValueError(f"Unknown {label} team ID: {team_id}")

        connection.execute("BEGIN TRANSACTION")
        connection.execute(
            """INSERT INTO league_history (
                   season, champion_team_id, runner_up_team_id,
                   regular_season_first_team_id, prize_champion, prize_runner_up, notes
               ) VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (season) DO UPDATE SET
                   champion_team_id = excluded.champion_team_id,
                   runner_up_team_id = excluded.runner_up_team_id,
                   regular_season_first_team_id = excluded.regular_season_first_team_id,
                   prize_champion = excluded.prize_champion,
                   prize_runner_up = excluded.prize_runner_up,
                   notes = excluded.notes""",
            [
                season,
                champion_team_id,
                runner_up_team_id,
                regular_season_first_team_id,
                prize_champion,
                prize_runner_up,
                notes,
            ],
        )
        connection.execute(
            """UPDATE team_season_history
               SET playoff_finish = CASE
                   WHEN team_id = ? THEN 'champion'
                   WHEN team_id = ? THEN 'runner_up'
                   ELSE playoff_finish
               END,
               made_playoffs = CASE
                   WHEN team_id IN (?, ?) THEN TRUE
                   ELSE made_playoffs
               END
               WHERE season = ? AND team_id IN (?, ?)""",
            [
                champion_team_id,
                runner_up_team_id,
                champion_team_id,
                runner_up_team_id,
                season,
                champion_team_id,
                runner_up_team_id,
            ],
        )
        connection.execute("COMMIT")
    export_text_snapshot(database_path)


def derive_playoff_champion(matchup_payload: Any) -> str | None:
    """Return a champion only when the payload explicitly identifies one."""
    explicit_keys = {"playoffChampionTeamId", "championTeamId"}
    found: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                if key in explicit_keys and child is not None:
                    found.add(str(child))
                elif isinstance(child, (Mapping, list)):
                    visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(matchup_payload)
    return next(iter(found)) if len(found) == 1 else None


def _extract_standing_records(
    payload: Any,
) -> list[tuple[str, str, int, int, int, int | None, bool | None]]:
    records: list[tuple[str, str, int, int, int, int | None, bool | None]] = []
    inherited: list[tuple[str | None, Any]] = []
    if isinstance(payload, list):
        inherited.extend((None, item) for item in payload)
    elif isinstance(payload, Mapping):
        for key in _STANDING_COLLECTIONS:
            value = payload.get(key)
            if isinstance(value, list):
                inherited.extend((None, item) for item in value)
                break
            if isinstance(value, Mapping):
                inherited.extend((str(team_id), team) for team_id, team in value.items())
                break
        else:
            inherited.extend((str(team_id), team) for team_id, team in payload.items())

    for inherited_id, item in inherited:
        if not isinstance(item, Mapping):
            continue
        team_id = _first_value(item, "teamId", "teamID", "team_id", "id") or inherited_id
        team_name = _first_value(item, "teamName", "name", "displayName")
        if team_id is None or team_name is None:
            continue
        wins_losses_ties = _parse_record(_first_value(item, "points", "record", "winLossTie"))
        wins = _integer_value(item, "w", "wins", "win")
        losses = _integer_value(item, "l", "losses", "loss")
        ties = _integer_value(item, "t", "ties", "tie")
        if wins_losses_ties is not None:
            wins = wins if wins is not None else wins_losses_ties[0]
            losses = losses if losses is not None else wins_losses_ties[1]
            ties = ties if ties is not None else wins_losses_ties[2]
        if wins is None or losses is None or ties is None:
            LOGGER.warning("Skipping standings row without a complete W-L-T record: team %s", team_id)
            continue
        rank = _integer_value(item, "regularSeasonRank", "rank", "place")
        made_playoffs = _bool_value(item, "madePlayoffs", "playoffQualified", "made_playoffs")
        records.append((str(team_id), str(team_name), wins, losses, ties, rank, made_playoffs))
    return records


def _parse_record(value: Any) -> tuple[int, int, int] | None:
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"\s*(\d+)\s*[-–]\s*(\d+)\s*[-–]\s*(\d+)\s*", value)
    return tuple(int(part) for part in match.groups()) if match else None


def _first_value(record: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if record.get(key) is not None:
            return record[key]
    return None


def _integer_value(record: Mapping[str, Any], *keys: str) -> int | None:
    value = _first_value(record, *keys)
    try:
        return int(value) if value is not None and str(value).strip() else None
    except (TypeError, ValueError):
        return None


def _bool_value(record: Mapping[str, Any], *keys: str) -> bool | None:
    value = _first_value(record, *keys)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"true", "yes", "1", "made playoffs", "playoff"}:
            return True
        if normalized in {"false", "no", "0", "missed playoffs"}:
            return False
    return None