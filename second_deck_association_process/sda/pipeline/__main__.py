"""Command-line entry point for SDA pipeline operations."""

import argparse
import logging


def main() -> None:
    """Parse pipeline options and run the requested operation."""
    parser = argparse.ArgumentParser(description="Run Second Deck Association pipeline tasks.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Describe the pipeline run without performing any work.",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    if args.dry_run:
        logging.getLogger(__name__).info("Pipeline dry run; no tasks are configured yet.")
        return
    logging.getLogger(__name__).info("Pipeline is not implemented yet.")


if __name__ == "__main__":
    main()