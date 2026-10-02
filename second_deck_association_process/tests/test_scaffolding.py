"""Smoke tests for the initial SDA project scaffold."""

from pathlib import Path

import yaml


def test_season_config_identifies_sda_2027() -> None:
    """Load the season configuration and confirm its league identity."""
    config_path = Path(__file__).parents[1] / "config" / "season.yaml"
    with config_path.open(encoding="utf-8") as config_file:
        season_config = yaml.safe_load(config_file)

    assert season_config["league_id"] == "4fyzhujxmk7scnaf"
    assert season_config["season"] == 2027