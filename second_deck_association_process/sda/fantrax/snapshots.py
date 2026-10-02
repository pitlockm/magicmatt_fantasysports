"""Read and write dated raw Fantrax API snapshots."""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SNAPSHOT_ROOT = PROJECT_ROOT / "data" / "raw"
_SNAPSHOT_NAME = re.compile(r"^[A-Za-z0-9_-]+$")


def save_snapshot(
    name: str,
    payload: Any,
    *,
    snapshot_date: date | None = None,
    root: Path | None = None,
) -> Path:
    """Save a JSON payload under the requested date and return its path."""
    _validate_name(name)
    target_date = snapshot_date or date.today()
    snapshot_path = (root or DEFAULT_SNAPSHOT_ROOT) / target_date.isoformat() / f"{name}.json"
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = snapshot_path.with_suffix(".tmp")
    try:
        temporary_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary_path.replace(snapshot_path)
    except (OSError, TypeError, ValueError):
        temporary_path.unlink(missing_ok=True)
        raise
    return snapshot_path


def load_snapshot(
    name: str,
    *,
    snapshot_date: date | None = None,
    root: Path | None = None,
) -> Any:
    """Load a dated snapshot, or the latest available snapshot, offline."""
    _validate_name(name)
    snapshot_root = root or DEFAULT_SNAPSHOT_ROOT
    if snapshot_date is not None:
        snapshot_path = snapshot_root / snapshot_date.isoformat() / f"{name}.json"
        if not snapshot_path.is_file():
            raise FileNotFoundError(f"No snapshot found for {name} on {snapshot_date.isoformat()}")
    else:
        matches = sorted(
            snapshot_root.glob(f"????-??-??/{name}.json"),
            key=lambda path: path.parent.name,
            reverse=True,
        )
        if not matches:
            raise FileNotFoundError(f"No snapshots found for {name} under {snapshot_root}")
        snapshot_path = matches[0]

    with snapshot_path.open(encoding="utf-8") as snapshot_file:
        return json.load(snapshot_file)


def _validate_name(name: str) -> None:
    if not _SNAPSHOT_NAME.fullmatch(name):
        raise ValueError("Snapshot names may contain only letters, numbers, underscores, and hyphens")