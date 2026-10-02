"""Normalize Fantrax API payloads and maintain player aliases."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ALIAS_PATH = PROJECT_ROOT / "data" / "player_aliases.json"

_PLAYER_COLLECTION_KEYS = {
    "players",
    "playerids",
    "playerinfo",
    "playerlist",
    "playerdata",
    "entries",
    "items",
}
_TEAM_COLLECTION_KEYS = {"teams", "teaminfo", "teamrosters", "rosters"}
_ROSTER_KEYS = ("players", "roster", "rosterEntries", "playerList", "teamRoster", "rosterItems")
_PLAYER_ID_KEYS = ("playerId", "playerID", "player_id", "fantraxId", "id")
_TEAM_ID_KEYS = ("teamId", "teamID", "team_id", "id")
_NAME_KEYS = ("name", "playerName", "fullName", "displayName")
_FANTRAX_ROSTER_STATUSES = {
    "ACTIVE": "MLB",
    "RESERVE": "MLB",
    "MINORS": "minors",
    "INJURED_RESERVE": "IL",
}


def normalize_players(payload: Any) -> dict[str, dict[str, Any]]:
    """Return players keyed by Fantrax ID with normalized identity fields."""
    players: dict[str, dict[str, Any]] = {}
    for record, inherited_id in _iter_records(payload, _PLAYER_COLLECTION_KEYS):
        player_id = _first(record, _PLAYER_ID_KEYS) or inherited_id
        player_name = _text(_first(record, _NAME_KEYS))
        if player_id is None or player_name is None:
            continue
        positions = _normalize_positions(_first(record, ("positions", "position", "pos")))
        players[str(player_id)] = {
            "id": str(player_id),
            "name": player_name,
            "positions": positions,
            "mlb_team": _team_name(_first(record, ("mlbTeam", "proTeam", "teamAbbr", "team"))),
        }
    return players


def normalize_rosters(
    payload: Any,
    players: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Return rosters keyed by team ID, separating MLB, minors, and IL slots."""
    normalized: dict[str, dict[str, Any]] = {}
    player_lookup = players or {}
    for team, inherited_id in _iter_records(payload, _TEAM_COLLECTION_KEYS, _has_roster):
        team_id = _first(team, _TEAM_ID_KEYS) or inherited_id
        if team_id is None:
            continue
        team_id = str(team_id)
        team_name = _text(_first(team, ("teamName", "name", "displayName"))) or team_id
        mlb_roster: list[dict[str, Any]] = []
        minors_roster: list[dict[str, Any]] = []
        il_slots_used = 0

        explicit_mlb = _first(team, ("mlbRoster", "activeRoster"))
        explicit_minors = _first(team, ("minorsRoster", "minorLeagueRoster", "minorRoster"))
        roster_entries: list[tuple[dict[str, Any], bool]] = []
        if explicit_mlb is not None:
            roster_entries.extend((record, False) for record in _as_records(explicit_mlb))
        if explicit_minors is not None:
            roster_entries.extend((record, True) for record in _as_records(explicit_minors))
        if not roster_entries:
            roster_value = _first(team, _ROSTER_KEYS)
            roster_entries.extend((record, False) for record in _as_records(roster_value))

        for record, explicitly_minor in roster_entries:
            player_id = _first(record, _PLAYER_ID_KEYS)
            if player_id is None:
                nested_player = record.get("player")
                if isinstance(nested_player, Mapping):
                    player_id = _first(nested_player, _PLAYER_ID_KEYS)
            if player_id is None:
                LOGGER.warning("Skipping roster entry without a Fantrax player ID for team %s", team_id)
                continue

            player_id = str(player_id)
            known_player = player_lookup.get(player_id, {})
            player_name = _text(_first(record, _NAME_KEYS)) or _text(known_player.get("name"))
            status = _text(_first(record, ("status",)))
            status_level = _FANTRAX_ROSTER_STATUSES.get(status.upper()) if status else None
            roster_slot = _text(_first(record, ("rosterSlot", "slot")))
            is_minor = explicitly_minor or status_level == "minors" or _is_minor(record, None, roster_slot)
            if status_level is not None:
                roster_level = "minors" if is_minor else status_level
            else:
                LOGGER.warning(
                    "Unrecognized or missing Fantrax roster status for player %s; "
                    "using explicit-slot fallback",
                    player_id,
                )
                roster_level = "minors" if is_minor else "IL" if _legacy_il_slot(roster_slot) else "MLB"
            if roster_level == "IL":
                il_slots_used += 1

            roster_player = {
                "id": player_id,
                "name": player_name,
                "roster_level": roster_level,
                "positions": _normalize_positions(
                    _first(record, ("positions", "position", "pos"))
                ) or list(known_player.get("positions", [])),
                "mlb_team": _team_name(
                    _first(record, ("mlbTeam", "proTeam", "teamAbbr", "team"))
                ) or known_player.get("mlb_team"),
            }
            (minors_roster if is_minor else mlb_roster).append(roster_player)

        explicit_il_count = _first(team, ("ilSlotsUsed", "injuredListSlotsUsed"))
        if isinstance(explicit_il_count, int):
            il_slots_used = explicit_il_count

        normalized[team_id] = {
            "team_id": team_id,
            "team_name": team_name,
            "mlb_roster": mlb_roster,
            "minors_roster": minors_roster,
            "il_slots_used": il_slots_used,
        }
    return normalized


def load_aliases(path: Path = DEFAULT_ALIAS_PATH) -> dict[str, str]:
    """Load the persisted normalized alias-to-player-ID map."""
    try:
        raw_aliases = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    if not isinstance(raw_aliases, dict):
        raise ValueError(f"Alias file must contain a JSON object: {path}")
    return {normalize_alias(str(alias)): str(player_id) for alias, player_id in raw_aliases.items()}


def save_aliases(aliases: Mapping[str, str], path: Path = DEFAULT_ALIAS_PATH) -> None:
    """Persist an alias map as readable, sorted JSON."""
    normalized = {normalize_alias(alias): str(player_id) for alias, player_id in aliases.items()}
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(".tmp")
    try:
        temporary_path.write_text(
            json.dumps(normalized, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary_path.replace(path)
    except (OSError, TypeError, ValueError):
        temporary_path.unlink(missing_ok=True)
        raise


def seed_aliases(payload: Any, path: Path = DEFAULT_ALIAS_PATH) -> dict[str, str]:
    """Add each known Fantrax player name to the persistent alias map."""
    aliases = load_aliases(path)
    existing_aliases = set(aliases)
    ambiguous_aliases: set[str] = set()
    persisted_conflicts: list[str] = []
    for player_id, player in normalize_players(payload).items():
        alias = normalize_alias(str(player["name"]))
        if not alias or alias in ambiguous_aliases:
            continue
        existing_id = aliases.get(alias)
        if existing_id is not None and existing_id != player_id:
            if alias in existing_aliases:
                persisted_conflicts.append(alias)
                continue
            aliases.pop(alias)
            ambiguous_aliases.add(alias)
            continue
        aliases[alias] = player_id
    save_aliases(aliases, path)
    if ambiguous_aliases:
        examples = ", ".join(sorted(ambiguous_aliases)[:5])
        LOGGER.warning(
            "Omitted %d ambiguous player-name aliases; examples: %s",
            len(ambiguous_aliases),
            examples,
        )
    if persisted_conflicts:
        LOGGER.warning(
            "Retained %d persisted aliases that conflict with player names; examples: %s",
            len(persisted_conflicts),
            ", ".join(sorted(set(persisted_conflicts))[:5]),
        )
    return aliases


def add_alias(
    alias: str,
    player_id: str,
    *,
    path: Path = DEFAULT_ALIAS_PATH,
    aliases: dict[str, str] | None = None,
) -> dict[str, str]:
    """Append an alias, rejecting a normalized alias already owned by another ID."""
    normalized_alias = normalize_alias(alias)
    if not normalized_alias:
        raise ValueError("alias must contain at least one letter or number")
    target = aliases if aliases is not None else load_aliases(path)
    existing_id = target.get(normalized_alias)
    if existing_id is not None and existing_id != str(player_id):
        raise ValueError(f"Alias {alias!r} already maps to player ID {existing_id}")
    target[normalized_alias] = str(player_id)
    if aliases is None:
        save_aliases(target, path)
    return target


def resolve_player_id(alias: str, path: Path = DEFAULT_ALIAS_PATH) -> str | None:
    """Resolve a name or nickname to its Fantrax player ID, if known."""
    return load_aliases(path).get(normalize_alias(alias))


def normalize_alias(alias: str) -> str:
    """Normalize case, punctuation, and whitespace for stable alias matching."""
    return " ".join(re.findall(r"[\w]+", alias.casefold(), flags=re.UNICODE))


def _iter_records(
    value: Any,
    collection_keys: set[str],
    predicate: Callable[[Mapping[str, Any]], bool] | None = None,
    inherited_id: str | None = None,
) -> Iterator[tuple[dict[str, Any], str | None]]:
    if isinstance(value, list):
        for item in value:
            yield from _iter_records(item, collection_keys, predicate)
        return
    if not isinstance(value, Mapping):
        return

    record = dict(value)
    matches_record = predicate(record) if predicate is not None else (
        _looks_like_player(record)
        or (inherited_id is not None and any(key in record for key in _NAME_KEYS))
    )
    if matches_record:
        yield record, inherited_id
        return

    for key, child in value.items():
        if str(key).casefold() in collection_keys:
            yield from _iter_records(child, collection_keys, predicate)
        elif isinstance(child, (Mapping, list)):
            child_id = str(key) if isinstance(child, Mapping) else None
            yield from _iter_records(child, collection_keys, predicate, child_id)


def _as_records(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [dict(item) for item in value if isinstance(item, Mapping)]
    if isinstance(value, Mapping):
        if _looks_like_player(value):
            return [dict(value)]
        return [
            {"playerId": str(key), **dict(item)}
            for key, item in value.items()
            if isinstance(item, Mapping)
        ]
    return []


def _has_roster(record: Mapping[str, Any]) -> bool:
    return any(key in record for key in _ROSTER_KEYS) or any(
        key in record for key in ("mlbRoster", "activeRoster", "minorsRoster", "minorLeagueRoster")
    )


def _looks_like_player(record: Mapping[str, Any]) -> bool:
    return any(key in record for key in _PLAYER_ID_KEYS) and any(
        key in record for key in _NAME_KEYS
    )


def _first(record: Mapping[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = record.get(key)
        if value is not None:
            return value
    return None


def _text(value: Any) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, (int, float)):
        return str(value)
    return None


def _normalize_positions(value: Any) -> list[str]:
    if isinstance(value, str):
        return [part.strip() for part in re.split(r"[,/]", value) if part.strip()]
    if isinstance(value, list):
        positions: list[str] = []
        for item in value:
            if isinstance(item, str) and item.strip():
                positions.append(item.strip())
            elif isinstance(item, Mapping):
                position = _text(_first(item, ("abbreviation", "name", "position", "code")))
                if position:
                    positions.append(position)
        return positions
    return []


def _team_name(value: Any) -> str | None:
    if isinstance(value, Mapping):
        return _text(_first(value, ("abbreviation", "abbr", "name", "code", "teamName")))
    return _text(value)


def _is_minor(record: Mapping[str, Any], status: str | None, roster_slot: str | None) -> bool:
    explicit = _first(record, ("isMinorLeague", "isMinor", "minorLeague"))
    if explicit is True or str(explicit).casefold() in {"true", "1", "yes"}:
        return True
    labels = " ".join(value or "" for value in (status, roster_slot)).casefold()
    return any(marker in labels for marker in ("minor", "farm", "prospect"))


def _legacy_il_slot(roster_slot: str | None) -> bool:
    """Read explicit legacy slot labels; status and position never imply IL.

    Current Fantrax status enums identify fantasy slots. Constitution section
    2.4 permits IL classification only for an MLB IL slot, not real-life status.
    """
    if not roster_slot:
        return False
    compact = re.sub(r"[^a-z0-9]", "", roster_slot.casefold())
    return "injuredreserve" in compact or "injuredlist" in compact or bool(
        re.fullmatch(r"il\d*", compact)
    )