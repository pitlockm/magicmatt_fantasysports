"""Command-line tools for minor-leaguer bio refreshes."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from sda.db.connection import DEFAULT_DATABASE_PATH
from sda.mlb.client import refresh_minor_bios

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def main(argv: list[str] | None = None) -> int:
    """Refresh MLB bio fields for players in the latest minors roster snapshot."""
    parser = argparse.ArgumentParser(description="Refresh minor-leaguer bios from MLB Stats API.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    refresh_parser = subparsers.add_parser("refresh", help="Refresh rostered player bios.")
    refresh_parser.add_argument("--minors-only", action="store_true", required=True)
    refresh_parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE_PATH)
    refresh_parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    if args.dry_run:
        logging.getLogger(__name__).info("Would refresh minor-leaguer bios using %s", args.database)
        return 0
    try:
        refreshed = refresh_minor_bios(args.database)
    except (OSError, RuntimeError, ValueError) as error:
        logging.getLogger(__name__).error("Minor-leaguer bio refresh failed: %s", error)
        return 1
    logging.getLogger(__name__).info("Refreshed bio fields for %d minor leaguer(s)", refreshed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())