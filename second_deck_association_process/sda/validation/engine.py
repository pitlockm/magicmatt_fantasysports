"""Load SDA state, run validation rules, and apply one-day default actions."""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Mapping
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from sda.db.connection import DEFAULT_DATABASE_PATH, open_database
from sda.db.ledger import append_event
from sda.validation import rules

LOGGER = logging.getLogger(__name__)
_START_EVENTS = {"SIGNED", "EXTENDED", "CALLED_UP", "DEFAULTED_1YR"}
_ROSTERED_LEVELS = {"MLB", "IL", "minors"}


def run_all(
    season: int,
    database_path: Path = DEFAULT_DATABASE_PATH,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Run V1-V13 and return per-team results, league summary, and V12 actions."""
    current_time = (now or datetime.now()).replace(tzinfo=None)
    state = _load_state(Path(database_path), season)
    v12_results, actions = _apply_one_day_defaults(state, Path(database_path), current_time)
    if actions:
        state = _load_state(Path(database_path), season)

    per_team: dict[str, list[dict[str, Any]]] = {team_id: [] for team_id in state["team_names"]}
    league_results: list[dict[str, Any]] = []

    def add(result: dict[str, Any]) -> None:
        team_id = result.get("team_id")
        if team_id is None:
            league_results.append(result)
        else:
            per_team.setdefault(str(team_id), []).append(result)

    for result in v12_results:
        add(result)

    latest_rows = state["latest_rows"]
    counts: dict[str, dict[str, int]] = defaultdict(lambda: {"mlb": 0, "minors": 0, "il": 0})
    for row in latest_rows:
        team_id = str(row["team_id"])
        level = str(row["roster_level"])
        normalized = {**row, "team_id": team_id, "roster_level": level}
        if level in {"MLB", "IL"}:
            counts[team_id]["mlb"] += 1
        elif level == "minors":
            counts[team_id]["minors"] += 1
        if level == "IL":
            counts[team_id]["il"] += 1

    # V1, V2, V3, V4
    for team_id in state["team_names"]:
        roster_counts = counts[team_id]
        add(
            rules.v1_cap(
                team_id,
                state["cap_committed"].get(team_id, 0.0),
                roster_counts["il"],
                state["cap_years"],
            )
        )
    for event in state["events"]:
        if str(event.get("roster_level", "MLB")).casefold() == "minors":
            continue
        if str(event.get("event_type", "")).upper() in {"SIGNED", "CALLED_UP", "DEFAULTED_1YR"}:
            acquisition = _acquisition_type(event, state["draft_start"], state["draft_end"])
            add(rules.v2_contract_bounds(event, acquisition))
    for result in rules.v3_resign_tripwire(
        state["events"], season, state["draft_start"], state["draft_end"]
    ):
        add(result)
    for result in rules.v4_duplicates(latest_rows):
        add(result)

    # V5-V8
    active_contract_ids = set(state["active_contract_ids"])
    for row in latest_rows:
        if row["roster_level"] in {"MLB", "IL"}:
            add(rules.v5_coverage(str(row["team_id"]), str(row["fantrax_id"]), active_contract_ids))
    for result in rules.v6_fa_year_stability(state["events"]):
        add(result)
    for result in rules.v7_drop_penalties(
        state["events"], unreported_exits=state["unreported_exits"]
    ):
        add(result)
    shuttle_dates = _shuttle_dates(state["snapshots"])
    for row in latest_rows:
        team_id = str(row["team_id"])
        fantrax_id = str(row["fantrax_id"])
        if row["roster_level"] == "minors" and (team_id, fantrax_id) in active_contract_ids:
            add(
                rules.v8_minors_shuttle(
                    team_id,
                    fantrax_id,
                    has_active_contract=True,
                    included_in_cap=(team_id, fantrax_id) in state["cap_contract_ids"],
                    shuttle_dates=shuttle_dates.get((team_id, fantrax_id), ()),
                )
            )

    # V9-V13
    for team_id, fantrax_id, added_date, _level in state["roster_additions"]:
        player = state["players"].get(fantrax_id, {})
        add(
            rules.v9_pool_freeze(
                team_id,
                fantrax_id,
                player.get("real_draft_year"),
                added_date,
                season,
                state["freeze_date"],
            )
        )
    for team_id in state["team_names"]:
        roster_counts = counts[team_id]
        add(rules.v10_roster_max(team_id, roster_counts["mlb"], roster_counts["minors"], roster_counts["il"]))
        if state["min_mlb_roster"] is not None:
            add(rules.v11_roster_min(team_id, roster_counts["mlb"], state["min_mlb_roster"]))

    active_notes = state["active_notes"]
    for row in latest_rows:
        team_id = str(row["team_id"])
        fantrax_id = str(row["fantrax_id"])
        event_note = active_notes.get((team_id, fantrax_id), "")
        add(
            rules.v13_il_eligibility(
                team_id,
                fantrax_id,
                str(row["roster_level"]),
                state["players"].get(fantrax_id, {}).get("real_life_il"),
                is_minor=str(row["roster_level"]) == "minors",
                claims_il_relief="il=yes" in str(event_note).casefold(),
            )
        )

    applicable_rule_ids = [f"V{rule_number}" for rule_number in range(1, 14)]
    if state["min_mlb_roster"] is None:
        applicable_rule_ids.remove("V11")
    for team_id, results in per_team.items():
        present = {result["rule_id"] for result in results}
        for rule_id in applicable_rule_ids:
            if rule_id not in present:
                results.append(
                    {
                        "rule_id": rule_id,
                        "team_id": team_id,
                        "fantrax_id": None,
                        "passed": True,
                        "detail": "No applicable violation found.",
                        "review_required": False,
                    }
                )

    results = [result for rows in per_team.values() for result in rows] + league_results
    summary = {
        "checks": len(results),
        "passed": sum(bool(result["passed"]) for result in results),
        "failed": sum(not bool(result["passed"]) for result in results),
        "review_required": sum(bool(result.get("review_required")) for result in results),
        "actions_taken": actions,
    }
    return {
        "season": season,
        "per_team": per_team,
        "league_results": league_results,
        "summary": summary,
    }


def _load_state(database_path: Path, season: int) -> dict[str, Any]:
    with open_database(database_path, read_only=True) as connection:
        team_names = {
            str(team_id): str(team_name)
            for team_id, team_name in connection.execute(
                "SELECT team_id, team_name FROM teams ORDER BY team_id"
            ).fetchall()
        }
        player_rows = connection.execute(
            "SELECT fantrax_id, real_draft_year, real_life_il FROM players"
        ).fetchall()
        players = {
            str(player_id): {"real_draft_year": draft_year, "real_life_il": real_life_il}
            for player_id, draft_year, real_life_il in player_rows
        }
        event_cursor = connection.execute(
            """SELECT event_id, ts, team_id, fantrax_id, event_type, years, fa_year,
                      source, note, roster_level
               FROM contract_events ORDER BY ts, recorded_at, event_id"""
        )
        event_columns = [column[0] for column in event_cursor.description]
        events = [dict(zip(event_columns, row, strict=True)) for row in event_cursor.fetchall()]
        latest_snapshot = connection.execute(
            "SELECT max(snapshot_date) FROM roster_snapshots"
        ).fetchone()[0]
        snapshot_rows = connection.execute(
            """SELECT snapshot_date, team_id, fantrax_id, roster_level
               FROM roster_snapshots ORDER BY snapshot_date, team_id, fantrax_id"""
        ).fetchall()
        config = connection.execute(
            """SELECT cap_years, freeze_date, min_mlb_roster, draft_start, draft_end
               FROM season_config WHERE season = ?""",
            [season],
        ).fetchone()
        if config is None:
            cap_years, freeze_date, min_mlb_roster, draft_start, draft_end = 78, None, None, None, None
        else:
            cap_years, freeze_date, min_mlb_roster, draft_start, draft_end = config
        active_cursor = connection.execute(
            "SELECT team_id, fantrax_id, fa_year, note FROM current_contracts WHERE fa_year > ?",
            [season],
        )
        active_rows = active_cursor.fetchall()
        announcement_rows = connection.execute(
            "SELECT team_id, fantrax_id, announced_at FROM announcements"
        ).fetchall()
        pending_rows = connection.execute(
            "SELECT team_id, fantrax_id, created_at FROM pending_contracts"
        ).fetchall()

        cap_committed: dict[str, float] = {}
        cap_adjustments: dict[str, float] = {}
        for team_id in team_names:
            active_years = connection.execute(
                """SELECT COALESCE(SUM(fa_year - ?), 0) FROM current_contracts
                   WHERE team_id = ? AND fa_year > ?""",
                     [season, team_id, season],
            ).fetchone()[0]
            dead_cap = connection.execute(
                """SELECT COALESCE(SUM(CASE
                       WHEN ? = fa_year - 1 THEN 0
                       ELSE GREATEST(years - 0.5 * (? - YEAR(ts)), 0)
                   END), 0)
                   FROM contract_events
                   WHERE team_id = ? AND event_type = 'DROPPED'
                     AND ? >= YEAR(ts) AND ? < fa_year""",
                [season, season, team_id, season, season],
            ).fetchone()[0]
            cap_trades = connection.execute(
                """SELECT COALESCE(SUM(years), 0) FROM contract_events
                   WHERE team_id = ? AND event_type = 'CAP_TRADE' AND YEAR(ts) = ?""",
                [team_id, season],
            ).fetchone()[0]
            cap_adjustments[team_id] = float(dead_cap - cap_trades)
            cap_committed[team_id] = float(active_years + dead_cap - cap_trades)

        active_contract_ids = {
            (str(team_id), str(fantrax_id))
            for team_id, fantrax_id, fa_year, _note in active_rows
            if fantrax_id is not None
        }
        active_contract_years = {
            (str(team_id), str(fantrax_id)): float(fa_year - season)
            for team_id, fantrax_id, fa_year, _note in active_rows
            if fantrax_id is not None
        }
        team_active_years: dict[str, float] = defaultdict(float)
        for (team_id, _fantrax_id), years in active_contract_years.items():
            team_active_years[team_id] += years
        cap_contract_ids = set()
        for key, years in active_contract_years.items():
            team_id, _fantrax_id = key
            observed_contribution = (
                cap_committed[team_id]
                - cap_adjustments[team_id]
                - (team_active_years[team_id] - years)
            )
            if abs(observed_contribution - years) <= 1e-9:
                cap_contract_ids.add(key)
        active_notes = {
            (str(team_id), str(fantrax_id)): str(note or "")
            for team_id, fantrax_id, _fa_year, note in active_rows
            if fantrax_id is not None
        }

    snapshots: dict[date, dict[tuple[str, str], str]] = defaultdict(dict)
    for snapshot_date, team_id, fantrax_id, roster_level in snapshot_rows:
        snapshots[snapshot_date][(str(team_id), str(fantrax_id))] = str(roster_level)
    latest_rows = []
    if latest_snapshot is not None:
        latest_rows = [
            {"team_id": team_id, "fantrax_id": fantrax_id, "roster_level": roster_level}
            for (team_id, fantrax_id), roster_level in snapshots[latest_snapshot].items()
        ]

    roster_additions: list[tuple[str, str, datetime, str]] = []
    dates = sorted(snapshots)
    for previous_date, snapshot_date in zip(dates, dates[1:]):
        previous = snapshots[previous_date]
        current = snapshots[snapshot_date]
        for (team_id, fantrax_id), level in current.items():
            if (team_id, fantrax_id) not in previous and level in _ROSTERED_LEVELS:
                roster_additions.append(
                    (team_id, fantrax_id, datetime.combine(snapshot_date, time.min), level)
                )

    unreported_exits = _find_unreported_exits(snapshots, events)
    announcements = [
        {"team_id": str(team_id), "fantrax_id": str(fantrax_id), "announced_at": announced_at}
        for team_id, fantrax_id, announced_at in announcement_rows
    ]
    announcements.extend(
        {"team_id": str(team_id), "fantrax_id": str(fantrax_id), "announced_at": created_at}
        for team_id, fantrax_id, created_at in pending_rows
    )
    return {
        "team_names": team_names,
        "players": players,
        "events": events,
        "latest_rows": latest_rows,
        "snapshots": snapshots,
        "roster_additions": roster_additions,
        "unreported_exits": unreported_exits,
        "cap_years": int(cap_years),
        "freeze_date": freeze_date,
        "min_mlb_roster": min_mlb_roster,
        "draft_start": draft_start,
        "draft_end": draft_end,
        "active_contract_ids": active_contract_ids,
        "cap_contract_ids": cap_contract_ids,
        "cap_adjustments": cap_adjustments,
        "active_contract_years": active_contract_years,
        "active_notes": active_notes,
        "cap_committed": cap_committed,
        "announcements": announcements,
    }


def _apply_one_day_defaults(
    state: Mapping[str, Any],
    database_path: Path,
    now: datetime,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    results: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []
    existing_defaults = {
        (str(event["team_id"]), str(event["fantrax_id"]), event["ts"].date())
        for event in state["events"]
    }
    existing_contracts = set(state["active_contract_ids"])
    for team_id, fantrax_id, added_at, level in state["roster_additions"]:
        if level == "minors":
            continue
        if added_at + timedelta(days=1) > now:
            continue
        result = rules.v12_one_day_signing(team_id, fantrax_id, added_at, state["announcements"])
        results.append(result)
        if result["passed"] or (team_id, fantrax_id, added_at.date()) in existing_defaults:
            continue
        if (team_id, fantrax_id) in existing_contracts:
            result["detail"] += " Existing contract retained; no duplicate default event was written."
            continue
        event_id = append_event(
            team_id,
            fantrax_id,
            "DEFAULTED_1YR",
            1,
            added_at.year + 1,
            "system",
            ts=added_at,
            note="no announcement within 1 day - defaulted",
            database_path=database_path,
        )
        action = {
            "event_id": event_id,
            "event_type": "DEFAULTED_1YR",
            "team_id": team_id,
            "fantrax_id": fantrax_id,
            "detail": "Appended the one-year default contract.",
        }
        actions.append(action)
        LOGGER.warning("Applied V12 one-year default for %s on team %s", fantrax_id, team_id)
    return results, actions


def _find_unreported_exits(
    snapshots: Mapping[date, Mapping[tuple[str, str], str]],
    events: list[dict[str, Any]],
) -> list[dict[str, str]]:
    exits: list[dict[str, str]] = []
    dates = sorted(snapshots)
    for previous_date, current_date in zip(dates, dates[1:]):
        previous = snapshots[previous_date]
        current = snapshots[current_date]
        current_player_ids = {player_id for _, player_id in current}
        for team_id, fantrax_id in previous:
            if (team_id, fantrax_id) in current or fantrax_id in current_player_ids:
                continue
            last_event = _latest_contract_event(events, team_id, fantrax_id, previous_date)
            if not last_event or str(last_event.get("event_type", "")).upper() not in _START_EVENTS:
                continue
            fa_year = last_event.get("fa_year")
            if fa_year is None or int(fa_year) <= previous_date.year:
                continue
            dropped = any(
                str(event.get("team_id")) == team_id
                and str(event.get("fantrax_id")) == fantrax_id
                and str(event.get("event_type", "")).upper() == "DROPPED"
                and previous_date < _date_value(event.get("ts")) <= current_date
                for event in events
            )
            if not dropped:
                exits.append({"team_id": team_id, "fantrax_id": fantrax_id})
    return exits


def _latest_contract_event(
    events: list[dict[str, Any]], team_id: str, fantrax_id: str, as_of: date
) -> dict[str, Any] | None:
    eligible = [
        event
        for event in events
        if str(event.get("team_id")) == team_id
        and str(event.get("fantrax_id")) == fantrax_id
        and _date_value(event.get("ts")) <= as_of
    ]
    return max(eligible, key=lambda event: (event.get("ts"), event.get("event_id", 0))) if eligible else None


def _shuttle_dates(
    snapshots: Mapping[date, Mapping[tuple[str, str], str]],
) -> dict[tuple[str, str], list[date]]:
    histories: dict[tuple[str, str], list[tuple[date, str]]] = defaultdict(list)
    for snapshot_date in sorted(snapshots):
        for key, level in snapshots[snapshot_date].items():
            histories[key].append((snapshot_date, level))
    results: dict[tuple[str, str], list[date]] = {}
    for key, history in histories.items():
        moves = [
            snapshot_date
            for (previous_date, previous_level), (snapshot_date, level) in zip(history, history[1:])
            if (previous_level == "minors") != (level == "minors")
        ]
        results[key] = moves
    return results


def _acquisition_type(event: Mapping[str, Any], draft_start: date | None, draft_end: date | None) -> str:
    event_type = str(event.get("event_type", "")).upper()
    note = str(event.get("note") or "").casefold()
    if event_type == "CALLED_UP":
        return "called_up"
    if "waiver" in note or "free agent" in note or "free-agent" in note:
        return "waiver"
    if "draft" in note:
        return "draft"
    event_value = event.get("ts")
    event_date = event_value.date() if isinstance(event_value, datetime) else event_value
    if isinstance(event_date, date) and draft_start and draft_end:
        if draft_start <= event_date <= draft_end:
            return "draft"
    return "waiver"


def _date_value(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def main(argv: list[str] | None = None) -> int:
    """Run validation CLI and return nonzero when any hard rule fails."""
    import argparse

    parser = argparse.ArgumentParser(description="Validate SDA contracts, rosters, and cap rules.")
    parser.add_argument("--season", type=int, required=True)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE_PATH)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        report = run_all(args.season, args.database)
    except (OSError, RuntimeError, ValueError) as error:
        LOGGER.error("Validation could not run: %s", error)
        return 2

    summary = report["summary"]
    print(f"SDA validation for {args.season}: {summary['passed']} passed, {summary['failed']} failed")
    for team_id, results in report["per_team"].items():
        failures = [result for result in results if not result["passed"]]
        reviews = [result for result in results if result.get("review_required")]
        print(f"{team_id}: {len(results) - len(failures)} passed, {len(failures)} failed, {len(reviews)} review")
        for result in failures + reviews:
            print(f"  {result['rule_id']}: {result['detail']}")
    for result in report["league_results"]:
        print(f"{result['rule_id']}: {result['detail']}")
    for action in summary["actions_taken"]:
        print(f"ACTION {action['event_type']}: {action['detail']} ({action['fantrax_id']})")
    return 1 if summary["failed"] else 0