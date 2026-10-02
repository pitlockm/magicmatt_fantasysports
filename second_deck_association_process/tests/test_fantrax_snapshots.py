"""Tests for dated Fantrax snapshots."""

from datetime import date
from pathlib import Path
from typing import Any

from sda.fantrax.snapshots import load_snapshot, save_snapshot


def test_save_overwrites_same_day_and_loads_latest(tmp_path: Path) -> None:
    """Same-day saves are idempotent, and offline loads find the latest date."""
    save_snapshot("standings", {"run": 1}, snapshot_date=date(2027, 3, 1), root=tmp_path)
    save_snapshot("standings", {"run": 2}, snapshot_date=date(2027, 3, 1), root=tmp_path)
    save_snapshot("standings", {"run": 3}, snapshot_date=date(2027, 3, 2), root=tmp_path)

    assert load_snapshot("standings", snapshot_date=date(2027, 3, 1), root=tmp_path) == {"run": 2}
    assert load_snapshot("standings", root=tmp_path) == {"run": 3}


def test_client_uses_cache_until_ttl_expires(tmp_path: Path) -> None:
    """A response is reused while fresh and fetched again after its TTL."""
    from unittest.mock import Mock

    from sda.fantrax.client import FantraxClient

    first_response = Mock(status_code=200)
    first_response.json.return_value = {"run": 1}
    second_response = Mock(status_code=200)
    second_response.json.return_value = {"run": 2}
    session = Mock()
    session.get.side_effect = [first_response, second_response]
    now = [1000.0]
    client = FantraxClient(
        "league-id",
        session=session,
        cache_dir=tmp_path / "cache",
        cache_ttl_seconds=10,
        request_interval_seconds=0,
        clock=lambda: now[0],
        wall_clock=lambda: now[0],
    )

    assert client.get_standings() == {"run": 1}
    assert client.get_standings() == {"run": 1}
    now[0] += 11
    assert client.get_standings() == {"run": 2}
    assert session.get.call_count == 2