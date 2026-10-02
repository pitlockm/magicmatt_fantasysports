"""Polite MLB Stats API client and minor-leaguer biography refresh."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
import unicodedata
from collections.abc import Callable, Mapping
from datetime import date, datetime
from pathlib import Path
from typing import Any

import requests

from sda.db.connection import DEFAULT_DATABASE_PATH, open_database
from sda.db.snapshots import export_text_snapshot

LOGGER = logging.getLogger(__name__)
BASE_URL = "https://statsapi.mlb.com/api/v1/"
PROJECT_ROOT = Path(__file__).resolve().parents[2]


class MLBStatsAPIError(RuntimeError):
    """Raised when the MLB Stats API request fails or returns invalid JSON."""


class MLBStatsClient:
    """Query MLB player identity, biography, and career hitting/pitching stats."""

    def __init__(
        self,
        *,
        session: requests.Session | None = None,
        base_url: str = BASE_URL,
        timeout: float = 20.0,
        max_retries: int = 3,
        backoff_factor: float = 0.5,
        cache_dir: Path | None = None,
        cache_ttl_seconds: float = 86_400.0,
        request_interval_seconds: float = 1.0,
        sleeper: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        """Create a cached, rate-limited MLB Stats API client."""
        if timeout <= 0:
            raise ValueError("timeout must be greater than zero")
        if max_retries < 0:
            raise ValueError("max_retries must be zero or greater")
        self.session = session or requests.Session()
        self.base_url = base_url.rstrip("/") + "/"
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff_factor = max(0.0, backoff_factor)
        self.cache_dir = cache_dir or PROJECT_ROOT / "data" / "cache" / "mlb"
        self.cache_ttl_seconds = max(0.0, cache_ttl_seconds)
        self.request_interval_seconds = max(0.0, request_interval_seconds)
        self._sleep = sleeper
        self._clock = clock
        self._wall_clock = wall_clock
        self._last_request_at: float | None = None

    def find_mlbam_id(self, name: str) -> int | None:
        """Resolve a player only if exactly one API result matches the full name."""
        normalized_name = normalize_person_name(name)
        if not normalized_name:
            return None
        payload = self._get_json("people/search", {"names": name})
        people = payload.get("people", []) if isinstance(payload, Mapping) else []
        exact_ids = {
            int(person["id"])
            for person in people
            if isinstance(person, Mapping)
            and person.get("id") is not None
            and any(
                normalize_person_name(str(person.get(key, ""))) == normalized_name
                for key in ("fullName", "nameFirstLast")
            )
        }
        if len(exact_ids) != 1:
            if len(exact_ids) > 1:
                LOGGER.warning("Ambiguous MLB Stats API exact-name match for %r", name)
            return None
        return next(iter(exact_ids))

    def get_bio(self, mlbam_id: int) -> date | None:
        """Return a player's birthdate, or None when the API has no valid date."""
        payload = self._get_json(f"people/{int(mlbam_id)}", {})
        people = payload.get("people", []) if isinstance(payload, Mapping) else []
        if not people or not isinstance(people[0], Mapping):
            return None
        birthdate = people[0].get("birthDate")
        try:
            return date.fromisoformat(str(birthdate)) if birthdate else None
        except ValueError:
            LOGGER.warning("Invalid MLB birthdate for MLBAM ID %s", mlbam_id)
            return None

    def get_career_stats(self, mlbam_id: int) -> dict[str, int | float | None]:
        """Return career MLB at-bats and innings pitched for a player."""
        payload = self._get_json(
            f"people/{int(mlbam_id)}/stats",
            {"stats": "career", "group": "hitting,pitching"},
        )
        career_ab: int | None = None
        career_ip: float | None = None
        stats_groups = payload.get("stats", []) if isinstance(payload, Mapping) else []
        for group in stats_groups:
            if not isinstance(group, Mapping):
                continue
            group_name = group.get("group", {})
            group_name = group_name.get("displayName", "") if isinstance(group_name, Mapping) else str(group_name)
            splits = group.get("splits", [])
            for split in splits if isinstance(splits, list) else []:
                stat = split.get("stat", {}) if isinstance(split, Mapping) else {}
                if not isinstance(stat, Mapping):
                    continue
                if str(group_name).casefold() == "hitting":
                    value = stat.get("atBats")
                    career_ab = _optional_int(value)
                elif str(group_name).casefold() == "pitching":
                    career_ip = parse_innings_pitched(stat.get("inningsPitched"))
        return {"career_ab": career_ab, "career_ip": career_ip}

    def _get_json(self, endpoint: str, params: dict[str, str]) -> Any:
        cache_path = self._cache_path(endpoint, params)
        cached = self._read_cache(cache_path)
        if cached is not None:
            return cached
        for attempt in range(self.max_retries + 1):
            self._wait_for_request_slot()
            try:
                response = self.session.get(
                    f"{self.base_url}{endpoint}",
                    params=params,
                    timeout=self.timeout,
                )
            except requests.RequestException as error:
                if attempt == self.max_retries:
                    raise MLBStatsAPIError(f"MLB Stats API request failed: {error}") from error
                self._backoff(attempt, endpoint)
                continue
            if 500 <= response.status_code < 600:
                if attempt == self.max_retries:
                    raise MLBStatsAPIError(
                        f"MLB Stats API endpoint {endpoint} returned HTTP {response.status_code}"
                    )
                self._backoff(attempt, endpoint)
                continue
            if response.status_code >= 400:
                raise MLBStatsAPIError(
                    f"MLB Stats API endpoint {endpoint} returned HTTP {response.status_code}"
                )
            try:
                payload = response.json()
            except (ValueError, requests.JSONDecodeError) as error:
                raise MLBStatsAPIError(f"MLB Stats API endpoint {endpoint} returned invalid JSON") from error
            self._write_cache(cache_path, payload)
            return payload
        raise MLBStatsAPIError(f"MLB Stats API request to {endpoint} failed unexpectedly")

    def _cache_path(self, endpoint: str, params: dict[str, str]) -> Path:
        cache_key = json.dumps({"endpoint": endpoint, "params": params}, sort_keys=True)
        digest = hashlib.sha256(cache_key.encode("utf-8")).hexdigest()
        return self.cache_dir / f"{digest}.json"

    def _read_cache(self, cache_path: Path) -> Any | None:
        if self.cache_ttl_seconds <= 0:
            return None
        try:
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
            if self._wall_clock() - float(cache["fetched_at"]) <= self.cache_ttl_seconds:
                return cache["payload"]
        except (OSError, ValueError, KeyError, TypeError):
            return None
        return None

    def _write_cache(self, cache_path: Path, payload: Any) -> None:
        if self.cache_ttl_seconds <= 0:
            return
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = cache_path.with_suffix(".tmp")
        try:
            temporary_path.write_text(
                json.dumps({"fetched_at": self._wall_clock(), "payload": payload}, separators=(",", ":")),
                encoding="utf-8",
            )
            temporary_path.replace(cache_path)
        except (OSError, TypeError, ValueError) as error:
            temporary_path.unlink(missing_ok=True)
            LOGGER.warning("Could not cache MLB Stats API response: %s", error)

    def _wait_for_request_slot(self) -> None:
        if self._last_request_at is not None:
            remaining = self.request_interval_seconds - (self._clock() - self._last_request_at)
            if remaining > 0:
                self._sleep(remaining)
        self._last_request_at = self._clock()

    def _backoff(self, attempt: int, endpoint: str) -> None:
        delay = self.backoff_factor * (2**attempt)
        LOGGER.warning("Transient MLB API failure at %s; retrying in %.2f seconds", endpoint, delay)
        if delay > 0:
            self._sleep(delay)


def normalize_person_name(name: str) -> str:
    """Case-fold and remove accents/punctuation for exact full-name matching."""
    decomposed = unicodedata.normalize("NFKD", name.casefold())
    unaccented = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(re.findall(r"[a-z0-9]+", unaccented))


def parse_innings_pitched(value: Any) -> float | None:
    """Convert MLB's baseball notation (e.g. 12.2) into decimal innings."""
    if value is None or str(value).strip() == "":
        return None
    match = re.fullmatch(r"\s*(\d+)(?:\.(\d))?\s*", str(value))
    if not match:
        return None
    innings = int(match.group(1))
    outs = int(match.group(2) or 0)
    if outs > 2:
        LOGGER.warning("Invalid MLB innings-pitched notation: %s", value)
        return None
    return innings + outs / 3


def refresh_minor_bios(
    database_path: Path = DEFAULT_DATABASE_PATH,
    *,
    client: MLBStatsClient | None = None,
) -> int:
    """Refresh bio fields for minors in the latest roster snapshot only."""
    stats_client = client or MLBStatsClient()
    with open_database(database_path, read_only=True) as connection:
        latest_date_row = connection.execute(
            "SELECT max(snapshot_date) FROM roster_snapshots"
        ).fetchone()
        snapshot_date = latest_date_row[0] if latest_date_row else None
        if snapshot_date is None:
            LOGGER.info("No roster snapshots available; nothing to refresh")
            return 0
        minors = connection.execute(
            """SELECT DISTINCT p.fantrax_id, p.name, p.mlbam_id
               FROM roster_snapshots AS roster
               JOIN players AS p ON p.fantrax_id = roster.fantrax_id
               WHERE roster.snapshot_date = ? AND roster.roster_level = 'minors'
               ORDER BY p.fantrax_id""",
            [snapshot_date],
        ).fetchall()

    refreshed_at = datetime.now()
    updates: list[tuple[int | None, date | None, int | None, float | None, datetime, str]] = []
    for fantrax_id, name, current_mlbam_id in minors:
        mlbam_id = int(current_mlbam_id) if current_mlbam_id is not None else stats_client.find_mlbam_id(str(name))
        if mlbam_id is None:
            LOGGER.warning("No exact MLB Stats API identity match for minor leaguer %r", name)
            updates.append((None, None, None, None, refreshed_at, str(fantrax_id)))
            continue
        birthdate = stats_client.get_bio(mlbam_id)
        career_stats = stats_client.get_career_stats(mlbam_id)
        updates.append(
            (
                mlbam_id,
                birthdate,
                career_stats.get("career_ab"),
                career_stats.get("career_ip"),
                refreshed_at,
                str(fantrax_id),
            )
        )

    with open_database(database_path) as connection:
        connection.execute("BEGIN TRANSACTION")
        connection.executemany(
            """UPDATE players SET
                   mlbam_id = COALESCE(?, mlbam_id),
                   birthdate = ?, career_ab = ?, career_ip = ?, bio_refreshed_at = ?
               WHERE fantrax_id = ?""",
            updates,
        )
        connection.execute("COMMIT")
    if updates:
        export_text_snapshot(database_path)
    return len(updates)


def _optional_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None