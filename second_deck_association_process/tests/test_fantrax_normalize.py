"""Tests for Fantrax payload normalization and player aliases."""

import json
from pathlib import Path
from typing import Any

from sda.fantrax.normalize import (
    add_alias,
    normalize_players,
    normalize_rosters,
    resolve_player_id,
    seed_aliases,
)


def _fixture_payload() -> dict[str, Any]:
    fixture_path = Path(__file__).parent / "fixtures" / "fantrax_sample.json"
    with fixture_path.open(encoding="utf-8") as fixture_file:
        return json.load(fixture_file)


def test_normalizes_player_and_team_roster_payload() -> None:
    """Normalize identities and separate minor-league and injured players."""
    payload = _fixture_payload()
    players = normalize_players(payload["playerIds"])
    rosters = normalize_rosters(payload["teamRosters"], players)

    assert players["player-1"] == {
        "id": "player-1",
        "name": "Shohei Ohtani",
        "positions": ["DH", "P"],
        "mlb_team": "LAD",
    }
    assert rosters["team-1"]["team_name"] == "SDA Sluggers"
    assert [player["id"] for player in rosters["team-1"]["mlb_roster"]] == ["player-1"]
    assert [player["id"] for player in rosters["team-1"]["minors_roster"]] == ["player-2"]
    assert rosters["team-1"]["il_slots_used"] == 1


def test_alias_map_round_trips_and_normalizes_names(tmp_path: Path) -> None:
    """Persist a nickname alias and resolve it independent of case/punctuation."""
    alias_path = tmp_path / "player_aliases.json"
    add_alias("Sho-Time", "player-1", path=alias_path)

    assert json.loads(alias_path.read_text(encoding="utf-8")) == {"sho time": "player-1"}
    assert resolve_player_id("SHO TIME", path=alias_path) == "player-1"


def test_alias_seed_omits_ambiguous_player_names(tmp_path: Path) -> None:
    """Do not choose arbitrarily when Fantrax reuses a display name."""
    payload = [
        {"playerId": "player-1", "name": "Alex Smith"},
        {"playerId": "player-2", "name": "Alex Smith"},
        {"playerId": "player-3", "name": "Unique Player"},
    ]

    aliases = seed_aliases(payload, path=tmp_path / "aliases.json")

    assert aliases == {"unique player": "player-3"}