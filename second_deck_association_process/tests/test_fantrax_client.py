"""Tests for Fantrax HTTP behavior."""

from unittest.mock import Mock
from pathlib import Path

from sda.fantrax.client import FantraxClient


def test_retries_on_server_error(tmp_path: Path) -> None:
    """Retry a transient 500 response and return the next JSON response."""
    first_response = Mock(status_code=500)
    second_response = Mock(status_code=200)
    second_response.json.return_value = {"ok": True}
    session = Mock()
    session.get.side_effect = [first_response, second_response]
    delays: list[float] = []
    client = FantraxClient(
        "league-id",
        session=session,
        cache_dir=tmp_path / "cache",
        request_interval_seconds=0,
        sleeper=delays.append,
    )

    result = client.get_standings()

    assert result == {"ok": True}
    assert session.get.call_count == 2
    assert delays == [0.5]