"""Strict parser tests for Google Forms relay messages."""

from datetime import datetime, timezone

import pytest

from sda.discord_bot.parser import parse_cap_trade, parse_signing

ALIASES = {
    "mookie betts": "player-1",
    "shohei ohtani": "player-2",
    "ronald acuna jr": "player-3",
}
TEAMS = {
    "Alpha Owls": "team-a",
    "Copperheads": "team-b",
    "Third Deck": "team-c",
}
REF = "123e4567-e89b-12d3-a456-426614174000"
SUBMITTED = "2027-03-15T15:30:00Z"


@pytest.mark.parametrize("acquisition", ["drafted", "called_up", "waiver"])
def test_strict_signing_relay_carries_type_timestamp_and_ref(acquisition: str) -> None:
    relay = (
        "📝 SIGNING: Mookie Betts — 3 years — Alpha Owls\n"
        f"type: {acquisition} · submitted: {SUBMITTED} · by: Alpha Owls · ref: {REF}"
    )

    parsed = parse_signing(relay, ALIASES, TEAMS)

    assert parsed is not None
    assert (parsed.player_id, parsed.player_name, parsed.years, parsed.team_id) == (
        "player-1", "Mookie Betts", 3, "team-a"
    )
    assert parsed.acquisition_type == acquisition
    assert parsed.submitted_at == datetime(2027, 3, 15, 15, 30, tzinfo=timezone.utc)
    assert parsed.submitted_by == "Alpha Owls"
    assert parsed.form_ref == REF


def test_strict_fractional_cap_trade_relay_carries_metadata() -> None:
    relay = (
        "📝 CAP TRADE: Alpha Owls sends 2.5 years to Copperheads\n"
        f"submitted: {SUBMITTED} · by: Alpha Owls · ref: {REF}"
    )

    parsed = parse_cap_trade(relay, TEAMS)

    assert parsed is not None
    assert (parsed.from_team_id, parsed.to_team_id, parsed.years) == ("team-a", "team-b", 2.5)
    assert parsed.submitted_at == datetime(2027, 3, 15, 15, 30, tzinfo=timezone.utc)
    assert parsed.submitted_by == "Alpha Owls"
    assert parsed.form_ref == REF


@pytest.mark.parametrize(
    "relay",
    [
        "Mookie Betts signs a 3-year deal with Alpha Owls",
        "📝 SIGNING: Mookie Betts — 3 years — Alpha Owls",
        "📝 SIGNING: Unknown Player — 3 years — Alpha Owls\ntype: drafted · submitted: 2027-03-15T15:30:00Z · by: Alpha Owls · ref: " + REF,
        "📝 SIGNING: Mookie Betts — 3 years — Alpha Owls\ntype: unknown · submitted: 2027-03-15T15:30:00Z · by: Alpha Owls · ref: " + REF,
        "📝 SIGNING: Mookie Betts — 3 years — Alpha Owls\ntype: drafted · submitted: 2027-03-15 15:30 · by: Alpha Owls · ref: " + REF,
        "📝 SIGNING: Mookie Betts — 3 years — Alpha Owls\ntype: drafted · submitted: 2027-03-15T15:30:00-05:00 · by: Alpha Owls · ref: " + REF,
        "📝 SIGNING: Mookie Betts — 3 years — Alpha Owls\ntype: drafted · submitted: 2027-03-15T15:30:00Z · by: Copperheads · ref: " + REF,
        "📝 SIGNING: Mookie Betts — 3 years — Alpha Owls\ntype: drafted · submitted: 2027-03-15T15:30:00Z · by: Alpha Owls · ref: not-a-uuid",
    ],
)
def test_malformed_or_hand_typed_signing_is_rejected(relay: str) -> None:
    assert parse_signing(relay, ALIASES, TEAMS) is None


@pytest.mark.parametrize(
    "relay",
    [
        "📝 CAP TRADE: Alpha Owls sends 2.5 years to Copperheads",
        "Alpha Owls sends 2.5 years to Copperheads",
        "📝 CAP TRADE: Unknown Team sends 2.5 years to Copperheads\nsubmitted: " + SUBMITTED + " · by: Unknown Team · ref: " + REF,
        "📝 CAP TRADE: Alpha Owls sends 2.5 years to Alpha Owls\nsubmitted: " + SUBMITTED + " · by: Alpha Owls · ref: " + REF,
        "📝 CAP TRADE: Alpha Owls sends 0 years to Copperheads\nsubmitted: " + SUBMITTED + " · by: Alpha Owls · ref: " + REF,
        "📝 CAP TRADE: Alpha Owls sends 2.5 years to Copperheads\nsubmitted: " + SUBMITTED + " · by: Copperheads · ref: " + REF,
    ],
)
def test_malformed_or_hand_typed_cap_trade_is_rejected(relay: str) -> None:
    assert parse_cap_trade(relay, TEAMS) is None
