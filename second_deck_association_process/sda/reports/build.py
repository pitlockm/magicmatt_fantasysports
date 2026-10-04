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
from sda.fantrax.normalize import DEFAULT_ALIAS_PATH, load_aliases, normalize_alias
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
    ("index.html", "Multi-year grid", "grid"),
    ("free_agents.html", "Free-agent projection", "free_agents"),
    ("waivers.html", "Waiver wire", "waivers"),
    ("standings.html", "Standings", "standings"),
    ("history.html", "League history", "history"),
    ("cap_tracker.html", "Salary cap tracker", "cap_tracker"),
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
) -> dict[str, Any]:
    """Create team-by-season cap cells and a separate current-minors listing."""
    seasons = list(range(season, season + years))
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
                    "fa_year": int(contract["fa_year"]),
                    "years_remaining": int(contract["fa_year"]) - target_year,
                    "roster_level": contract.get("roster_level", "MLB"),
                }
                for contract in active_contracts
                if int(contract["fa_year"]) - _year(contract.get("ts")) > 1
            ]
            committed = float(active_years) + dead_cap
            effective_cap = float(cap_years + il_relief + cap_trade)
            remaining = effective_cap - committed
            cells.append(
                {
                    "year": target_year,
                    "active_years": float(active_years),
                    "dead_cap": float(dead_cap),
                    "committed": committed,
                    "cap_trade": float(cap_trade),
                    "il_relief": il_relief,
                    "effective_cap": effective_cap,
                    "remaining": remaining,
                    "contracts": sorted(multi_year_contracts, key=lambda item: item["player_name"].casefold()),
                }
            )
        rows.append({**team, "cells": cells})
    return {"season": season, "seasons": seasons, "teams": rows}


def report_free_agents(
    contracts: Sequence[Mapping[str, Any]],
    teams: Sequence[Mapping[str, Any]],
    season: int,
    *,
    years: int = 7,
) -> dict[str, Any]:
    """Group active deals by their first free-agent year."""
    team_names = {str(team["team_id"]): team["team_name"] for team in teams}
    by_year: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for contract in contracts:
        fa_year = int(contract["fa_year"])
        if not season <= fa_year <= season + years:
            continue
        signed_year = _year(contract.get("ts"))
        by_year[fa_year].append(
            {
                "player_name": contract.get("player_name") or contract["fantrax_id"],
                "fantrax_id": contract["fantrax_id"],
                "team_name": team_names.get(str(contract["team_id"]), str(contract["team_id"])),
                "team_id": str(contract["team_id"]),
                "signed_through": fa_year - 1,
                "fa_year": fa_year,
                "multi_year": fa_year - signed_year > 1,
                "roster_level": contract.get("roster_level", "MLB"),
                "star": _is_top_rank(contract.get("rank")),
            }
        )
    years_data = [
        {"year": year, "players": sorted(players, key=lambda item: (item["team_name"], item["player_name"]))}
        for year, players in sorted(by_year.items())
    ]
    return {"season": season, "years": years_data}


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


def report_cap_tracker(
    teams: Sequence[Mapping[str, Any]],
    contracts: Sequence[Mapping[str, Any]],
    events: Sequence[Mapping[str, Any]],
    il_counts: Mapping[str, int],
    season: int,
    cap_years: int = 78,
    *,
    years: int = 7,
) -> dict[str, Any]:
    """Prepare annual base, IL, trade, commitment, and remaining-cap values."""
    grid = report_multiyear_grid(teams, contracts, events, il_counts, season, cap_years, years=years)
    return {
        "season": season,
        "years": [
            {
                "team_id": team["team_id"],
                "team_name": team["team_name"],
                "base_cap": cap_years,
                **cell,
                "remaining": cell["effective_cap"] - cell["committed"],
            }
            for team in grid["teams"]
            for cell in cell_list(team)
        ],
    }


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
    alias_path: Path = DEFAULT_ALIAS_PATH,
) -> Path:
    """Render six reports and three supporting views to a static site directory."""
    validation = run_all(season, database_path)
    data = _load_database_data(Path(database_path), season)
    standings_payload = _load_optional_snapshot("standings", snapshot_root)
    matchup_payload = _load_optional_snapshot("matchup_scores", snapshot_root)
    league_info = _load_optional_snapshot("league_info", snapshot_root)
    adp_payload = _load_optional_snapshot("adp", snapshot_root)
    player_ranks = _player_ranks(adp_payload)
    for contract in data["contracts"]:
        contract["rank"] = player_ranks.get(str(contract["fantrax_id"]))
    waiver_rows = _load_waiver_rows(league_info, waiver_csv or _find_data_csv(snapshot_root, "waiver_wire.csv"), data["players"])
    transactions = _load_transactions(
        _load_optional_snapshot("transactions", snapshot_root),
        transactions_csv or _find_data_csv(snapshot_root, "transactions.csv"),
    )

    grid = report_multiyear_grid(
        data["teams"], data["contracts"], data["events"], data["il_counts"], season, data["cap_years"]
    )
    reports = {
        "grid": grid,
        "free_agents": report_free_agents(data["contracts"], data["teams"], season),
        "waivers": report_waivers(waiver_rows),
        "standings": report_standings(standings_payload, matchup_payload, data["teams"]),
        "history": report_league_history(data["all_time_history"], data["yearly_history"]),
        "cap_tracker": report_cap_tracker(
            data["teams"], data["contracts"], data["events"], data["il_counts"], season, data["cap_years"]
        ),
        "ledger": report_contract_ledger(data["events"]),
        "validation": report_validation(validation),
        "transactions": report_transactions(transactions),
    }
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
                      c.event_type, c.years, c.fa_year, c.roster_level, c.note
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
            }
            for row in contract_rows
        ]
        event_rows = connection.execute(
            """SELECT e.event_id, e.ts, e.recorded_at, e.team_id, t.team_name,
                      e.fantrax_id, p.name, e.event_type, e.years, e.fa_year, e.source,
                      e.note, e.approved_by, e.roster_level, e.form_ref,
                      e.acquisition_type, e.announced_at
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
        minors = []
        team_names = {team["team_id"]: team["team_name"] for team in teams}
        for row in roster_rows:
            team_id, player_id, level, name, birthdate, career_ab, career_ip, positions = row
            if level == "IL":
                il_counts[str(team_id)] += 1
            if level == "minors":
                minors.append(
                    {
                        "team_id": str(team_id), "team_name": team_names.get(str(team_id), str(team_id)),
                        "fantrax_id": str(player_id), "player_name": name or str(player_id),
                        "age": _age(birthdate, date(season, 7, 1)),
                        "career_ab": career_ab, "career_ip": career_ip, "positions": positions,
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
        "minors": minors,
        "all_time_history": all_time,
        "yearly_history": yearly,
        "cap_years": int(config[0]) if config else 78,
    }


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


def cell_list(team: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]:
    """Expose cap cells for report_cap_tracker without coupling it to DB handles."""
    return team["cells"]


def main(argv: list[str] | None = None) -> int:
    """Run report-site generation from the command line."""
    parser = argparse.ArgumentParser(description="Build the SDA static HTML reports site.")
    parser.add_argument("--season", type=int, required=True)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--snapshot-root", type=Path, default=DEFAULT_SNAPSHOT_ROOT)
    parser.add_argument("--waiver-csv", type=Path)
    parser.add_argument("--transactions-csv", type=Path)
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
        )
    except (OSError, RuntimeError, ValueError) as error:
        LOGGER.error("Could not build reports: %s", error)
        return 1
    LOGGER.info("Built SDA reports at %s", output)
    return 0