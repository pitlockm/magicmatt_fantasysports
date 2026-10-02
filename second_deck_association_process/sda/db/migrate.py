"""One-time Google Sheets contract migration with a dry-run reconciliation gate."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import math
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml

from sda.db.connection import DEFAULT_DATABASE_PATH, open_database
from sda.db.ledger import EVENT_TYPES
from sda.db.snapshots import export_text_snapshot
from sda.fantrax.normalize import load_aliases, normalize_alias, normalize_rosters
from sda.fantrax.snapshots import load_snapshot

LOGGER = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE_DIR = Path("/Users/matthewpitlock/Development/SDACommishprocess/data/contractmigrationdata")
DEFAULT_DATA_DIR = PROJECT_ROOT / "data"
TEAM_TABS = (
    "Boe",
    "Maloun",
    "Hoffman",
    "Pecora",
    "C. Pelton",
    "W. Pelton",
    "Pitlock",
    "Riggen",
    "A. Rolain",
    "M. Rolain",
)
REQUIRED_TABS = TEAM_TABS
MINOR_MOVE_TYPES = {"dfa", "minors", "draft minors"}

BASE_CAP_YEARS = 78


@dataclass(frozen=True)
class MigrationEvent:
    """A fully resolved contract event ready for commissioner review."""

    ts: datetime
    team_id: str
    fantrax_id: str | None
    event_type: str
    years: float
    fa_year: int | None
    roster_level: str
    note: str
    migration_key: str
    sheet_il: bool = False


@dataclass(frozen=True)
class MigrationException:
    """An unimportable source row with enough context for manual resolution."""

    tab: str
    row_number: int
    player_name: str
    reason: str


@dataclass
class MigrationPlan:
    """Reconciliation data and exactly the events currently eligible to import."""

    batch_id: str
    season: int
    missing_tabs: list[str] = field(default_factory=list)
    sheet_player_rows: int = 0
    matched_player_rows: int = 0
    exceptions: list[MigrationException] = field(default_factory=list)
    events: list[MigrationEvent] = field(default_factory=list)
    cap_errors: list[str] = field(default_factory=list)
    cap_report: list[str] = field(default_factory=list)
    il_slots_by_team: dict[str, int] = field(default_factory=dict)
    fantrax_orphans: list[str] = field(default_factory=list)
    sheet_orphans: list[str] = field(default_factory=list)
    roster_audit_unavailable: str | None = None
    minors_audit: list[str] = field(default_factory=list)
    il_mismatches: list[str] = field(default_factory=list)
    player_positions: dict[str, str] = field(default_factory=dict)

    @property
    def can_commit(self) -> bool:
        return not self.missing_tabs and not self.exceptions and not self.cap_errors

    def render(self) -> str:
        """Format a complete plain-text reconciliation report."""
        lines = [
            f"SDA Sheet migration reconciliation (batch {self.batch_id})",
            f"Season: {self.season}",
            "",
            "REQUIRED TABS",
            "  All required tabs found." if not self.missing_tabs else "  Missing: " + ", ".join(self.missing_tabs),
            "",
            "PLAYER MATCH RATE",
            f"  {self.matched_player_rows}/{self.sheet_player_rows} Sheet player rows matched to Fantrax IDs.",
            "",
            "EXCEPTIONS",
        ]
        if self.exceptions:
            lines.extend(
                f"  {item.tab}, row {item.row_number}, {item.player_name or '<blank player>'}: {item.reason}"
                for item in self.exceptions
            )
        else:
            lines.append("  None.")
        lines.extend(["", "ORPHANS", "  Fantrax MLB roster with no Sheet row:"])
        lines.extend(f"    {item}" for item in self.fantrax_orphans)
        if not self.fantrax_orphans:
            lines.append("    None.")
        lines.append("  Sheet players on no Fantrax roster:")
        lines.extend(f"    {item}" for item in self.sheet_orphans)
        if not self.sheet_orphans:
            lines.append("    None.")
        if self.roster_audit_unavailable:
            lines.append(f"  Roster comparison unavailable: {self.roster_audit_unavailable}")
        lines.extend(["", "CAP SANITY (78 YEARS + FANTRAX IL RELIEF)"])
        lines.extend(f"  {item}" for item in self.cap_report)
        if not self.cap_report:
            lines.append("  No cap calculations available.")
        if self.cap_errors:
            lines.append("  MIGRATION ERRORS:")
            lines.extend(f"    {item}" for item in self.cap_errors)
        lines.extend(["", "SHEET IL FLAG / FANTRAX IL SLOT"])
        lines.extend(f"  {item}" for item in self.il_mismatches)
        if not self.il_mismatches:
            lines.append("  No discrepancies.")
        lines.extend(["", "DFA / MINORS AUDIT"])
        lines.extend(f"  {item}" for item in self.minors_audit)
        if not self.minors_audit:
            lines.append("  None.")
        lines.append(f"Planned ledger events: {len(self.events)}")
        lines.append("COMMIT GATE: " + ("ready" if self.can_commit else "blocked; resolve all listed issues first"))
        return "\n".join(lines)


class MissingMigrationTabsError(ValueError):
    """Raised when required CSV exports are missing."""


def find_tab_files(source_dir: Path) -> tuple[dict[str, Path], list[str]]:
    """Map required Sheet tab labels to CSV files and list missing tabs."""
    source_dir = Path(source_dir)
    candidates: dict[str, list[Path]] = defaultdict(list)
    for path in source_dir.glob("*.csv"):
        candidates[_normalize_tab(_tab_label_from_filename(path))].append(path)
    tabs: dict[str, Path] = {}
    missing: list[str] = []
    for tab in REQUIRED_TABS:
        matches = candidates.get(_normalize_tab(tab), [])
        if not matches:
            missing.append(tab)
        elif len(matches) > 1:
            raise ValueError(f"Multiple CSV exports found for tab {tab}: {', '.join(str(p) for p in matches)}")
        else:
            tabs[tab] = matches[0]
    return tabs, missing


def build_migration_plan(
    source_dir: Path = DEFAULT_SOURCE_DIR,
    database_path: Path = DEFAULT_DATABASE_PATH,
    *,
    data_dir: Path = DEFAULT_DATA_DIR,
    season: int | None = None,
    team_map: dict[str, str] | None = None,
) -> MigrationPlan:
    """Parse exports, resolve identities, and build a read-only reconciliation plan."""
    source_dir = Path(source_dir)
    database_path = Path(database_path)
    data_dir = Path(data_dir)
    tabs, missing_tabs = find_tab_files(source_dir)
    batch_id = _batch_id(tabs.values())
    if season is None:
        season = _configured_season(database_path)
    plan = MigrationPlan(batch_id=batch_id, season=season, missing_tabs=missing_tabs)
    if missing_tabs:
        return plan
    if not database_path.is_file():
        plan.exceptions.append(MigrationException("database", 0, "", f"Database not initialized: {database_path}"))
        return plan

    with open_database(database_path, read_only=True) as connection:
        team_rows = connection.execute("SELECT team_id, team_name, manager FROM teams").fetchall()
        player_ids = {
            str(row[0])
            for row in connection.execute("SELECT fantrax_id FROM players").fetchall()
        }
    alias_path = data_dir / "player_aliases.json"
    aliases = load_aliases(alias_path)
    team_lookup = _team_lookup(team_rows, team_map or {})
    inverse_aliases: dict[str, str] = {}
    for alias, fantrax_id in aliases.items():
        inverse_aliases[normalize_alias(alias)] = fantrax_id

    matched_sheet_players: set[tuple[str, str]] = set()
    team_name_by_id = {str(team_id): str(team_name) for team_id, team_name, _ in team_rows}
    for tab in TEAM_TABS:
        mapped_team = _resolve_team(tab, team_lookup)
        if mapped_team is None:
            plan.exceptions.append(
                MigrationException(tab, 0, "", "No exact Fantrax team mapping; configure --team-map")
            )
            continue
        header, records = _read_team_csv(tabs[tab])
        if header is None:
            plan.exceptions.append(MigrationException(tab, 0, "", "Could not find Player Name header row"))
            continue
        for row_number, record in records:
            player_name = _value(record, "player name")
            if not player_name:
                continue
            plan.sheet_player_rows += 1
            fantrax_id = inverse_aliases.get(normalize_alias(player_name))
            if not fantrax_id or fantrax_id not in player_ids:
                plan.exceptions.append(
                    MigrationException(tab, row_number, player_name, "No unambiguous Fantrax player ID alias")
                )
                continue
            plan.matched_player_rows += 1
            matched_sheet_players.add((mapped_team, fantrax_id))
            position = _value(record, "pos")
            if position:
                plan.player_positions[fantrax_id] = position

            move_type = _value(record, "move type")
            if not move_type or move_type.casefold() == "null":
                plan.exceptions.append(MigrationException(tab, row_number, player_name, "Move Type is blank/NULL"))
                continue
            fa_year = _parse_year(_value(record, "free agent year"))
            years = _parse_number(_value(record, "player contract years", "years", "contract years"))
            added_year = _parse_year(_value(record, "year added"))
            if fa_year is None:
                plan.exceptions.append(MigrationException(tab, row_number, player_name, "Invalid or missing Free Agent Year"))
                continue
            if years is None or years < 0:
                plan.exceptions.append(MigrationException(tab, row_number, player_name, "Invalid or missing Player Contract Years"))
                continue
            if added_year is None:
                plan.exceptions.append(MigrationException(tab, row_number, player_name, "Invalid or missing Year added"))
                continue

            roster_level = "minors" if _normalize_header(move_type) in MINOR_MOVE_TYPES else "MLB"
            is_il = _is_yes(_value(record, "il", "il yes"))
            dropped = _is_yes(_value(record, "dropped", "dropped yes"))
            note_parts = [f"migrated from {tab}", f"move_type={move_type}", f"migration_batch={batch_id}"]
            if is_il:
                note_parts.append("IL=Yes; cross-check Fantrax Inj Res slot")
            note = "; ".join(note_parts)
            migration_key = f"{tab}:{row_number}:{fantrax_id}"
            sign_event = MigrationEvent(
                ts=datetime(added_year, 1, 1),
                team_id=mapped_team,
                fantrax_id=fantrax_id,
                event_type="SIGNED",
                years=years,
                fa_year=fa_year,
                roster_level=roster_level,
                note=note,
                migration_key=migration_key,
                sheet_il=is_il,
            )
            plan.events.append(sign_event)
            if roster_level == "minors":
                plan.minors_audit.append(f"{tab} row {row_number}: {player_name} [{fantrax_id}], Move Type={move_type}")
            if dropped:
                penalty_years = _parse_number(
                    _value(record, "drop penalty years", "penalty years", "drop years")
                )
                drop_year = _parse_year(_value(record, "drop year", "year dropped"))
                if penalty_years is None or penalty_years < 0:
                    plan.exceptions.append(
                        MigrationException(tab, row_number, player_name, "Dropped=Yes but penalty years are missing/invalid")
                    )
                    continue
                if drop_year is None:
                    plan.exceptions.append(
                        MigrationException(tab, row_number, player_name, "Dropped=Yes but drop year is missing/invalid")
                    )
                    continue
                drop_event = MigrationEvent(
                    ts=datetime(drop_year, 1, 1),
                    team_id=mapped_team,
                    fantrax_id=fantrax_id,
                    event_type="DROPPED",
                    years=penalty_years,
                    fa_year=fa_year,
                    roster_level=roster_level,
                    note=f"migrated from {tab}; dropped; migration_batch={batch_id}",
                    migration_key=migration_key + ":dropped",
                    sheet_il=is_il,
                )
                plan.events.append(drop_event)

    fantrax_il_players = _reconcile_rosters(plan, data_dir, matched_sheet_players, team_name_by_id)
    _annotate_il_cross_checks(plan, fantrax_il_players)
    _reconcile_cap_sanity(plan, team_name_by_id)
    return plan


def commit_migration(
    plan: MigrationPlan,
    database_path: Path = DEFAULT_DATABASE_PATH,
) -> int:
    """Write one reviewed migration batch atomically and export a text snapshot."""
    if not plan.can_commit:
        raise ValueError("Migration is blocked until missing tabs and all exceptions are resolved")
    database_path = Path(database_path)
    batch_marker = f"migration_batch={plan.batch_id}"
    with open_database(database_path) as connection:
        connection.execute("BEGIN TRANSACTION")
        existing_count = connection.execute(
            "SELECT count(*) FROM contract_events WHERE source = 'migration' AND contains(note, ?)",
            [batch_marker],
        ).fetchone()[0]
        if existing_count:
            expected_count = len(plan.events)
            if existing_count != expected_count:
                connection.execute("ROLLBACK")
                raise RuntimeError(
                    f"Migration batch {plan.batch_id} is partially present: "
                    f"found {existing_count} events, expected {expected_count}"
                )
            connection.execute("ROLLBACK")
            return 0

        for fantrax_id, positions in plan.player_positions.items():
            connection.execute(
                "UPDATE players SET positions = ? WHERE fantrax_id = ?",
                [positions, fantrax_id],
            )
        for event in plan.events:
            if event.event_type not in EVENT_TYPES:
                raise ValueError(f"Invalid event type in migration plan: {event.event_type}")
            connection.execute(
                """INSERT INTO contract_events (
                       ts, team_id, fantrax_id, event_type, years, fa_year, source,
                       note, approved_by, roster_level
                   ) VALUES (?, ?, ?, ?, ?, ?, 'migration', ?, 'migration', ?)""",
                [
                    event.ts,
                    event.team_id,
                    event.fantrax_id,
                    event.event_type,
                    event.years,
                    event.fa_year,
                    event.note,
                    event.roster_level,
                ],
            )
        connection.execute("COMMIT")
    export_text_snapshot(database_path)
    return len(plan.events)


def _team_lookup(
    team_rows: list[tuple[Any, ...]],
    explicit_map: dict[str, str],
) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for team_id, team_name, manager in team_rows:
        for label in (team_name, manager):
            if label:
                lookup[normalize_alias(str(label))] = str(team_id)
    for tab_name, team_id in explicit_map.items():
        lookup[normalize_alias(tab_name)] = str(team_id)
    return lookup


def _resolve_team(tab: str, lookup: dict[str, str]) -> str | None:
    return lookup.get(normalize_alias(tab))


def _read_team_csv(path: Path) -> tuple[list[str] | None, list[tuple[int, dict[str, str]]]]:
    rows = _read_csv(path)
    for index, row in enumerate(rows):
        header = [_normalize_header(cell) for cell in row]
        if {"player name", "free agent year", "move type"}.issubset(set(header)):
            records: list[tuple[int, dict[str, str]]] = []
            for row_number, values in enumerate(rows[index + 1 :], start=index + 2):
                record = {
                    column: values[column_index].strip()
                    for column_index, column in enumerate(header)
                    if column and column_index < len(values)
                }
                if any(record.values()):
                    records.append((row_number, record))
            return header, records
    return None, []


def _read_csv(path: Path) -> list[list[str]]:
    with path.open(encoding="utf-8-sig", newline="") as csv_file:
        return list(csv.reader(csv_file))


def _normalize_header(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def _normalize_tab(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def _tab_label_from_filename(path: Path) -> str:
    return path.stem.rsplit(" - ", 1)[-1].strip()


def _value(record: dict[str, str], *names: str) -> str:
    for name in names:
        normalized = _normalize_header(name)
        value = record.get(normalized, "").strip()
        if value:
            return value
    return ""


def _parse_year(value: str) -> int | None:
    text = value.strip()
    if not text:
        return None
    match = re.search(r"(?:^|\D)(19\d{2}|20\d{2}|21\d{2})(?:\D|$)", text)
    if not match:
        return None
    year = int(match.group(1))
    return year if 1900 <= year <= 2199 else None


def _parse_number(value: str) -> float | None:
    text = value.strip().replace(",", "").replace("$", "")
    if not text:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _is_yes(value: str) -> bool:
    return value.strip().casefold() in {"yes", "y", "true", "1"}


def _batch_id(paths: Any) -> str:
    digest = hashlib.sha256()
    for path in sorted((Path(path) for path in paths), key=lambda item: str(item)):
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


def _configured_season(database_path: Path) -> int:
    if database_path.is_file():
        with open_database(database_path, read_only=True) as connection:
            has_config_table = connection.execute(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_schema = 'main' AND table_name = 'season_config'"
            ).fetchone()[0]
            if has_config_table:
                value = connection.execute("SELECT max(season) FROM season_config").fetchone()[0]
                if value is not None:
                    return int(value)
    config_path = PROJECT_ROOT / "config" / "season.yaml"
    with config_path.open(encoding="utf-8") as config_file:
        return int(yaml.safe_load(config_file)["season"])


def _reconcile_rosters(
    plan: MigrationPlan,
    data_dir: Path,
    sheet_players: set[tuple[str, str]],
    team_name_by_id: dict[str, str],
) -> set[tuple[str, str]]:
    try:
        payload = load_snapshot("team_rosters", root=Path(data_dir) / "raw")
    except FileNotFoundError:
        plan.roster_audit_unavailable = "no cached Fantrax team_rosters snapshot"
        plan.cap_errors.append("Cannot verify the cap allowance without a Fantrax roster snapshot")
        return set()
    rosters = normalize_rosters(payload)
    fantrax_mlb_players: set[tuple[str, str]] = set()
    fantrax_all_players: set[tuple[str, str]] = set()
    fantrax_il_players: set[tuple[str, str]] = set()
    for team_id, roster in rosters.items():
        plan.il_slots_by_team[str(team_id)] = int(roster["il_slots_used"])
        for player in roster["mlb_roster"]:
            player_key = (team_id, str(player["id"]))
            fantrax_mlb_players.add(player_key)
            fantrax_all_players.add(player_key)
            if player.get("roster_level") == "IL":
                fantrax_il_players.add(player_key)
        for player in roster["minors_roster"]:
            fantrax_all_players.add((team_id, str(player["id"])))
    missing_team_ids = set(team_name_by_id) - set(rosters)
    for team_id in sorted(missing_team_ids):
        plan.cap_errors.append(
            f"Cannot verify cap allowance: Fantrax roster missing for {team_name_by_id[team_id]} ({team_id})"
        )
    plan.fantrax_orphans = [
        f"{team_name_by_id.get(team_id, team_id)}: {player_id}"
        for team_id, player_id in sorted(fantrax_mlb_players - sheet_players)
    ]
    plan.sheet_orphans = [
        f"{team_name_by_id.get(team_id, team_id)}: {player_id}"
        for team_id, player_id in sorted(sheet_players - fantrax_all_players)
    ]
    return fantrax_il_players


def _annotate_il_cross_checks(
    plan: MigrationPlan,
    fantrax_il_players: set[tuple[str, str]],
) -> None:
    annotated_events: list[MigrationEvent] = []
    for event in plan.events:
        player_key = (event.team_id, str(event.fantrax_id))
        fantrax_il = player_key in fantrax_il_players
        if event.fantrax_id is not None:
            note = (
                f"{event.note}; Sheet IL={'Yes' if event.sheet_il else 'No'}; "
                f"Fantrax IL slot={'Yes' if fantrax_il else 'No'}"
            )
            event = replace(event, note=note)
            if event.sheet_il != fantrax_il and event.event_type == "SIGNED":
                plan.il_mismatches.append(
                    f"{event.team_id}: player {event.fantrax_id}, "
                    f"Sheet IL={'Yes' if event.sheet_il else 'No'}, "
                    f"Fantrax IL slot={'Yes' if fantrax_il else 'No'}"
                )
        annotated_events.append(event)
    plan.events = annotated_events


def _reconcile_cap_sanity(
    plan: MigrationPlan,
    team_name_by_id: dict[str, str],
) -> None:
    if not team_name_by_id:
        plan.cap_errors.append("Cannot calculate cap sanity: database has no seeded teams")
        return
    for team_id, team_name in sorted(team_name_by_id.items()):
        il_slots = plan.il_slots_by_team.get(team_id, 0)
        cap_limit = BASE_CAP_YEARS + il_slots
        committed = _planned_cap(plan.events, team_id, plan.season)
        plan.cap_report.append(
            f"{team_name}: {committed:g} committed / {cap_limit} allowed "
            f"(78 base + {il_slots} Fantrax IL slot(s))"
        )
        if committed > cap_limit + 1e-9:
            plan.cap_errors.append(
                f"{team_name}: {committed:g} committed years exceeds {cap_limit} "
                f"(78 base + {il_slots} Fantrax IL slot(s))"
            )


def _planned_cap(events: list[MigrationEvent], team_id: str, season: int) -> float:
    by_player: dict[str, list[MigrationEvent]] = defaultdict(list)
    total = 0.0
    for event in events:
        if event.team_id != team_id:
            continue
        if event.fantrax_id is not None:
            by_player[event.fantrax_id].append(event)
    for player_events in by_player.values():
        signed = next((event for event in player_events if event.event_type == "SIGNED"), None)
        dropped = next((event for event in player_events if event.event_type == "DROPPED"), None)
        if dropped is None:
            if signed and signed.fa_year and season < signed.fa_year:
                total += signed.fa_year - season
        elif dropped.fa_year and dropped.ts.year <= season < dropped.fa_year:
            total += 0.0 if season == dropped.fa_year - 1 else max(dropped.years - 0.5 * (season - dropped.ts.year), 0)
    return total


def _configured_defaults() -> tuple[Path, Path]:
    return DEFAULT_SOURCE_DIR, DEFAULT_DATABASE_PATH


def _build_parser() -> argparse.ArgumentParser:
    source_dir, database_path = _configured_defaults()
    parser = argparse.ArgumentParser(description="Reconcile and migrate SDA Google Sheets contract data.")
    parser.add_argument("--source-dir", type=Path, default=source_dir, help="Directory containing one CSV per required Sheet tab.")
    parser.add_argument("--database", type=Path, default=database_path)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--season", type=int, help="Season for cap reconciliation; defaults to DB/config season.")
    parser.add_argument("--team-map", type=Path, help="JSON object mapping Sheet tab labels to Fantrax team IDs.")
    parser.add_argument("--commit", action="store_true", help="Write only after a clean reconciliation; default is dry run.")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run dry-run reconciliation or commit a clean migration batch."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    explicit_map: dict[str, str] = {}
    if args.team_map:
        try:
            explicit_map = json.loads(args.team_map.read_text(encoding="utf-8"))
            if not isinstance(explicit_map, dict):
                raise ValueError("team map JSON must be an object")
        except (OSError, json.JSONDecodeError, ValueError) as error:
            LOGGER.error("Could not load team map %s: %s", args.team_map, error)
            return 2

    try:
        plan = build_migration_plan(
            args.source_dir,
            args.database,
            data_dir=args.data_dir,
            season=args.season,
            team_map=explicit_map,
        )
    except (OSError, ValueError, csv.Error) as error:
        LOGGER.error("Migration preflight failed: %s", error)
        return 2
    sys.stdout.write(plan.render() + "\n")
    if not args.commit:
        return 0 if not plan.missing_tabs else 2
    if not plan.can_commit:
        LOGGER.error("Migration was not committed; resolve all reconciliation exceptions first")
        return 2
    try:
        imported = commit_migration(plan, args.database)
    except (OSError, RuntimeError, ValueError) as error:
        LOGGER.error("Migration commit failed: %s", error)
        return 1
    LOGGER.info("Migration complete; wrote %d event(s)", imported)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
