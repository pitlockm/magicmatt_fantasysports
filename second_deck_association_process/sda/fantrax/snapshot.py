"""Pull Fantrax endpoints and save dated JSON snapshots."""

from __future__ import annotations

import argparse
import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from sda.fantrax.client import FantraxClient
from sda.fantrax.normalize import seed_aliases
from sda.fantrax.snapshots import save_snapshot

LOGGER = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Pull Fantrax data into dated JSON snapshots.")
    parser.add_argument("--all", action="store_true", help="Pull every supported endpoint.")
    parser.add_argument("--period", type=int, help="Roster/scoring period for period-based data.")
    parser.add_argument("--dry-run", action="store_true", help="List requests without calling Fantrax.")
    parser.add_argument(
        "--config",
        type=Path,
        default=PROJECT_ROOT / "config" / "season.yaml",
        help="Path to season configuration YAML.",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=PROJECT_ROOT / "data",
        help="Directory for API cache, snapshots, and aliases.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the Fantrax snapshot command and return a process exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    if not args.all:
        parser.error("--all is required")

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    if args.dry_run:
        LOGGER.info("Would pull all configured Fantrax endpoints into %s/raw", args.data_dir)
        return 0

    load_dotenv(PROJECT_ROOT / ".env", override=False)
    try:
        with args.config.open(encoding="utf-8") as config_file:
            season_config = yaml.safe_load(config_file)
        league_id = str(season_config["league_id"])
    except (OSError, KeyError, TypeError, yaml.YAMLError) as error:
        LOGGER.error("Could not load Fantrax league configuration from %s: %s", args.config, error)
        return 1

    user_secret_id = os.environ.get("FANTRAX_USER_SECRET_ID") or None
    client = FantraxClient(
        league_id,
        user_secret_id,
        cache_dir=args.data_dir / "cache",
    )
    endpoints: dict[str, Callable[[], Any]] = {
        "player_ids": client.get_player_ids,
        "league_info": client.get_league_info,
        "team_rosters": lambda: client.get_team_rosters(args.period),
        "standings": client.get_standings,
        "matchup_scores": lambda: client.get_matchup_scores(args.period),
        "draft_picks": client.get_draft_picks,
        "draft_results": client.get_draft_results,
        "adp": client.get_adp,
    }
    if user_secret_id:
        endpoints["leagues"] = client.get_leagues
    else:
        LOGGER.info("Skipping getLeagues; FANTRAX_USER_SECRET_ID is not configured")

    failed_endpoints: list[str] = []
    for name, fetch in endpoints.items():
        try:
            payload = fetch()
            snapshot_path = save_snapshot(name, payload, root=args.data_dir / "raw")
            if name == "player_ids":
                seed_aliases(payload, path=args.data_dir / "player_aliases.json")
            LOGGER.info("Saved Fantrax endpoint %s to %s", name, snapshot_path)
        except Exception as error:  # Keep a single endpoint failure from blocking the remaining pulls.
            LOGGER.exception("Fantrax endpoint %s failed: %s", name, error)
            failed_endpoints.append(name)

    if failed_endpoints:
        LOGGER.error("Fantrax snapshot run failed for: %s", ", ".join(failed_endpoints))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())