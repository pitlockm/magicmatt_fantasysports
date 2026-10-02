"""Pure, deterministic SDA roster and contract validation rules."""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta
from typing import Any

RuleResult = dict[str, Any]

_CONTRACT_START_EVENTS = {"SIGNED", "EXTENDED", "CALLED_UP", "DEFAULTED_1YR"}
_MLB_LEVELS = {"MLB", "IL"}


def _result(
    rule_id: str,
    passed: bool,
    detail: str,
    *,
    team_id: str | None = None,
    fantrax_id: str | None = None,
    review_required: bool = False,
) -> RuleResult:
    return {
        "rule_id": rule_id,
        "team_id": team_id,
        "fantrax_id": fantrax_id,
        "passed": passed,
        "detail": detail,
        "review_required": review_required,
    }


def v1_cap(team_id: str, committed: float, il_players: int, cap_years: int = 78) -> RuleResult:
    """Check committed active/dead cap and cap trades against the IL-adjusted cap."""
    limit = cap_years + il_players
    passed = committed <= limit
    return _result(
        "V1",
        passed,
        f"Committed {committed:g} years against {limit} available ({cap_years} base + {il_players} IL).",
        team_id=team_id,
    )


def v2_contract_bounds(event: Mapping[str, Any], acquisition_type: str | None = None) -> RuleResult:
    """Check 1-7 year draft/call-up and 1-3 year in-season waiver contracts."""
    event_type = str(event.get("event_type", "")).upper()
    note = str(event.get("note") or "").casefold()
    acquisition = (acquisition_type or "").casefold()
    if not acquisition:
        if event_type == "CALLED_UP":
            acquisition = "called_up"
        elif event_type == "DEFAULTED_1YR":
            acquisition = "waiver"
        elif "waiver" in note or "free agent" in note or "free-agent" in note:
            acquisition = "waiver"
        elif "draft" in note:
            acquisition = "draft"
        else:
            acquisition = "draft"
    maximum = 3 if acquisition in {"waiver", "free agent", "free-agent", "fa"} else 7
    years = _finite_number(event.get("years"))
    passed = years is not None and 1 <= years <= maximum
    label = "waiver/FA" if maximum == 3 else "draft/call-up"
    value = "invalid" if years is None else f"{years:g}"
    return _result(
        "V2",
        passed,
        f"{label} contract is {value} years; allowed range is 1-{maximum}.",
        team_id=_optional_text(event.get("team_id")),
        fantrax_id=_optional_text(event.get("fantrax_id")),
    )


def v3_resign_tripwire(
    events: Sequence[Mapping[str, Any]],
    season: int,
    draft_start: date | None,
    draft_end: date | None,
) -> list[RuleResult]:
    """Flag same-team re-signings over three years outside the annual draft."""
    ordered = sorted(events, key=_event_sort_key)
    dropped: set[tuple[str, str]] = set()
    findings: list[RuleResult] = []
    for event in ordered:
        key = (_optional_text(event.get("team_id")) or "", _optional_text(event.get("fantrax_id")) or "")
        event_type = str(event.get("event_type", "")).upper()
        if event_type == "DROPPED":
            dropped.add(key)
            continue
        if event_type not in _CONTRACT_START_EVENTS or key not in dropped:
            continue
        event_date = _event_date(event.get("ts"))
        in_draft = (
            draft_start is not None
            and draft_end is not None
            and draft_start <= event_date <= draft_end
        )
        years = _finite_number(event.get("years"))
        if event_date.year == season and not in_draft and years is not None and years > 3:
            findings.append(
                _result(
                    "V3",
                    False,
                    f"Same-team re-signing is {years:g} years in-season; maximum is 3.",
                    team_id=key[0],
                    fantrax_id=key[1],
                )
            )
        dropped.discard(key)
    return findings


def v4_duplicates(rosters: Sequence[Mapping[str, Any]]) -> list[RuleResult]:
    """Flag player IDs present on more than one team's MLB roster."""
    teams_by_player: dict[str, set[str]] = defaultdict(set)
    for row in rosters:
        if str(row.get("roster_level", "")).casefold() in {"mlb", "il"}:
            player_id = _optional_text(row.get("fantrax_id"))
            team_id = _optional_text(row.get("team_id"))
            if player_id and team_id:
                teams_by_player[player_id].add(team_id)
    findings: list[RuleResult] = []
    for player_id, team_ids in sorted(teams_by_player.items()):
        if len(team_ids) > 1:
            team_list = ", ".join(sorted(team_ids))
            findings.append(
                _result(
                    "V4",
                    False,
                    f"Player appears on multiple MLB rosters: {team_list}.",
                    team_id=sorted(team_ids)[0],
                    fantrax_id=player_id,
                )
            )
    return findings


def v5_coverage(team_id: str, fantrax_id: str, active_contract_ids: set[tuple[str, str]]) -> RuleResult:
    """Check that an MLB-rostered player has a current contract event."""
    passed = (team_id, fantrax_id) in active_contract_ids
    return _result(
        "V5",
        passed,
        "Active contract found." if passed else "MLB-rostered player has no active contract event.",
        team_id=team_id,
        fantrax_id=fantrax_id,
    )


def v6_fa_year_stability(events: Sequence[Mapping[str, Any]]) -> list[RuleResult]:
    """Require a signed/extended event to explain every FA-year change."""
    grouped: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for event in events:
        team_id = _optional_text(event.get("team_id"))
        fantrax_id = _optional_text(event.get("fantrax_id"))
        if team_id and fantrax_id:
            grouped[(team_id, fantrax_id)].append(event)
    findings: list[RuleResult] = []
    for (team_id, fantrax_id), player_events in sorted(grouped.items()):
        ordered = sorted(player_events, key=_event_sort_key)
        previous_fa_year: int | None = None
        for event in ordered:
            fa_year = _integer(event.get("fa_year"))
            if (
                previous_fa_year is not None
                and fa_year is not None
                and fa_year != previous_fa_year
                and str(event.get("event_type", "")).upper() not in _CONTRACT_START_EVENTS
            ):
                findings.append(
                    _result(
                        "V6",
                        False,
                        f"FA year changed from {previous_fa_year} to {fa_year} without a signing or extension event.",
                        team_id=team_id,
                        fantrax_id=fantrax_id,
                    )
                )
            if fa_year is not None:
                previous_fa_year = fa_year
    return findings


def expected_dead_cap(penalty_years: float, drop_year: int, fa_year: int, season: int) -> float:
    """Return scheduled dead cap, including the constitution's final-year zero."""
    if season < drop_year or season >= fa_year - 1:
        return 0.0
    return max(penalty_years - 0.5 * (season - drop_year), 0.0)


def v7_drop_penalties(
    events: Sequence[Mapping[str, Any]],
    *,
    unreported_exits: Sequence[Mapping[str, Any]] = (),
) -> list[RuleResult]:
    """Validate assessed drop penalties and flag mid-contract roster exits."""
    findings: list[RuleResult] = []
    ordered = sorted(events, key=_event_sort_key)
    prior_contracts: dict[tuple[str, str], Mapping[str, Any]] = {}
    for event in ordered:
        event_type = str(event.get("event_type", "")).upper()
        team_id = _optional_text(event.get("team_id"))
        fantrax_id = _optional_text(event.get("fantrax_id"))
        if not team_id or not fantrax_id:
            continue
        key = (team_id, fantrax_id)
        if event_type == "DROPPED":
            contract = prior_contracts.get(key)
            year = _event_date(event.get("ts")).year
            fa_year = _integer(event.get("fa_year"))
            penalty = _finite_number(event.get("years"))
            if contract is None or fa_year is None or penalty is None:
                findings.append(
                    _result(
                        "V7",
                        False,
                        "Drop event has no preceding contract or valid penalty schedule fields.",
                        team_id=team_id,
                        fantrax_id=fantrax_id,
                    )
                )
                continue
            remaining = fa_year - year
            expected = 0.0 if remaining <= 1 else remaining / 2
            if not math.isclose(penalty, expected, abs_tol=1e-9):
                findings.append(
                    _result(
                        "V7",
                        False,
                        f"Drop penalty is {penalty:g}; expected {expected:g} from {remaining} years remaining.",
                        team_id=team_id,
                        fantrax_id=fantrax_id,
                    )
                )
            prior_contracts.pop(key, None)
        elif event_type in _CONTRACT_START_EVENTS:
            prior_contracts[key] = event

    for exit_row in unreported_exits:
        findings.append(
            _result(
                "V7",
                False,
                "Player left the roster mid-contract without a DROPPED event.",
                team_id=_optional_text(exit_row.get("team_id")),
                fantrax_id=_optional_text(exit_row.get("fantrax_id")),
            )
        )
    return findings


def v8_minors_shuttle(
    team_id: str,
    fantrax_id: str,
    *,
    has_active_contract: bool,
    included_in_cap: bool,
    shuttle_dates: Sequence[date] = (),
) -> RuleResult:
    """Ensure demoted contracts remain in cap and flag frequent MLB/minors moves."""
    passed = has_active_contract and included_in_cap
    detail = (
        "Contracted minor leaguer is included in cap."
        if passed
        else "Contracted minor-league player is missing from active cap commitment."
    )
    ordered_dates = sorted(shuttle_dates)
    review = any(
        (ordered_dates[index + 1] - ordered_dates[index - 1]).days <= 30
        for index in range(1, len(ordered_dates) - 1)
    )
    if review:
        detail += " Two or more MLB/minors moves occurred within 30 days; commissioner review required."
    return _result(
        "V8",
        passed,
        detail,
        team_id=team_id,
        fantrax_id=fantrax_id,
        review_required=review,
    )


def v9_pool_freeze(
    team_id: str,
    fantrax_id: str,
    real_draft_year: int | None,
    added_at: date | datetime,
    season: int,
    freeze_date: date | None,
) -> RuleResult:
    """Disallow adding a player drafted in the current year after pool freeze."""
    added_date = added_at.date() if isinstance(added_at, datetime) else added_at
    passed = not (
        real_draft_year == season and freeze_date is not None and added_date > freeze_date
    )
    return _result(
        "V9",
        passed,
        "Pool freeze respected."
        if passed
        else f"Current-season MLB draftee was added on {added_date.isoformat()}, after {freeze_date}. ",
        team_id=team_id,
        fantrax_id=fantrax_id,
    )


def v10_roster_max(
    team_id: str,
    mlb_count: int,
    minors_count: int,
    il_count: int,
) -> RuleResult:
    """Enforce 26 MLB, 15 minors, and 8 Fantrax IL-slot maximums."""
    violations = []
    if mlb_count > 26:
        violations.append(f"{mlb_count} MLB players (max 26)")
    if minors_count > 15:
        violations.append(f"{minors_count} minors players (max 15)")
    if il_count > 8:
        violations.append(f"{il_count} IL slots used (max 8)")
    return _result(
        "V10",
        not violations,
        "Roster maximums respected." if not violations else "; ".join(violations) + ".",
        team_id=team_id,
    )


def v11_roster_min(team_id: str, mlb_count: int, minimum: int) -> RuleResult:
    """Enforce the configured minimum MLB roster size."""
    passed = mlb_count >= minimum
    return _result(
        "V11",
        passed,
        f"MLB roster has {mlb_count} players; configured minimum is {minimum}.",
        team_id=team_id,
    )


def v12_one_day_signing(
    team_id: str,
    fantrax_id: str,
    added_at: datetime,
    announcements: Sequence[Mapping[str, Any]],
) -> RuleResult:
    """Match a roster add to an announcement within one day of its timestamp."""
    matches = []
    for announcement in announcements:
        if (
            str(announcement.get("team_id")) != team_id
            or str(announcement.get("fantrax_id")) != fantrax_id
        ):
            continue
        announced_at = _event_datetime(announcement.get("announced_at"))
        if announced_at is not None and abs(announced_at - added_at) <= timedelta(days=1):
            matches.append(announced_at)
    passed = bool(matches)
    return _result(
        "V12",
        passed,
        "Matching announcement found within one day."
        if passed
        else "No matching announcement within one day; DEFAULTED_1YR action required.",
        team_id=team_id,
        fantrax_id=fantrax_id,
    )


def v13_il_eligibility(
    team_id: str,
    fantrax_id: str,
    roster_level: str,
    real_life_il: bool | None,
    *,
    is_minor: bool = False,
    claims_il_relief: bool = False,
) -> RuleResult:
    """Check slot-based IL placement without treating Fantrax status as injury status."""
    level = roster_level.upper()
    if is_minor and level == "IL":
        return _result(
            "V13",
            False,
            "Minor-league players are not eligible for an IL roster slot.",
            team_id=team_id,
            fantrax_id=fantrax_id,
        )
    if level == "IL" and real_life_il is False:
        return _result(
            "V13",
            False,
            "Player occupies a Fantrax IL slot but is not on the real-life MLB IL.",
            team_id=team_id,
            fantrax_id=fantrax_id,
        )
    if level == "IL" and real_life_il is None:
        return _result(
            "V13",
            True,
            "Real-life IL eligibility is unknown; verify when injury-status data is available.",
            team_id=team_id,
            fantrax_id=fantrax_id,
            review_required=True,
        )
    if level in _MLB_LEVELS and real_life_il is True and claims_il_relief:
        return _result(
            "V13",
            True,
            "Real-life-IL player is in an active slot while IL relief is claimed; committee review required.",
            team_id=team_id,
            fantrax_id=fantrax_id,
            review_required=True,
        )
    return _result(
        "V13",
        True,
        "IL placement and eligibility are consistent.",
        team_id=team_id,
        fantrax_id=fantrax_id,
    )


def _event_sort_key(event: Mapping[str, Any]) -> tuple[datetime, int]:
    return (_event_datetime(event.get("ts")) or datetime.min, _integer(event.get("event_id")) or 0)


def _event_date(value: Any) -> date:
    parsed = _event_datetime(value)
    return parsed.date() if parsed else date.min


def _event_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            return None
    return None


def _finite_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _integer(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _optional_text(value: Any) -> str | None:
    return None if value is None else str(value)