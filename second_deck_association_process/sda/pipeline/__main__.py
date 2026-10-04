"""Run the SDA nightly ingestion, validation, reports, and publishing pipeline."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import shutil
import subprocess
import tempfile
from datetime import date, datetime
from pathlib import Path
from typing import Any

import requests
import yaml

from sda.db.connection import DEFAULT_DATABASE_PATH, open_database
from sda.db.history import update_current_season
from sda.db.schema import (
    initialize_database,
    seed_players,
    seed_teams,
    sync_season_config,
)
from sda.db.snapshots import export_text_snapshot
from sda.fantrax.normalize import normalize_players, normalize_rosters
from sda.fantrax.snapshot import main as fantrax_snapshot_main
from sda.fantrax.snapshots import load_snapshot
from sda.db.ledger import record_roster_snapshot
from sda.reports.build import build_site
from sda.validation.engine import run_all

LOGGER = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
REPOSITORY_ROOT = PROJECT_ROOT.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "season.yaml"
DEFAULT_DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_SITE_DIR = PROJECT_ROOT / "site"
DEFAULT_PAGES_DIR = REPOSITORY_ROOT / "docs"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the SDA nightly pipeline.")
    parser.add_argument("--season", type=int, required=True)
    parser.add_argument("--dry-run", action="store_true", help="Use cached inputs and a temporary DB; write nothing to the real worktree.")
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE_PATH)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--site-dir", type=Path, default=DEFAULT_SITE_DIR)
    parser.add_argument("--pages-dir", type=Path, default=DEFAULT_PAGES_DIR)
    parser.add_argument("--no-git", action="store_true", help="Build and export without git add/commit/push.")
    parser.add_argument("--date", type=date.fromisoformat, default=date.today())
    return parser


def run_pipeline(
    season: int,
    *,
    database_path: Path = DEFAULT_DATABASE_PATH,
    config_path: Path = DEFAULT_CONFIG_PATH,
    data_dir: Path = DEFAULT_DATA_DIR,
    site_dir: Path = DEFAULT_SITE_DIR,
    pages_dir: Path = DEFAULT_PAGES_DIR,
    run_date: date | None = None,
    dry_run: bool = False,
    publish_git: bool = True,
) -> dict[str, Any]:
    """Execute the nightly pipeline, isolating all dry-run writes in a temporary directory."""
    target_date = run_date or date.today()
    data_dir = Path(data_dir)
    database_path = Path(database_path)
    site_dir = Path(site_dir)
    pages_dir = Path(pages_dir)
    config = _load_config(Path(config_path))
    steps: list[str] = []

    if not dry_run and publish_git:
        _require_clean_worktree()

    with tempfile.TemporaryDirectory(prefix="sda-pipeline-") if dry_run else _null_context() as temporary:
        if dry_run:
            temporary_root = Path(temporary)
            effective_database = temporary_root / "sda.duckdb"
            effective_data = temporary_root / "data"
            effective_site = temporary_root / "site"
            effective_pages = temporary_root / "docs"
            effective_data.mkdir(parents=True, exist_ok=True)
            if database_path.exists():
                shutil.copy2(database_path, effective_database)
            else:
                initialize_database(effective_database)
            raw_source = data_dir / "raw"
            raw_target = effective_data / "raw"
            if raw_source.exists():
                shutil.copytree(raw_source, raw_target, dirs_exist_ok=True)
            alias_source = data_dir / "player_aliases.json"
            if alias_source.exists():
                shutil.copy2(alias_source, effective_data / "player_aliases.json")
            LOGGER.info("Would pull Fantrax; dry-run uses saved snapshots only")
        else:
            effective_database = database_path
            effective_data = data_dir
            effective_site = site_dir
            effective_pages = pages_dir
            steps.append("1. Pull Fantrax data")
            pull_result = fantrax_snapshot_main([
                "--all", "--config", str(config_path), "--data-dir", str(data_dir)
            ])
            if pull_result:
                raise RuntimeError("Fantrax snapshot pull failed")

        snapshot_root = effective_data / "raw"
        fingerprint = _snapshot_fingerprint(snapshot_root)
        marker_path = effective_data / "pipeline_runs" / (target_date.isoformat() + ".json")
        if not dry_run and _already_ran(marker_path, fingerprint):
            LOGGER.info("Pipeline already ran for %s with identical inputs; skipping writes", target_date)
            return {"season": season, "steps": ["already_ran"], "summary": {"failed": 0, "actions_taken": []}}

        if dry_run:
            steps.append("1. Pull Fantrax data (cached snapshots; no network)")

        initialize_database(effective_database)
        with open_database(effective_database) as connection:
            sync_season_config(connection, Path(config_path))
            league_info = _optional_snapshot("league_info", snapshot_root)
            if league_info is not None:
                seed_teams(connection, league_info)
            player_ids = _optional_snapshot("player_ids", snapshot_root)
            if player_ids is not None:
                seed_players(
                    connection,
                    player_ids,
                    alias_path=effective_data / "player_aliases.json",
                )

        roster_payload = _optional_snapshot("team_rosters", snapshot_root)
        roster_changes: dict[str, list[dict[str, str]]] = {
            "adds": [], "drops": [], "moves": [], "trades": [],
        }
        if roster_payload is not None:
            normalized_players = normalize_players(_optional_snapshot("player_ids", snapshot_root) or {})
            rosters = normalize_rosters(roster_payload, normalized_players)
            roster_changes = detect_roster_changes(rosters, effective_database, target_date)
            record_roster_snapshot(target_date, rosters, effective_database)
        steps.append("2. Detect roster adds, drops, trades, and MLB/minors moves")

        validation = run_all(season, effective_database, now=datetime.combine(target_date, datetime.max.time()))
        steps.append("3. Validate contracts, cap, and roster rules")
        steps.append("4. Apply one-day defaults for unmatched MLB adds")

        standings = _optional_snapshot("standings", snapshot_root)
        if standings is not None:
            update_current_season(season, standings, effective_database)
        steps.append("5. Update standings history")

        build_site(season, effective_database, effective_site, snapshot_root=snapshot_root,
                   alias_path=effective_data / "player_aliases.json")
        steps.append("6. Rebuild static reports")
        steps.append("6b. Publish Forms JSON feeds")

        effective_pages.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(effective_site, effective_pages, dirs_exist_ok=True)
        export_text_snapshot(
            effective_database,
            snapshot_date=target_date,
            snapshot_root=effective_data / "snapshots",
        )
        steps.append("7. Export DB snapshot and publish Pages site")

        summary = validation["summary"]
        summary["roster_changes"] = roster_changes
        if dry_run:
            LOGGER.info("Would alert commissioner if failures/defaults exist")
            LOGGER.info("Would git add, commit, and push reports and DB snapshot")
            steps.append("8. Alert commissioner if failures/defaults exist (dry-run only; no alert sent)")
        else:
            if publish_git:
                _publish_commit(target_date, int(summary["failed"]), target_date)
            _alert_commissioner(summary)
            _write_run_marker(marker_path, fingerprint, int(summary["failed"]))
            steps.append("8. Alert commissioner if failures/defaults exist")

        return {"season": season, "steps": steps, "summary": summary}


def _load_config(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as config_file:
        config = yaml.safe_load(config_file)
    if not isinstance(config, dict):
        raise ValueError(f"Invalid season config: {path}")
    return config


def _optional_snapshot(name: str, snapshot_root: Path) -> Any | None:
    try:
        return load_snapshot(name, root=snapshot_root)
    except FileNotFoundError:
        LOGGER.warning("Snapshot %s is absent; its dependent step will use empty data", name)
        return None


def detect_roster_changes(
    rosters: dict[str, dict[str, Any]],
    database_path: Path,
    snapshot_date: date,
) -> dict[str, list[dict[str, str]]]:
    """Compare normalized current rosters with the prior snapshot before recording this run."""
    changes: dict[str, list[dict[str, str]]] = {"adds": [], "drops": [], "moves": [], "trades": []}
    with open_database(database_path, read_only=True) as connection:
        previous_date = connection.execute(
            "SELECT max(snapshot_date) FROM roster_snapshots WHERE snapshot_date < ?",
            [snapshot_date],
        ).fetchone()[0]
        if previous_date is None:
            return changes
        previous = {
            (str(team_id), str(player_id)): str(level)
            for team_id, player_id, level in connection.execute(
                """SELECT team_id, fantrax_id, roster_level FROM roster_snapshots
                   WHERE snapshot_date = ?""",
                [previous_date],
            ).fetchall()
        }
    current: dict[tuple[str, str], str] = {}
    for team_id, roster in rosters.items():
        for bucket in ("mlb_roster", "minors_roster"):
            for player in roster.get(bucket, []):
                player_id = player.get("id") if isinstance(player, dict) else player
                level = player.get("roster_level", "MLB" if bucket == "mlb_roster" else "minors") if isinstance(player, dict) else ("MLB" if bucket == "mlb_roster" else "minors")
                if player_id is not None:
                    current[(str(team_id), str(player_id))] = str(level)
    previous_by_player: dict[str, str] = {}
    current_by_player: dict[str, str] = {}
    for (team_id, player_id), level in previous.items():
        previous_by_player[player_id] = team_id
    for (team_id, player_id), level in current.items():
        current_by_player[player_id] = team_id
        old_team = previous_by_player.get(player_id)
        if old_team is None:
            changes["adds"].append({"team_id": team_id, "fantrax_id": player_id, "roster_level": level})
        elif old_team != team_id:
            changes["trades"].append({"from_team_id": old_team, "to_team_id": team_id, "fantrax_id": player_id})
        elif previous.get((team_id, player_id)) != level:
            changes["moves"].append({
                "team_id": team_id,
                "fantrax_id": player_id,
                "from_level": previous[(team_id, player_id)],
                "to_level": level,
            })
    for team_id, player_id in previous:
        if player_id not in current_by_player:
            changes["drops"].append({"team_id": team_id, "fantrax_id": player_id})
    return changes


def _snapshot_fingerprint(snapshot_root: Path) -> str:
    digest = hashlib.sha256()
    files = sorted(Path(snapshot_root).glob("????-??-??/*.json"))
    for path in files:
        digest.update(path.parent.name.encode("utf-8"))
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _already_ran(marker_path: Path, fingerprint: str) -> bool:
    if not marker_path.exists():
        return False
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return marker.get("input_fingerprint") == fingerprint


def _write_run_marker(marker_path: Path, fingerprint: str, failures: int) -> None:
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    marker_path.write_text(
        json.dumps({"input_fingerprint": fingerprint, "validation_failures": failures}, indent=2) + "\n",
        encoding="utf-8",
    )


def _require_clean_worktree() -> None:
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    if result.stdout.strip():
        raise RuntimeError("Refusing to publish: commit or stash existing worktree changes first")
    branch = subprocess.run(
        ["git", "branch", "--show-current"], cwd=REPOSITORY_ROOT,
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    if branch != "main":
        raise RuntimeError(f"Refusing to publish from branch {branch!r}; switch to main")


def _publish_commit(run_date: date, failures: int, snapshot_date: date) -> None:
    snapshot_relative = (Path("second_deck_association_process") / "data" / "snapshots" / snapshot_date.isoformat()).as_posix()
    subprocess.run(
        ["git", "add", "docs", snapshot_relative],
        cwd=REPOSITORY_ROOT,
        check=True,
    )
    changed = subprocess.run(
        ["git", "diff", "--cached", "--quiet"],
        cwd=REPOSITORY_ROOT,
        check=False,
    ).returncode
    if changed == 0:
        LOGGER.info("Published files are unchanged; no commit is needed")
        return
    subprocess.run(
        ["git", "commit", "-m", f"nightly: {run_date.isoformat()} ({failures} validation failures)"],
        cwd=REPOSITORY_ROOT,
        check=True,
    )
    subprocess.run(["git", "push", "origin", "main"], cwd=REPOSITORY_ROOT, check=True)


def _alert_commissioner(summary: Mapping[str, Any]) -> None:
    actions = summary.get("actions_taken", [])
    failures = int(summary.get("failed", 0))
    if not failures and not actions:
        return
    webhook = os.environ.get("SDA_ALERT_WEBHOOK_URL")
    if not webhook:
        LOGGER.error("Validation failures/defaults require alerting, but SDA_ALERT_WEBHOOK_URL is not configured")
        return
    message = f"SDA pipeline: {failures} validation failure(s), {len(actions)} DEFAULTED_1YR action(s)."
    try:
        response = requests.post(webhook, json={"content": message}, timeout=15)
        response.raise_for_status()
    except requests.RequestException:
        LOGGER.exception("Could not deliver SDA pipeline alert")


class _null_context:
    """Minimal context manager for the non-dry-run path."""

    def __enter__(self) -> None:
        return None

    def __exit__(self, *_exc: Any) -> None:
        return None


def main(argv: list[str] | None = None) -> int:
    """Run the SDA pipeline CLI and return its process exit status."""
    args = _build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        report = run_pipeline(
            args.season,
            database_path=args.database,
            config_path=args.config,
            data_dir=args.data_dir,
            site_dir=args.site_dir,
            pages_dir=args.pages_dir,
            run_date=args.date,
            dry_run=args.dry_run,
            publish_git=not args.no_git,
        )
    except (OSError, RuntimeError, ValueError) as error:
        LOGGER.error("Pipeline failed: %s", error)
        return 1
    for index, step in enumerate(report["steps"], start=1):
        LOGGER.info("Step %d: %s", index, step)
    LOGGER.info(
        "Pipeline complete: %d validation failures; %d default action(s)",
        report["summary"].get("failed", 0),
        len(report["summary"].get("actions_taken", [])),
    )
    return 1 if report["summary"].get("failed", 0) else 0


if __name__ == "__main__":
    raise SystemExit(main())