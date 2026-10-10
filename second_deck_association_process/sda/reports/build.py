"""Build the SDA static reports site from DuckDB and saved Fantrax snapshots."""

from __future__ import annotations

import argparse
import csv
import json
import logging
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from sda.db.connection import DEFAULT_DATABASE_PATH, open_database
from sda.fantrax.normalize import DEFAULT_ALIAS_PATH, load_aliases, normalize_alias, normalize_rosters
from sda.fantrax.snapshots import load_snapshot
from sda.validation.engine import run_all

LOGGER = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "site"
DEFAULT_SNAPSHOT_ROOT = PROJECT_ROOT / "data" / "raw"
DEFAULT_DATA_DIR = PROJECT_ROOT / "data"
REPORT_PAGES = (
    ("index.html", "Contract Cap Tracker", "grid"),
    ("free_agents.html", "Free Agency Forecast", "free_agents"),
    ("roster_projections.html", "Team Roster Projections", "roster_projections"),
    ("waivers.html", "Waiver wire", "waivers"),
    ("standings.html", "Standings", "standings"),
    ("history.html", "League history", "history"),
    ("ledger.html", "Contract ledger", "ledger"),
    ("validation.html", "Validation report", "validation"),
    ("transactions.html", "Transaction log", "transactions"),
)
CATEGORIES = ("R", "HR", "RBI", "SB", "OBP", "QS", "K", "ERA", "WHIP", "SVH")


def report_multiyear_grid(
    teams: Sequence[Mapping[str, Any]],
    contracts: Sequence[Mapping[str, Any]],
    events: Sequence[Mapping[str, Any]],
    il_counts: Mapping[str, int],
    season: int,
    cap_years: int = 78,
    *,
    years: int = 7,
    il_players: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    il_roster_available: bool = False,
    minors: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Create team-by-season cap cells and a separate current-minors listing."""
    seasons = list(range(season, season + years))
    il_players = il_players or {}
    minors_by_team: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for player in minors:
        team_id = str(player.get("team_id", ""))
        birthdate = player.get("birthdate")
        minors_by_team[team_id].append(
            {
                "player_name": player.get("player_name") or player.get("fantrax_id", ""),
                "fantrax_id": player.get("fantrax_id"),
                "position": player.get("positions"),
            }
        )
    rows = []
    for team in teams:
        team_id = str(team["team_id"])
        cells = []
        for target_year in seasons:
            active_contracts = [
                contract
                for contract in contracts
                if str(contract["team_id"]) == team_id
                and int(contract["fa_year"]) > target_year
            ]
            active_years = sum(int(contract["fa_year"]) - target_year for contract in active_contracts)
            dead_cap = sum(
                _dead_cap_for_year(event, target_year)
                for event in events
                if str(event.get("team_id")) == team_id
                and str(event.get("event_type", "")).upper() == "DROPPED"
            )
            dead_cap_players = []
            for event in events:
                if str(event.get("team_id")) != team_id or str(event.get("event_type", "")).upper() != "DROPPED":
                    continue
                penalty = _dead_cap_for_year(event, target_year)
                if penalty > 0:
                    dead_cap_players.append(
                        {
                            "player_name": event.get("player_name") or event.get("fantrax_id") or "Unknown player",
                            "fantrax_id": event.get("fantrax_id"),
                            "position": event.get("positions"),
                            "dead_cap": penalty,
                            "fa_year": event.get("fa_year"),
                        }
                    )
            cap_trade = sum(
                float(event["years"])
                for event in events
                if str(event.get("team_id")) == team_id
                and str(event.get("event_type", "")).upper() == "CAP_TRADE"
                and _year(event.get("ts")) == target_year
            )
            il_relief = int(il_counts.get(team_id, 0)) if target_year == season else 0
            multi_year_contracts = [
                {
                    "player_name": contract.get("player_name") or contract["fantrax_id"],
                    "position": contract.get("positions"),
                    "fa_year": int(contract["fa_year"]),
                    "years_remaining": int(contract["fa_year"]) - target_year,
                    "roster_level": contract.get("roster_level", "MLB"),
                }
                for contract in active_contracts
                if int(contract["fa_year"]) - _year(contract.get("ts")) > 1
            ]
            one_year_contracts = [
                {
                    "player_name": contract.get("player_name") or contract["fantrax_id"],
                    "position": contract.get("positions"),
                    "fa_year": int(contract["fa_year"]),
                    "roster_level": contract.get("roster_level", "MLB"),
                }
                for contract in active_contracts
                if int(contract["fa_year"]) - _year(contract.get("ts")) <= 1
            ]
            cap_trade_events = [
                {
                    "years": float(event["years"]),
                    "note": event.get("note") or "",
                    "source": event.get("source") or "",
                }
                for event in events
                if str(event.get("team_id")) == team_id
                and str(event.get("event_type", "")).upper() == "CAP_TRADE"
                and _year(event.get("ts")) == target_year
            ]
            current_il_players = list(il_players.get(team_id, [])) if target_year == season else []
            committed = float(active_years) + dead_cap
            effective_cap = float(cap_years + il_relief + cap_trade)
            remaining = effective_cap - committed
            cells.append(
                {
                    "year": target_year,
                    "active_years": float(active_years),
                    "dead_cap": float(dead_cap),
                    "dead_cap_players": sorted(dead_cap_players, key=lambda item: str(item["player_name"]).casefold()),
                    "committed": committed,
                    "cap_trade": float(cap_trade),
                    "il_relief": il_relief,
                    "effective_cap": effective_cap,
                    "remaining": remaining,
                    "contracts": sorted(multi_year_contracts, key=lambda item: item["player_name"].casefold()),
                    "one_year_contracts": sorted(one_year_contracts, key=lambda item: item["player_name"].casefold()),
                    "cap_trade_events": cap_trade_events,
                    "il_players": sorted(current_il_players, key=lambda item: str(item.get("player_name", "")).casefold()),
                    "il_roster_known": il_roster_available and target_year == season,
                }
            )
        team_id = str(team["team_id"])
        rows.append({
            **team,
            "cells": cells,
            "minor_players": sorted(minors_by_team.get(team_id, []), key=lambda item: str(item["player_name"]).casefold()),
        })
    return {"season": season, "seasons": seasons, "teams": rows, "minor_rosters_available": il_roster_available}


POSITION_ORDER = ("C", "1B", "2B", "3B", "SS", "OF", "UT", "SP", "RP", "P")
POSITION_ALIASES = {"LF": "OF", "CF": "OF", "RF": "OF", "DH": "UT", "CI": "1B", "MI": "2B"}


def _primary_position(value: Any) -> str:
    parts = [part.strip().upper() for part in re.split(r"[,/|]", str(value or "")) if part.strip()]
    if not parts:
        return "UT"
    return POSITION_ALIASES.get(parts[0], parts[0])


def report_roster_projections(
    teams: Sequence[Mapping[str, Any]],
    contracts: Sequence[Mapping[str, Any]],
    minors: Sequence[Mapping[str, Any]],
    season: int,
    *,
    years: int = 7,
) -> dict[str, Any]:
    """Create per-team player-by-season grids grouped by position, longest contract first.

    Each player year carries ``on_roster`` (contract coverage) and ``projected``
    (reserved for future minor-league projections; always False for now).
    """
    seasons = list(range(season, season + years))
    rows = []
    for team in teams:
        team_id = str(team["team_id"])
        players: list[dict[str, Any]] = []
        contracted_ids = set()
        for contract in contracts:
            if str(contract["team_id"]) != team_id or int(contract["fa_year"]) <= season:
                continue
            contracted_ids.add(str(contract["fantrax_id"]))
            fa_year = int(contract["fa_year"])
            players.append(
                {
                    "player_name": contract.get("player_name") or contract["fantrax_id"],
                    "fantrax_id": contract["fantrax_id"],
                    "position": contract.get("positions"),
                    "primary_position": _primary_position(contract.get("positions")),
                    "fa_year": fa_year,
                    "signed": True,
                    "roster_level": contract.get("roster_level", "MLB"),
                    "years": [
                        {"year": year, "on_roster": year < fa_year, "projected": False}
                        for year in seasons
                    ],
                }
            )
        for player in minors:
            if str(player.get("team_id", "")) != team_id or str(player.get("fantrax_id")) in contracted_ids:
                continue
            players.append(
                {
                    "player_name": player.get("player_name") or player.get("fantrax_id", ""),
                    "fantrax_id": player.get("fantrax_id"),
                    "position": player.get("positions"),
                    "primary_position": _primary_position(player.get("positions")),
                    "fa_year": None,
                    "signed": False,
                    "roster_level": "MINORS",
                    "years": [{"year": year, "on_roster": False, "projected": False} for year in seasons],
                }
            )

        def sort_key(item: Mapping[str, Any]) -> tuple[int, int, str]:
            position = item["primary_position"]
            order = POSITION_ORDER.index(position) if position in POSITION_ORDER else len(POSITION_ORDER)
            return order, -(item["fa_year"] or 0), str(item["player_name"]).casefold()

        players.sort(key=sort_key)
        rows.append({**team, "players": players})
    return {"season": season, "seasons": seasons, "teams": rows}


def report_free_agents(
    contracts: Sequence[Mapping[str, Any]],
    teams: Sequence[Mapping[str, Any]],
    season: int,
    *,
    players: Mapping[str, Mapping[str, Any]] | None = None,
    stats_by_id: Mapping[str, Mapping[str, Any]] | None = None,
    stats_season: int | None = None,
    adp_by_id: Mapping[str, float] | None = None,
    adp_as_of: str | None = None,
    years: int = 7,
) -> dict[str, Any]:
    """Build one enriched, sortable row per player and free-agent year."""
    team_names = {str(team["team_id"]): team["team_name"] for team in teams}
    players = players or {}
    stats_by_id = stats_by_id or {}
    adp_by_id = adp_by_id or {}
    stats_season = stats_season if stats_season is not None else season - 1
    free_agents: list[dict[str, Any]] = []
    for contract in contracts:
        fa_year = int(contract["fa_year"])
        if not season <= fa_year <= season + years:
            continue
        fantrax_id = str(contract["fantrax_id"])
        player = players.get(fantrax_id, {})
        stats = stats_by_id.get(fantrax_id, {})
        position_text = str(player.get("positions") or "")
        positions = [part.strip() for part in re.split(r"[,/|]", position_text) if part.strip()]
        player_stats = stats_by_id.get(fantrax_id, {})
        free_agents.append(
            {
                "player_name": contract.get("player_name") or player.get("name") or fantrax_id,
                "fantrax_id": fantrax_id,
                "team_name": team_names.get(str(contract["team_id"]), str(contract["team_id"])),
                "team_id": str(contract["team_id"]),
                "position": ", ".join(positions) or None,
                "positions": positions,
                "age": _age(player.get("birthdate"), date(stats_season, 7, 1)),
                "fa_year": fa_year,
                "adp": adp_by_id.get(fantrax_id),
                "roster_level": contract.get("roster_level", "MLB"),
                **{
                    stat: player_stats.get(stat)
                    for stat in (
                        "runs", "obp", "home_runs", "stolen_bases", "strikeouts",
                        "era", "whip", "quality_starts", "saves_holds",
                    )
                },
            }
        )
    free_agents.sort(key=lambda item: (item["fa_year"], item["team_name"].casefold(), item["player_name"].casefold()))
    return {
        "season": season,
        "stats_season": stats_season,
        "stats_available": any(
            player["fantrax_id"] in stats_by_id
            and any(value is not None for value in stats_by_id[player["fantrax_id"]].values())
            for player in free_agents
        ),
        "adp_as_of": adp_as_of,
        "players": free_agents,
        "fa_years": sorted({player["fa_year"] for player in free_agents}),
        "positions": sorted({position for player in free_agents for position in player["positions"]}),
        "teams": sorted({player["team_name"] for player in free_agents}, key=str.casefold),
    }


def report_waivers(rows: Sequence[Mapping[str, Any]], *, limit: int = 10) -> dict[str, Any]:
    """Return the ten most-rostered players currently on waivers."""
    normalized = []
    for row in rows:
        roster_pct = _number(row.get("roster_pct"))
        normalized.append(
            {
                "player_name": row.get("player_name") or "Unknown player",
                "position": row.get("position") or "—",
                "dropped_by": row.get("dropped_by") or "—",
                "days_on_waivers": row.get("days_on_waivers"),
                "roster_pct": roster_pct,
                "status": row.get("status", "waiver"),
            }
        )
    normalized.sort(key=lambda item: item["roster_pct"] if item["roster_pct"] is not None else -1, reverse=True)
    return {"players": normalized[:limit]}


def report_standings(
    standings_payload: Any,
    matchup_payload: Any,
    teams: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Combine standings category totals and matchup category points by team."""
    team_data: dict[str, dict[str, Any]] = {}
    for team in teams:
        team_id = str(team["team_id"])
        team_data[team_id] = {
            "team_id": team_id,
            "team_name": team["team_name"],
            "rank": None,
            "record": "—",
            "totals": {category: None for category in CATEGORIES},
            "points": {category: None for category in CATEGORIES},
        }
    for record in _team_records(standings_payload):
        team_id = _team_id(record)
        if team_id not in team_data:
            continue
        row = team_data[team_id]
        row["rank"] = _first(record, "rank", "place", "regularSeasonRank")
        row["record"] = _first(record, "record", "points", "winLossTie") or "—"
        totals, points = _category_values(record)
        row["totals"].update(totals)
        row["points"].update(points)
    matchup_points: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for record in _team_records(matchup_payload):
        team_id = _team_id(record)
        if team_id not in team_data:
            continue
        _, points = _category_values(record)
        for category, value in points.items():
            numeric_value = _number(value)
            if numeric_value is not None:
                matchup_points[team_id][category] += numeric_value
    for team_id, category_points in matchup_points.items():
        team_data[team_id]["points"].update(category_points)
    return {"categories": list(CATEGORIES), "teams": list(team_data.values())}


def report_league_history(
    all_time_rows: Sequence[Mapping[str, Any]],
    yearly_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Prepare all-time and season history sections for rendering."""
    all_time = sorted(all_time_rows, key=lambda row: (-int(row.get("championships") or 0), str(row.get("team_name", ""))))
    yearly = sorted(yearly_rows, key=lambda row: int(row["season"]), reverse=True)
    return {"all_time": list(all_time), "yearly": list(yearly)}


def report_contract_ledger(events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Sort append-only contract events newest first for the audit page."""
    return {"events": sorted(events, key=lambda row: (_datetime(row.get("ts")) or datetime.min, int(row.get("event_id") or 0)), reverse=True)}


def report_validation(validation: Mapping[str, Any]) -> dict[str, Any]:
    """Return the validation report in template-ready plain data form."""
    return {
        "season": validation.get("season"),
        "summary": validation.get("summary", {}),
        "per_team": validation.get("per_team", {}),
        "league_results": validation.get("league_results", []),
    }


def report_transactions(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Prepare a newest-first transaction log from snapshot or CSV records."""
    ordered = sorted(rows, key=lambda row: str(row.get("date") or ""), reverse=True)
    return {"transactions": list(ordered)}


def build_site(
    season: int,
    database_path: Path = DEFAULT_DATABASE_PATH,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    *,
    snapshot_root: Path = DEFAULT_SNAPSHOT_ROOT,
    waiver_csv: Path | None = None,
    transactions_csv: Path | None = None,
    player_stats_csv: Path | None = None,
    stats_season: int | None = None,
    alias_path: Path = DEFAULT_ALIAS_PATH,
) -> Path:
    """Render five reports and three supporting views to a static site directory."""
    validation = run_all(season, database_path)
    data = _load_database_data(Path(database_path), season)
    roster_payload = _load_optional_snapshot("team_rosters", snapshot_root)
    if roster_payload is not None:
        _overlay_current_roster(data, roster_payload, season)
    standings_payload = _load_optional_snapshot("standings", snapshot_root)
    matchup_payload = _load_optional_snapshot("matchup_scores", snapshot_root)
    league_info = _load_optional_snapshot("league_info", snapshot_root)
    adp_payload = _load_optional_snapshot("adp", snapshot_root)
    adp_by_id = _player_ranks(adp_payload)
    adp_as_of = _latest_snapshot_date("adp", snapshot_root)
    stats_by_id, loaded_stats_season = _load_player_stats_csv(
        player_stats_csv,
        stats_season if stats_season is not None else season - 1,
    )
    waiver_rows = _load_waiver_rows(league_info, waiver_csv or _find_data_csv(snapshot_root, "waiver_wire.csv"), data["players"])
    transactions = _load_transactions(
        _load_optional_snapshot("transactions", snapshot_root),
        transactions_csv or _find_data_csv(snapshot_root, "transactions.csv"),
    )

    grid = report_multiyear_grid(
        data["teams"], data["contracts"], data["events"], data["il_counts"], season,
        data["cap_years"], il_players=data["il_players"], il_roster_available=data["il_roster_available"],
        minors=data["minors"],
    )
    reports = {
        "grid": grid,
        "free_agents": report_free_agents(
            data["contracts"],
            data["teams"],
            season,
            players=data["players"],
            stats_by_id=stats_by_id,
            stats_season=loaded_stats_season,
            adp_by_id=adp_by_id,
            adp_as_of=adp_as_of,
        ),
        "waivers": report_waivers(waiver_rows),
        "standings": report_standings(standings_payload, matchup_payload, data["teams"]),
        "history": report_league_history(data["all_time_history"], data["yearly_history"]),
        "ledger": report_contract_ledger(data["events"]),
        "validation": report_validation(validation),
        "transactions": report_transactions(transactions),
    }
    reports["roster_projections"] = report_roster_projections(
        data["teams"], data["contracts"], data["minors"], season,
    )
    reports["grid"]["minors"] = data["minors"]
    reports["grid"]["season"] = season

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    environment = Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=select_autoescape(("html", "xml")),
    )
    environment.filters["number"] = _format_number
    environment.filters["date"] = _format_date
    environment.filters["value"] = _display
    environment.filters["cell"] = _cell_value
    environment.filters["stat"] = _format_stat
    for filename, title, key in REPORT_PAGES:
        template = environment.get_template(f"{key}.html")
        html = template.render(
            title=title,
            active_page=key,
            season=season,
            report=reports[key],
            generated_at=datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z"),
        )
        (output_dir / filename).write_text(html, encoding="utf-8")
    (output_dir / "cap_tracker.html").unlink(missing_ok=True)
    (output_dir / "static").mkdir(parents=True, exist_ok=True)
    for asset in ("style.css", "sort.js"):
        (output_dir / "static" / asset).write_bytes((STATIC_DIR / asset).read_bytes())
    _write_forms_api(
        output_dir / "api",
        season,
        data,
        reports["grid"],
        _load_optional_snapshot("draft_results", snapshot_root),
        _load_recent_adds(Path(database_path), data["players"], data["teams"]),
        alias_path,
    )
    LOGGER.info("Built SDA static reports in %s", output_dir)
    return output_dir


def _write_forms_api(
    output_dir: Path,
    season: int,
    data: Mapping[str, Any],
    grid: Mapping[str, Any],
    draft_payload: Any,
    recent_adds: Sequence[Mapping[str, Any]],
    alias_path: Path,
) -> None:
    """Publish the Forms input feeds, writing valid empty values when source data is absent."""
    output_dir.mkdir(parents=True, exist_ok=True)
    cap_by_team = {}
    for team in grid.get("teams", []):
        current_cell = next(
            (cell for cell in team.get("cells", []) if int(cell["year"]) == season),
            None,
        )
        if current_cell is None:
            continue
        cap_by_team[str(team["team_id"])] = {
            "team_id": str(team["team_id"]),
            "team_name": str(team["team_name"]),
            "effective_cap": float(current_cell["effective_cap"]),
            "committed_years": float(current_cell["committed"]),
            "remaining": float(current_cell["remaining"]),
            "season": season,
        }

    canonical_players = {
        str(player["name"]): str(player_id)
        for player_id, player in data.get("players", {}).items()
        if player.get("name")
    }
    aliases = load_aliases(alias_path)
    for name, player_id in canonical_players.items():
        aliases.setdefault(normalize_alias(name), player_id)

    draft_results = _normalize_draft_results(draft_payload, data.get("players", {}))
    payloads = {
        "cap_state.json": {"season": season, "teams": cap_by_team},
        "players.json": {"players": canonical_players, "aliases": aliases},
        "recent_adds.json": list(recent_adds),
        "draft_results.json": draft_results,
    }
    for filename, payload in payloads.items():
        (output_dir / filename).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


def _load_recent_adds(
    database_path: Path,
    players: Mapping[str, Mapping[str, Any]],
    teams: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Return MLB additions observed within the latest seven-day roster window."""
    with open_database(database_path, read_only=True) as connection:
        snapshot_dates = [
            row[0]
            for row in connection.execute(
                "SELECT DISTINCT snapshot_date FROM roster_snapshots ORDER BY snapshot_date DESC LIMIT 2"
            ).fetchall()
        ]
        if not snapshot_dates:
            return []
        latest_date = snapshot_dates[0]
        if len(snapshot_dates) < 2:
            return []
        previous_date = snapshot_dates[1]
        rows = connection.execute(
            """SELECT team_id, fantrax_id, roster_level FROM roster_snapshots
               WHERE snapshot_date = ? AND roster_level IN ('MLB', 'IL')""",
            [latest_date],
        ).fetchall()
        previous = {
            (str(team_id), str(player_id))
            for team_id, player_id in connection.execute(
                "SELECT team_id, fantrax_id FROM roster_snapshots WHERE snapshot_date = ?",
                [previous_date],
            ).fetchall()
        }
    team_names = {str(team["team_id"]): str(team["team_name"]) for team in teams}
    result = []
    for team_id, fantrax_id, level in rows:
        key = (str(team_id), str(fantrax_id))
        if key in previous or (date.today() - latest_date).days > 7:
            continue
        result.append(
            {
                "fantrax_id": key[1],
                "name": players.get(key[1], {}).get("name") or key[1],
                "team_id": key[0],
                "team": team_names.get(key[0], key[0]),
                "add_date": latest_date.isoformat(),
                "roster_level": level,
            }
        )
    return result


def _normalize_draft_results(
    payload: Any,
    players: Mapping[str, Mapping[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Group Fantrax slow-draft picks by team ID for per-team prefilled Forms."""
    if not isinstance(payload, Mapping):
        return {}
    picks = payload.get("draftPicks", payload.get("picks", []))
    if not isinstance(picks, list):
        return {}
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for pick in picks:
        if not isinstance(pick, Mapping):
            continue
        team_id = _first(pick, "teamId", "teamID", "team_id")
        if team_id is None:
            continue
        player_id = _first(pick, "playerId", "playerID", "player_id")
        player = players.get(str(player_id), {}) if player_id is not None else {}
        result[str(team_id)].append(
            {
                "round": _first(pick, "round", "roundNumber"),
                "pick": _first(pick, "pick", "pickNumber", "overallPick"),
                "fantrax_id": str(player_id) if player_id is not None else None,
                "player_name": player.get("name") or _first(pick, "playerName", "name"),
            }
        )
    return {team_id: picks_for_team for team_id, picks_for_team in sorted(result.items())}


def _load_database_data(database_path: Path, season: int) -> dict[str, Any]:
    with open_database(database_path, read_only=True) as connection:
        teams = [
            {"team_id": str(team_id), "team_name": str(team_name), "manager": manager}
            for team_id, team_name, manager in connection.execute(
                "SELECT team_id, team_name, manager FROM teams ORDER BY team_name"
            ).fetchall()
        ]
        players = {
            str(player_id): {
                "fantrax_id": str(player_id),
                "name": name,
                "positions": positions,
                "mlb_team": mlb_team,
                "birthdate": birthdate,
                "career_ab": career_ab,
                "career_ip": career_ip,
            }
            for player_id, name, positions, mlb_team, birthdate, career_ab, career_ip in connection.execute(
                "SELECT fantrax_id, name, positions, mlb_team, birthdate, career_ab, career_ip FROM players"
            ).fetchall()
        }
        contract_rows = connection.execute(
            """SELECT c.event_id, c.ts, c.team_id, t.team_name, c.fantrax_id, p.name,
                      c.event_type, c.years, c.fa_year, c.roster_level, c.note, p.positions
               FROM current_contracts c
               JOIN teams t ON t.team_id = c.team_id
               JOIN players p ON p.fantrax_id = c.fantrax_id
               ORDER BY t.team_name, p.name"""
        ).fetchall()
        contracts = [
            {
                "event_id": row[0], "ts": row[1], "team_id": str(row[2]), "team_name": row[3],
                "fantrax_id": str(row[4]), "player_name": row[5], "event_type": row[6],
                "years": float(row[7]), "fa_year": int(row[8]), "roster_level": row[9], "note": row[10],
                "positions": row[11],
            }
            for row in contract_rows
        ]
        event_rows = connection.execute(
            """SELECT e.event_id, e.ts, e.recorded_at, e.team_id, t.team_name,
                      e.fantrax_id, p.name, e.event_type, e.years, e.fa_year, e.source,
                      e.note, e.approved_by, e.roster_level, e.form_ref,
                      e.acquisition_type, e.announced_at, p.positions
               FROM contract_events e
               JOIN teams t ON t.team_id = e.team_id
               LEFT JOIN players p ON p.fantrax_id = e.fantrax_id
               ORDER BY e.ts DESC, e.recorded_at DESC, e.event_id DESC"""
        ).fetchall()
        events = [
            {
                "event_id": row[0], "ts": row[1], "recorded_at": row[2], "team_id": str(row[3]),
                "team_name": row[4], "fantrax_id": row[5], "player_name": row[6],
                "event_type": row[7], "years": float(row[8]), "fa_year": row[9], "source": row[10],
                "note": row[11], "approved_by": row[12], "roster_level": row[13],
                "form_ref": row[14], "acquisition_type": row[15], "announced_at": row[16],
                "positions": row[17],
            }
            for row in event_rows
        ]
        latest_date = connection.execute("SELECT max(snapshot_date) FROM roster_snapshots").fetchone()[0]
        roster_rows = connection.execute(
            """SELECT r.team_id, r.fantrax_id, r.roster_level, p.name, p.birthdate,
                      p.career_ab, p.career_ip, p.positions
               FROM roster_snapshots r LEFT JOIN players p ON p.fantrax_id = r.fantrax_id
               WHERE r.snapshot_date = ? ORDER BY r.team_id, p.name""",
            [latest_date],
        ).fetchall() if latest_date else []
        il_counts: dict[str, int] = defaultdict(int)
        il_players: dict[str, list[dict[str, Any]]] = defaultdict(list)
        minors = []
        team_names = {team["team_id"]: team["team_name"] for team in teams}
        for row in roster_rows:
            team_id, player_id, level, name, birthdate, career_ab, career_ip, positions = row
            if level == "IL":
                il_counts[str(team_id)] += 1
                il_players[str(team_id)].append(
                    {
                        "fantrax_id": str(player_id),
                        "player_name": name or str(player_id),
                        "positions": positions,
                    }
                )
            if level == "minors":
                minors.append(
                    {
                        "team_id": str(team_id), "team_name": team_names.get(str(team_id), str(team_id)),
                        "fantrax_id": str(player_id), "player_name": name or str(player_id),
                        "age": _age(birthdate, date(season, 7, 1)),
                        "career_ab": career_ab, "career_ip": career_ip, "positions": positions,
                        "birthdate": birthdate,
                    }
                )
        all_time_rows = connection.execute(
            """SELECT h.team_id, t.team_name, h.seasons_played, h.all_time_w, h.all_time_l,
                      h.all_time_t, h.championships, h.runner_ups, h.regular_season_firsts,
                      h.playoff_appearances
               FROM team_all_time_history h JOIN teams t ON t.team_id = h.team_id
               ORDER BY h.championships DESC, t.team_name"""
        ).fetchall()
        all_time = [
            dict(zip(("team_id", "team_name", "seasons_played", "all_time_w", "all_time_l", "all_time_t",
                      "championships", "runner_ups", "regular_season_firsts", "playoff_appearances"), row, strict=True))
            for row in all_time_rows
        ]
        yearly_rows = connection.execute(
            """SELECT l.season, champion.team_name, runner_up.team_name, first_place.team_name,
                      l.prize_champion, l.prize_runner_up, l.notes,
                      best_team.team_name, best.w, best.l, best.t
               FROM league_history l
               LEFT JOIN teams champion ON champion.team_id = l.champion_team_id
               LEFT JOIN teams runner_up ON runner_up.team_id = l.runner_up_team_id
               LEFT JOIN teams first_place ON first_place.team_id = l.regular_season_first_team_id
               LEFT JOIN team_season_history best ON best.season = l.season AND best.regular_season_rank = 1
               LEFT JOIN teams best_team ON best_team.team_id = best.team_id
               ORDER BY l.season DESC"""
        ).fetchall()
        yearly = [
            {
                "season": row[0], "champion": row[1], "runner_up": row[2], "regular_season_first": row[3],
                "prize_champion": row[4], "prize_runner_up": row[5], "notes": row[6],
                "best_record_team": row[7], "best_record": _format_record(row[8], row[9], row[10]),
            }
            for row in yearly_rows
        ]
        config = connection.execute("SELECT cap_years FROM season_config WHERE season = ?", [season]).fetchone()
    return {
        "teams": teams,
        "players": players,
        "contracts": contracts,
        "events": events,
        "il_counts": dict(il_counts),
        "il_players": dict(il_players),
        "il_roster_available": latest_date is not None,
        "minors": minors,
        "all_time_history": all_time,
        "yearly_history": yearly,
        "cap_years": int(config[0]) if config else 78,
    }


def _overlay_current_roster(
    data: dict[str, Any],
    payload: Any,
    season: int,
) -> None:
    """Use the saved Fantrax roster as current IL/minors detail for reports."""
    rosters = normalize_rosters(payload, data["players"])
    team_names = {str(team["team_id"]): team["team_name"] for team in data["teams"]}
    il_counts: dict[str, int] = {}
    il_players: dict[str, list[dict[str, Any]]] = defaultdict(list)
    minors: list[dict[str, Any]] = []
    for team_id, roster in rosters.items():
        il_counts[team_id] = int(roster["il_slots_used"])
        for player in roster["mlb_roster"]:
            if player["roster_level"] != "IL":
                continue
            il_players[team_id].append(
                {
                    "fantrax_id": player["id"],
                    "player_name": player.get("name") or player["id"],
                    "positions": ", ".join(player.get("positions", [])),
                }
            )
        for player in roster["minors_roster"]:
            known_player = data["players"].get(player["id"], {})
            positions = player.get("positions") or known_player.get("positions") or []
            minors.append(
                {
                    "team_id": team_id,
                    "team_name": team_names.get(team_id, roster["team_name"]),
                    "fantrax_id": player["id"],
                    "player_name": player.get("name") or known_player.get("name") or player["id"],
                    "positions": ", ".join(positions) if isinstance(positions, list) else str(positions),
                    "birthdate": known_player.get("birthdate"),
                    "age": _age(known_player.get("birthdate"), date(season, 7, 1)),
                    "career_ab": known_player.get("career_ab"),
                    "career_ip": known_player.get("career_ip"),
                }
            )
    data["il_counts"] = il_counts
    data["il_players"] = dict(il_players)
    data["minors"] = minors
    data["il_roster_available"] = bool(rosters)


def _load_optional_snapshot(name: str, snapshot_root: Path) -> Any:
    try:
        return load_snapshot(name, root=snapshot_root)
    except FileNotFoundError:
        LOGGER.info("No %s snapshot available; rendering an empty state", name)
        return None


def _load_waiver_rows(
    league_info: Any,
    csv_path: Path | None,
    players: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    # Prefer Fantrax player-pool data from getLeagueInfo; fall back to the player-page CSV export.
    pool_rows = _waiver_rows_from_pool(league_info, players)
    if pool_rows:
        return pool_rows
    if csv_path is None or not csv_path.is_file():
        return []
    rows = []
    with csv_path.open(encoding="utf-8-sig", newline="") as csv_file:
        for record in csv.DictReader(csv_file):
            values = {_key(key): value.strip() for key, value in record.items() if key}
            status = values.get("status", values.get("availability", ""))
            if "waiver" not in status.casefold():
                continue
            player_name = _field(values, "player", "player_name", "name")
            player_id = _field(values, "fantrax_id", "player_id", "id")
            player = players.get(str(player_id), {})
            dropped_at = _date_value(_field(values, "dropped_at", "date_dropped", "waiver_date"))
            rows.append(
                {
                    "player_name": player_name or player.get("name") or player_id or "Unknown player",
                    "position": _field(values, "position", "positions") or player.get("positions"),
                    "dropped_by": _field(values, "dropped_by", "owner", "team"),
                    "dropped_at": dropped_at,
                    "days_on_waivers": (date.today() - dropped_at).days if dropped_at else None,
                    "roster_pct": _number(_field(values, "roster_pct", "rostered_pct", "roster_percentage", "roster")),
                    "status": status,
                }
            )
    return rows


def _waiver_rows_from_pool(
    payload: Any,
    players: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    candidates: list[tuple[Mapping[str, Any], bool]] = []

    def visit(value: Any, waiver_bucket: bool = False) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                normalized = _key(str(key))
                is_bucket = waiver_bucket or normalized in {"waivers", "waiverpool", "waiverwire", "waiverplayers"}
                if isinstance(child, Mapping):
                    if _field(child, "playerId", "fantraxId", "id") is not None:
                        candidates.append((child, is_bucket))
                    else:
                        visit(child, is_bucket)
                elif isinstance(child, list):
                    visit(child, is_bucket)
        elif isinstance(value, list):
            for child in value:
                visit(child, waiver_bucket)

    visit(payload)
    rows = []
    for record, in_waiver_bucket in candidates:
        status = str(_first(record, "status", "availability", "ownershipStatus") or "")
        if not in_waiver_bucket and "waiver" not in status.casefold():
            continue
        player_id = str(_first(record, "playerId", "fantraxId", "id") or "")
        player = players.get(player_id, {})
        dropped_at = _date_value(_first(record, "droppedAt", "dropDate", "waiverDate"))
        rows.append(
            {
                "player_name": _first(record, "name", "playerName") or player.get("name") or player_id,
                "position": _first(record, "position", "positions") or player.get("positions"),
                "dropped_by": _first(record, "droppedBy", "dropTeam", "previousOwner"),
                "dropped_at": dropped_at,
                "days_on_waivers": (date.today() - dropped_at).days if dropped_at else None,
                "roster_pct": _number(_first(record, "rosterPct", "rosteredPct", "rosterPercentage")),
                "status": status or "waiver",
            }
        )
    return rows


def _player_ranks(payload: Any) -> dict[str, float]:
    """Read optional player ranks/ADP so known top-ranked expiring players can be highlighted."""
    ranks: dict[str, float] = {}
    for record in _generic_records(payload):
        player_id = _first(record, "playerId", "fantraxId", "id")
        rank = _number(_first(record, "rank", "overallRank", "adp", "averageDraftPosition"))
        if player_id is not None and rank is not None:
            ranks[str(player_id)] = rank
    return ranks


def _latest_snapshot_date(name: str, snapshot_root: Path) -> str | None:
    paths = sorted(Path(snapshot_root).glob(f"????-??-??/{name}.json"), reverse=True)
    return paths[0].parent.name if paths else None


def _load_player_stats_csv(
    path: Path | None,
    latest_completed_season: int,
) -> tuple[dict[str, dict[str, float | None]], int]:
    """Load one completed-season Fantrax stats export keyed by Fantrax player ID."""
    if path is None:
        return {}, latest_completed_season
    with Path(path).open(encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        if reader.fieldnames is None:
            raise ValueError(f"Player stats CSV has no header: {path}")
        rows = [{_key(key): value for key, value in row.items() if key is not None} for row in reader]
    if not rows:
        return {}, latest_completed_season
    rows = [row for row in rows if any(str(value or "").strip() for value in row.values())]

    season_values = [
        int(value)
        for row in rows
        if (value := _number(_field(row, "season", "year", "stat season"))) is not None
        and value <= latest_completed_season
    ]
    selected_season = max(season_values) if season_values else latest_completed_season
    fields = {
        "runs": ("R", "runs"),
        "obp": ("OBP", "on base percentage", "on base pct"),
        "home_runs": ("HR", "home runs"),
        "stolen_bases": ("SB", "stolen bases"),
        "strikeouts": ("K", "SO", "strikeouts"),
        "era": ("ERA",),
        "whip": ("WHIP",),
        "quality_starts": ("QS", "quality starts"),
        "saves_holds": ("SVH", "SV HLD", "saves holds", "saves and holds"),
    }
    stats_by_id: dict[str, dict[str, float | None]] = {}
    for row in rows:
        row_season = _number(_field(row, "season", "year", "stat season"))
        if row_season is not None and int(row_season) != selected_season:
            continue
        player_id = _field(row, "Fantrax ID", "player ID", "playerId", "fantraxId", "ID")
        if not player_id:
            raise ValueError(f"Player stats CSV row has no Fantrax player ID: {path}")
        player_id = str(player_id).strip()
        if player_id in stats_by_id:
            raise ValueError(f"Player stats CSV has duplicate Fantrax ID {player_id} for {selected_season}")
        stats_by_id[player_id] = {
            field_name: _number(_field(row, *aliases))
            for field_name, aliases in fields.items()
        }
    return stats_by_id, selected_season


def _load_transactions(payload: Any, csv_path: Path | None) -> list[dict[str, Any]]:
    if payload is not None:
        rows = _team_records(payload)
        if rows:
            return [
                {
                    "date": _first(row, "date", "transactionDate", "timestamp", "ts"),
                    "team": _first(row, "teamName", "team", "teamId"),
                    "player": _first(row, "playerName", "player", "name"),
                    "transaction": _first(row, "type", "transactionType", "action"),
                    "details": _first(row, "details", "note", "description"),
                }
                for row in rows
            ]
    if csv_path is None or not csv_path.is_file():
        return []
    with csv_path.open(encoding="utf-8-sig", newline="") as csv_file:
        return [
            {_key(key): value.strip() for key, value in row.items() if key}
            for row in csv.DictReader(csv_file)
        ]


def _team_records(payload: Any) -> list[Mapping[str, Any]]:
    records: list[Mapping[str, Any]] = []

    def visit(value: Any, inherited_id: str | None = None) -> None:
        if isinstance(value, list):
            for item in value:
                visit(item)
            return
        if not isinstance(value, Mapping):
            return
        team_id = _team_id(value) or inherited_id
        team_name = _first(value, "teamName", "name", "displayName")
        if team_id is not None and team_name is not None:
            records.append(value)
            return
        for key, child in value.items():
            if isinstance(child, Mapping):
                visit(child, str(key))
            elif isinstance(child, list):
                visit(child)

    visit(payload)
    return records


def _generic_records(payload: Any) -> list[Mapping[str, Any]]:
    records: list[Mapping[str, Any]] = []

    def visit(value: Any) -> None:
        if isinstance(value, Mapping):
            if _first(value, "playerId", "fantraxId", "id") is not None:
                records.append(value)
            else:
                for child in value.values():
                    visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(payload)
    return records


def _category_values(record: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    totals: dict[str, Any] = {}
    points: dict[str, Any] = {}

    def visit(value: Any) -> None:
        if isinstance(value, Mapping):
            category = _first(value, "category", "statName", "stat", "name", "abbreviation", "code")
            normalized_category = str(category).upper() if category is not None else ""
            if normalized_category in CATEGORIES:
                total = _first(value, "total", "value", "statValue", "amount")
                point_value = _first(value, "points", "categoryPoints", "score", "categoryScore")
                if total is not None:
                    totals[normalized_category] = total
                if point_value is not None:
                    points[normalized_category] = point_value
            for key, child in value.items():
                normalized = str(key).upper()
                if normalized in CATEGORIES:
                    if isinstance(child, Mapping):
                        total = _first(child, "total", "value", "statValue", "amount")
                        point_value = _first(child, "points", "categoryPoints", "score", "categoryScore")
                        if total is not None:
                            totals[normalized] = total
                        if point_value is not None:
                            points[normalized] = point_value
                    elif not isinstance(child, (Mapping, list)):
                        totals[normalized] = child
                elif isinstance(child, (Mapping, list)):
                    visit(child)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(record)
    return totals, points


def _find_data_csv(snapshot_root: Path, filename: str) -> Path | None:
    project_data = snapshot_root.parent
    direct = project_data / filename
    if direct.is_file():
        return direct
    matches = sorted(snapshot_root.glob(f"????-??-??/{filename}"), reverse=True)
    return matches[0] if matches else None


def _dead_cap_for_year(event: Mapping[str, Any], season: int) -> float:
    try:
        drop_year = _year(event.get("ts"))
        fa_year = int(event["fa_year"])
        penalty = float(event["years"])
    except (KeyError, TypeError, ValueError):
        return 0.0
    if season < drop_year or season >= fa_year or season == fa_year - 1:
        return 0.0
    return max(penalty - 0.5 * (season - drop_year), 0.0)


def _team_id(record: Mapping[str, Any]) -> str | None:
    value = _first(record, "teamId", "teamID", "team_id", "id")
    return str(value) if value is not None else None


def _first(record: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if record.get(key) is not None:
            return record[key]
    return None


def _field(values: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = values.get(_key(key))
        if value not in (None, ""):
            return value
    return None


def _key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def _number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value).strip().replace("%", ""))
    except (TypeError, ValueError):
        return None


def _year(value: Any) -> int:
    if isinstance(value, (date, datetime)):
        return value.year
    if value is None:
        return 0
    return int(str(value)[:4])


def _datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _date_value(value: Any) -> date | None:
    parsed = _datetime(value)
    return parsed.date() if parsed else None


def _age(birthdate: date | None, as_of: date) -> int | None:
    if birthdate is None:
        return None
    return as_of.year - birthdate.year - ((as_of.month, as_of.day) < (birthdate.month, birthdate.day))


def _is_top_rank(value: Any) -> bool:
    rank = _number(value)
    return rank is not None and rank <= 100


def _format_number(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    return str(int(number)) if number.is_integer() else f"{number:.1f}"


def _format_stat(value: Any, stat_type: str = "integer") -> str:
    number = _number(value)
    if number is None:
        return "—"
    if stat_type == "three-decimal":
        return f"{number:.3f}"
    if stat_type == "two-decimal":
        return f"{number:.2f}"
    if stat_type == "one-decimal":
        return f"{number:.1f}"
    return _format_number(number)


def _format_date(value: Any) -> str:
    parsed = _date_value(value)
    return parsed.isoformat() if parsed else "—"


def _display(value: Any) -> str:
    return "—" if value is None or value == "" else str(value)


def _cell_value(cell: Mapping[str, Any]) -> str:
    committed = _format_number(cell.get("active_years", 0))
    dead = _format_number(cell.get("dead_cap", 0))
    return f"{committed} + {dead} dead" if float(cell.get("dead_cap") or 0) else committed


def _format_record(wins: Any, losses: Any, ties: Any) -> str:
    if wins is None or losses is None or ties is None:
        return "—"
    return f"{wins}-{losses}-{ties}"


def main(argv: list[str] | None = None) -> int:
    """Run report-site generation from the command line."""
    parser = argparse.ArgumentParser(description="Build the SDA static HTML reports site.")
    parser.add_argument("--season", type=int, required=True)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--snapshot-root", type=Path, default=DEFAULT_SNAPSHOT_ROOT)
    parser.add_argument("--waiver-csv", type=Path)
    parser.add_argument("--transactions-csv", type=Path)
    parser.add_argument("--player-stats-csv", type=Path, help="Official Fantrax export for the latest completed season.")
    parser.add_argument("--stats-season", type=int, help="Season represented by --player-stats-csv; defaults to season - 1.")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        output = build_site(
            args.season,
            args.database,
            args.output,
            snapshot_root=args.snapshot_root,
            waiver_csv=args.waiver_csv,
            transactions_csv=args.transactions_csv,
            player_stats_csv=args.player_stats_csv,
            stats_season=args.stats_season,
        )
    except (OSError, RuntimeError, ValueError) as error:
        LOGGER.error("Could not build reports: %s", error)
        return 1
    LOGGER.info("Built SDA reports at %s", output)
    return 0