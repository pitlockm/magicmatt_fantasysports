"""Strict parsers for accepted Google Forms relay messages."""

from __future__ import annotations

import math
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone

from sda.fantrax.normalize import normalize_alias

_SIGNING_LINE = re.compile(
    r"^\U0001F4DD SIGNING: (.+?) — (\d+(?:\.\d+)?) years — (.+)$"
)
_SIGNING_META = re.compile(
    r"^type: (waiver|drafted|called_up) · submitted: (\S+) · by: (.+?) · ref: ([0-9a-fA-F-]{36})$"
)
_CAP_TRADE_LINE = re.compile(
    r"^\U0001F4DD CAP TRADE: (.+?) sends (\d+(?:\.\d+)?) years to (.+)$"
)
_CAP_TRADE_META = re.compile(
    r"^submitted: (\S+) · by: (.+?) · ref: ([0-9a-fA-F-]{36})$"
)


@dataclass(frozen=True)
class ParsedSigning:
    """Resolved signing relay with its original submission identity and timestamp."""

    player_id: str
    player_name: str
    years: float
    team_id: str
    team_name: str
    acquisition_type: str
    submitted_at: datetime
    submitted_by: str
    form_ref: str


@dataclass(frozen=True)
class ParsedCapTrade:
    """Resolved cap-trade relay with its original submission identity and timestamp."""

    from_team_id: str
    from_team_name: str
    to_team_id: str
    to_team_name: str
    years: float
    submitted_at: datetime
    submitted_by: str
    form_ref: str


def parse_signing(
    message: str,
    aliases: Mapping[str, str],
    teams: Mapping[str, str],
) -> ParsedSigning | None:
    """Parse only the exact two-line signing relay format."""
    lines = _message_lines(message)
    if lines is None:
        return None
    signing_match = _SIGNING_LINE.fullmatch(lines[0])
    metadata_match = _SIGNING_META.fullmatch(lines[1])
    if not signing_match or not metadata_match:
        return None
    player_label, raw_years, team_label = signing_match.groups()
    acquisition_type, submitted, by_label, raw_ref = metadata_match.groups()
    player = _exact_match(player_label, aliases)
    team = _exact_match(team_label, teams)
    submitter = _exact_match(by_label, teams)
    years = _positive_finite(raw_years)
    submitted_at = _parse_utc(submitted)
    form_ref = _parse_ref(raw_ref)
    if (
        player is None
        or team is None
        or submitter is None
        or submitter[1] != team[1]
        or years is None
        or not years.is_integer()
        or submitted_at is None
        or form_ref is None
    ):
        return None
    return ParsedSigning(
        player[1], player_label, years, team[1], team[0], acquisition_type,
        submitted_at, submitter[0], form_ref,
    )


def parse_cap_trade(message: str, teams: Mapping[str, str]) -> ParsedCapTrade | None:
    """Parse only the exact two-line cap-trade relay format."""
    lines = _message_lines(message)
    if lines is None:
        return None
    trade_match = _CAP_TRADE_LINE.fullmatch(lines[0])
    metadata_match = _CAP_TRADE_META.fullmatch(lines[1])
    if not trade_match or not metadata_match:
        return None
    sender_label, raw_years, receiver_label = trade_match.groups()
    submitted, by_label, raw_ref = metadata_match.groups()
    sender = _exact_match(sender_label, teams)
    receiver = _exact_match(receiver_label, teams)
    submitter = _exact_match(by_label, teams)
    years = _positive_finite(raw_years)
    submitted_at = _parse_utc(submitted)
    form_ref = _parse_ref(raw_ref)
    if (
        sender is None
        or receiver is None
        or submitter is None
        or sender[1] == receiver[1]
        or submitter[1] != sender[1]
        or years is None
        or submitted_at is None
        or form_ref is None
    ):
        return None
    return ParsedCapTrade(
        sender[1], sender[0], receiver[1], receiver[0], years,
        submitted_at, submitter[0], form_ref,
    )


def _message_lines(message: str) -> tuple[str, str] | None:
    lines = message.strip().splitlines()
    if len(lines) != 2 or any(not line.strip() for line in lines):
        return None
    return lines[0].strip(), lines[1].strip()


def _exact_match(label: str, known: Mapping[str, str]) -> tuple[str, str] | None:
    normalized = normalize_alias(label)
    matches = {
        (str(name), str(identifier))
        for name, identifier in known.items()
        if normalize_alias(str(name)) == normalized
    }
    ids = {identifier for _, identifier in matches}
    if len(ids) != 1:
        return None
    identifier = next(iter(ids))
    names = sorted(
        (name for name, value in matches if value == identifier),
        key=lambda name: (-sum(character.isupper() for character in name), name.casefold()),
    )
    return names[0], identifier


def _positive_finite(value: str) -> float | None:
    try:
        years = float(value)
    except (TypeError, ValueError):
        return None
    return years if math.isfinite(years) and years > 0 else None


def _parse_utc(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        return None
    return parsed.astimezone(timezone.utc)


def _parse_ref(value: str) -> str | None:
    try:
        return str(uuid.UUID(value))
    except (ValueError, AttributeError):
        return None
