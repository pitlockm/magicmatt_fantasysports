"""Command-line tools for SDA database initialization and snapshots."""

from __future__ import annotations

import argparse
import logging
from datetime import date
from pathlib import Path

from sda.db.connection import DEFAULT_DATABASE_PATH, open_database
from sda.db.history import derive_playoff_champion, finalize_season, update_current_season
from sda.db.schema import initialize_database, seed_players, seed_teams, sync_season_config
from sda.db.snapshots import export_text_snapshot
from sda.fantrax.snapshots import load_snapshot

LOGGER = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "season.yaml"
DEFAULT_DATA_DIR = PROJECT_ROOT / "data"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Initialize and snapshot the SDA DuckDB database.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Create schema and load local config/snapshots.")
    init_parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE_PATH)
    init_parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    init_parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    init_parser.add_argument("--dry-run", action="store_true", help="Describe setup without writing.")

    snapshot_parser = subparsers.add_parser("snapshot", help="Export all tables to dated CSV files.")
    snapshot_parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE_PATH)
    snapshot_parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    snapshot_parser.add_argument("--date", type=date.fromisoformat, default=date.today())
    snapshot_parser.add_argument("--dry-run", action="store_true", help="Describe export without writing.")

    history_update_parser = subparsers.add_parser(
        "history-update",
        help="Update the current season from the latest standings snapshot.",
    )
    history_update_parser.add_argument("--season", type=int, required=True)
    history_update_parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE_PATH)
    history_update_parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    history_update_parser.add_argument("--dry-run", action="store_true")

    history_finalize_parser = subparsers.add_parser(
        "history-finalize",
        help="Record commissioner-confirmed final season results.",
    )
    history_finalize_parser.add_argument("--season", type=int, required=True)
    history_finalize_parser.add_argument("--champion")
    history_finalize_parser.add_argument("--runner-up")
    history_finalize_parser.add_argument("--regular-season-first")
    history_finalize_parser.add_argument("--prize-champion", type=float)
    history_finalize_parser.add_argument("--prize-runner-up", type=float)
    history_finalize_parser.add_argument("--notes")
    history_finalize_parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE_PATH)
    history_finalize_parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    history_finalize_parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run a database subcommand and return a process exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    if args.command == "init":
        if args.dry_run:
            LOGGER.info("Would initialize %s using %s", args.database, args.config)
            return 0
        return _initialize(args.database, args.config, args.data_dir)

    if args.command == "history-update":
        if args.dry_run:
            LOGGER.info("Would update season %d from the latest standings snapshot", args.season)
            return 0
        try:
            standings = load_snapshot("standings", root=args.data_dir / "raw")
            count = update_current_season(args.season, standings, args.database)
        except (FileNotFoundError, OSError, RuntimeError, ValueError) as error:
            LOGGER.error("History update failed: %s", error)
            return 1
        LOGGER.info("Updated %d team history row(s) for %d", count, args.season)
        return 0

    if args.command == "history-finalize":
        if args.dry_run:
            LOGGER.info("Would finalize season %d", args.season)
            return 0
        champion_id = args.champion
        if champion_id is None:
            try:
                matchups = load_snapshot("matchup_scores", root=args.data_dir / "raw")
            except FileNotFoundError:
                matchups = None
            champion_id = derive_playoff_champion(matchups)
            if champion_id is None:
                LOGGER.error(
                    "Playoff payload does not unambiguously identify a champion; "
                    "provide --champion <team_id> after commissioner review"
                )
                return 2
        try:
            finalize_season(
                args.season,
                champion_id,
                args.runner_up,
                args.regular_season_first,
                args.database,
                prize_champion=args.prize_champion,
                prize_runner_up=args.prize_runner_up,
                notes=args.notes,
            )
        except (OSError, RuntimeError, ValueError) as error:
            LOGGER.error("History finalization failed: %s", error)
            return 1
        LOGGER.info("Finalized season %d", args.season)
        return 0

    if args.dry_run:
        LOGGER.info("Would export %s to %s/snapshots/%s", args.database, args.data_dir, args.date)
        return 0
    try:
        output_dir = export_text_snapshot(
            args.database,
            snapshot_date=args.date,
            snapshot_root=args.data_dir / "snapshots",
        )
    except (OSError, RuntimeError) as error:
        LOGGER.error("Could not export database snapshot: %s", error)
        return 1
    LOGGER.info("Exported database snapshot to %s", output_dir)
    return 0


def _initialize(database_path: Path, config_path: Path, data_dir: Path) -> int:
    try:
        initialize_database(database_path)
        with open_database(database_path) as connection:
            season = sync_season_config(connection, config_path)
            try:
                league_info = load_snapshot("league_info", root=data_dir / "raw")
            except FileNotFoundError:
                LOGGER.info("No cached league_info snapshot; skipping team seeding")
            else:
                team_count = seed_teams(connection, league_info)
                LOGGER.info("Seeded %d teams from cached league_info", team_count)

            try:
                player_ids = load_snapshot("player_ids", root=data_dir / "raw")
            except FileNotFoundError:
                LOGGER.info("No cached player_ids snapshot; skipping player seeding")
            else:
                player_count = seed_players(
                    connection,
                    player_ids,
                    alias_path=data_dir / "player_aliases.json",
                )
                LOGGER.info("Seeded %d players from cached player_ids", player_count)

        output_dir = export_text_snapshot(
            database_path,
            snapshot_root=data_dir / "snapshots",
        )
    except (OSError, RuntimeError, ValueError) as error:
        LOGGER.error("Database initialization failed: %s", error)
        return 1
    LOGGER.info("Initialized season %d and exported snapshot to %s", season, output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())