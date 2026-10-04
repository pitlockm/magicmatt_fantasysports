"""Fixture-backed pipeline dry-run and idempotency checks."""

import hashlib
import json
from datetime import date
from pathlib import Path

from sda.db.connection import open_database
from sda.db.schema import initialize_database
from sda.fantrax.snapshots import save_snapshot
from sda.pipeline import __main__ as pipeline
from sda.pipeline.__main__ import run_pipeline

PROJECT_ROOT = Path(__file__).parents[1]
FIXTURES = Path(__file__).parent / "fixtures"


def _fixture_database(path: Path) -> None:
    initialize_database(path)
    with open_database(path) as connection:
        connection.execute((FIXTURES / "reports_fixture.sql").read_text(encoding="utf-8"))


def _save_inputs(data_dir: Path) -> None:
    snapshots = {
        "player_ids": {
            "p-active": {"fantraxId": "p-active", "name": "Mason Hitter", "position": "OF"},
            "p-dead": {"fantraxId": "p-dead", "name": "Elliot Slugger", "position": "1B"},
            "p-minor": {"fantraxId": "p-minor", "name": "Nico Prospect", "position": "SS"},
            "p-beta": {"fantraxId": "p-beta", "name": "Riley Pitcher", "position": "SP"},
        },
        "league_info": {
            "teamInfo": {
                "team-a": {"teamId": "team-a", "teamName": "Alpha Owls"},
                "team-b": {"teamId": "team-b", "teamName": "Copperheads"},
                "team-c": {"teamId": "team-c", "teamName": "Third Deck"},
            }
        },
        "team_rosters": {
            "rosters": {
                "team-a": {
                    "teamName": "Alpha Owls",
                    "rosterItems": [
                        {"id": "p-active", "status": "INJURED_RESERVE", "position": "OF"},
                        {"id": "p-minor", "status": "MINORS", "position": "SS"},
                    ],
                },
                "team-b": {
                    "teamName": "Copperheads",
                    "rosterItems": [{"id": "p-beta", "status": "INJURED_RESERVE", "position": "SP"}],
                },
                "team-c": {"teamName": "Third Deck", "rosterItems": []},
            }
        },
        "standings": json.loads((FIXTURES / "reports_standings.json").read_text(encoding="utf-8")),
        "draft_results": {"draftPicks": []},
    }
    for name, payload in snapshots.items():
        save_snapshot(name, payload, snapshot_date=date(2026, 10, 3), root=data_dir / "raw")
    aliases = {"mason hitter": "p-active", "nico prospect": "p-minor"}
    (data_dir / "player_aliases.json").write_text(json.dumps(aliases), encoding="utf-8")


def test_pipeline_dry_run_runs_all_steps_without_touching_real_db_or_pages(tmp_path: Path) -> None:
    database = tmp_path / "sda.duckdb"
    data_dir = tmp_path / "data"
    site_dir = tmp_path / "site"
    pages_dir = tmp_path / "docs"
    _fixture_database(database)
    _save_inputs(data_dir)
    original_hash = hashlib.sha256(database.read_bytes()).hexdigest()
    with open_database(database, read_only=True) as connection:
        original_roster_count = connection.execute("SELECT count(*) FROM roster_snapshots").fetchone()[0]
        original_history_count = connection.execute("SELECT count(*) FROM team_season_history WHERE season = 2027").fetchone()[0]

    report = run_pipeline(
        2027,
        database_path=database,
        config_path=PROJECT_ROOT / "config" / "season.yaml",
        data_dir=data_dir,
        site_dir=site_dir,
        pages_dir=pages_dir,
        run_date=date(2026, 10, 4),
        dry_run=True,
    )

    assert report["steps"] == [
        "1. Pull Fantrax data (cached snapshots; no network)",
        "2. Detect roster adds, drops, trades, and MLB/minors moves",
        "3. Validate contracts, cap, and roster rules",
        "4. Apply one-day defaults for unmatched MLB adds",
        "5. Update standings history",
        "6. Rebuild static reports",
        "6b. Publish Forms JSON feeds",
        "7. Export DB snapshot and publish Pages site",
        "8. Alert commissioner if failures/defaults exist (dry-run only; no alert sent)",
    ]
    assert not site_dir.exists()
    assert not pages_dir.exists()
    assert hashlib.sha256(database.read_bytes()).hexdigest() == original_hash
    with open_database(database, read_only=True) as connection:
        assert connection.execute("SELECT count(*) FROM roster_snapshots").fetchone()[0] == original_roster_count
        assert connection.execute("SELECT count(*) FROM team_season_history WHERE season = 2027").fetchone()[0] == original_history_count


def test_pipeline_run_marker_skips_identical_input_hash(tmp_path: Path) -> None:
    from sda.pipeline.__main__ import _already_ran, _snapshot_fingerprint, _write_run_marker

    raw = tmp_path / "raw"
    save_snapshot("standings", [{"teamId": "t1"}], snapshot_date=date(2026, 10, 4), root=raw)
    marker = tmp_path / "runs" / "2026-10-04.json"
    fingerprint = _snapshot_fingerprint(raw)
    _write_run_marker(marker, fingerprint, 0)

    assert _already_ran(marker, fingerprint) is True
    assert _already_ran(marker, "different") is False


def test_pipeline_second_same_day_run_is_a_noop(tmp_path: Path, monkeypatch) -> None:
    database = tmp_path / "sda.duckdb"
    data_dir = tmp_path / "data"
    site_dir = tmp_path / "site"
    pages_dir = tmp_path / "docs"
    _fixture_database(database)
    _save_inputs(data_dir)
    monkeypatch.setattr(pipeline, "fantrax_snapshot_main", lambda _args: 0)

    first = run_pipeline(
        2027,
        database_path=database,
        config_path=PROJECT_ROOT / "config" / "season.yaml",
        data_dir=data_dir,
        site_dir=site_dir,
        pages_dir=pages_dir,
        run_date=date(2026, 10, 4),
        publish_git=False,
    )
    second = run_pipeline(
        2027,
        database_path=database,
        config_path=PROJECT_ROOT / "config" / "season.yaml",
        data_dir=data_dir,
        site_dir=site_dir,
        pages_dir=pages_dir,
        run_date=date(2026, 10, 4),
        publish_git=False,
    )

    assert any(step.startswith("8. Alert commissioner") for step in first["steps"])
    assert second["steps"] == ["already_ran"]