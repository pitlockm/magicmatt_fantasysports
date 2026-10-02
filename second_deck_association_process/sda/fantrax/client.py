"""HTTP client for Fantrax's public fantasy league API."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import requests

LOGGER = logging.getLogger(__name__)
BASE_URL = "https://www.fantrax.com/fxea/general/"


class FantraxAPIError(RuntimeError):
    """Raised when a Fantrax request fails or returns invalid JSON."""


class FantraxClient:
    """Fetch league and player data from the Fantrax API."""

    def __init__(
        self,
        league_id: str,
        user_secret_id: str | None = None,
        *,
        session: requests.Session | None = None,
        base_url: str = BASE_URL,
        timeout: float = 20.0,
        max_retries: int = 3,
        backoff_factor: float = 0.5,
        cache_dir: Path | None = None,
        cache_ttl_seconds: float = 21_600.0,
        request_interval_seconds: float = 1.0,
        sleeper: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        """Create a client for a league, optionally enabling account lookup."""
        if not league_id:
            raise ValueError("league_id must not be empty")
        if max_retries < 0:
            raise ValueError("max_retries must be zero or greater")
        if timeout <= 0:
            raise ValueError("timeout must be greater than zero")

        self.league_id = league_id
        self.user_secret_id = user_secret_id
        self.session = session or requests.Session()
        self.base_url = base_url.rstrip("/") + "/"
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff_factor = max(0.0, backoff_factor)
        self.cache_dir = cache_dir or Path(__file__).resolve().parents[2] / "data" / "cache"
        self.cache_ttl_seconds = max(0.0, cache_ttl_seconds)
        self.request_interval_seconds = max(0.0, request_interval_seconds)
        self._sleep = sleeper
        self._clock = clock
        self._wall_clock = wall_clock
        self._last_request_at: float | None = None

    def get_player_ids(self) -> Any:
        """Return the MLB player ID directory used by other API calls."""
        return self._get_json("getPlayerIds", {"sport": "MLB"})

    def get_league_info(self) -> Any:
        """Return configuration and team information for this league."""
        return self._get_json("getLeagueInfo", {"leagueId": self.league_id})

    def get_team_rosters(self, period: int | None = None) -> Any:
        """Return all team rosters, optionally for a specific lineup period."""
        params: dict[str, str | int] = {"leagueId": self.league_id}
        if period is not None:
            params["period"] = period
        return self._get_json("getTeamRosters", params)

    def get_standings(self) -> Any:
        """Return the current league standings."""
        return self._get_json("getStandings", {"leagueId": self.league_id})

    def get_matchup_scores(self, period: int | None = None) -> Any:
        """Return matchup scores, optionally for a specific scoring period."""
        params: dict[str, str | int] = {"leagueId": self.league_id}
        if period is not None:
            params["period"] = period
        return self._get_json("getMatchupScores", params)

    def get_draft_picks(self) -> Any:
        """Return future and current draft pick ownership."""
        return self._get_json("getDraftPicks", {"leagueId": self.league_id})

    def get_draft_results(self) -> Any:
        """Return completed and in-progress draft results."""
        return self._get_json("getDraftResults", {"leagueId": self.league_id})

    def get_adp(
        self,
        position: str | None = None,
        start: int | None = None,
        limit: int | None = None,
    ) -> Any:
        """Return MLB average draft position data with optional filters."""
        params: dict[str, str | int] = {"sport": "MLB"}
        if position is not None:
            params["position"] = position
        if start is not None:
            params["start"] = start
        if limit is not None:
            params["limit"] = limit
        return self._get_json("getAdp", params)

    def get_leagues(self) -> Any:
        """Return leagues associated with the configured Fantrax account."""
        if not self.user_secret_id:
            raise FantraxAPIError(
                "getLeagues requires FANTRAX_USER_SECRET_ID in the environment"
            )
        return self._get_json("getLeagues", {"userSecretId": self.user_secret_id})

    def _get_json(self, endpoint: str, params: dict[str, str | int]) -> Any:
        cache_path = self._cache_path(endpoint, params)
        cached_payload = self._read_cache(cache_path)
        if cached_payload is not None:
            LOGGER.debug("Using cached Fantrax response for %s", endpoint)
            return cached_payload

        url = f"{self.base_url}{endpoint}"
        for attempt in range(self.max_retries + 1):
            self._wait_for_request_slot()
            try:
                response = self.session.get(url, params=params, timeout=self.timeout)
            except requests.RequestException as error:
                if attempt == self.max_retries:
                    raise FantraxAPIError(
                        f"Fantrax request to {endpoint} failed after "
                        f"{attempt + 1} attempt(s): {error}"
                    ) from error
                self._backoff(attempt, endpoint)
                continue

            if 500 <= response.status_code < 600:
                if attempt == self.max_retries:
                    raise FantraxAPIError(
                        f"Fantrax endpoint {endpoint} returned HTTP "
                        f"{response.status_code} after {attempt + 1} attempt(s)"
                    )
                self._backoff(attempt, endpoint)
                continue
            if response.status_code >= 400:
                raise FantraxAPIError(
                    f"Fantrax endpoint {endpoint} returned HTTP {response.status_code}"
                )

            try:
                payload = response.json()
            except (ValueError, requests.JSONDecodeError) as error:
                raise FantraxAPIError(
                    f"Fantrax endpoint {endpoint} returned invalid JSON"
                ) from error

            self._write_cache(cache_path, payload)
            return payload

        raise FantraxAPIError(f"Fantrax request to {endpoint} failed unexpectedly")

    def _cache_path(self, endpoint: str, params: dict[str, str | int]) -> Path:
        cache_key = json.dumps(
            {"endpoint": endpoint, "params": params},
            sort_keys=True,
            separators=(",", ":"),
        )
        digest = hashlib.sha256(cache_key.encode("utf-8")).hexdigest()
        return self.cache_dir / f"{digest}.json"

    def _read_cache(self, cache_path: Path) -> Any | None:
        if self.cache_ttl_seconds <= 0:
            return None
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            fetched_at = float(cached["fetched_at"])
            if self._wall_clock() - fetched_at <= self.cache_ttl_seconds:
                return cached["payload"]
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
                json.dumps(
                    {"fetched_at": self._wall_clock(), "payload": payload},
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                encoding="utf-8",
            )
            temporary_path.replace(cache_path)
        except (OSError, TypeError, ValueError) as error:
            temporary_path.unlink(missing_ok=True)
            LOGGER.warning("Could not cache Fantrax response: %s", error)

    def _wait_for_request_slot(self) -> None:
        if self._last_request_at is not None:
            elapsed = self._clock() - self._last_request_at
            remaining = self.request_interval_seconds - elapsed
            if remaining > 0:
                self._sleep(remaining)
        self._last_request_at = self._clock()

    def _backoff(self, attempt: int, endpoint: str) -> None:
        delay = self.backoff_factor * (2**attempt)
        LOGGER.warning(
            "Transient failure calling %s; retrying in %.2f seconds",
            endpoint,
            delay,
        )
        if delay > 0:
            self._sleep(delay)