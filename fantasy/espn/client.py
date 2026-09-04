"""Thin, polite client for ESPN's v3 fantasy football API.

Two things make this API annoying, and this module hides both:

1. Endpoint split. The current season lives under `seasons/{year}/segments/0/
   leagues/{id}`, while older seasons often only answer on `leagueHistory/{id}
   ?seasonId={year}` (which returns a *list* of one league). We try the modern
   URL first and transparently fall back.
2. Everything interesting is behind a `view` parameter, and some views need a
   JSON blob in an `x-fantasy-filter` header.

Every response is cached to data/raw/ so re-running analytics never re-hits the
network. Pass refresh=True to bypass.
"""
from __future__ import annotations

import hashlib
import json
import logging
import random
import time
from datetime import date
from pathlib import Path
from typing import Any

import requests

from ..config import RAW_DIR, Config

log = logging.getLogger(__name__)

BASE = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

MAX_RETRIES = 4
BACKOFF_BASE = 1.5


class ESPNError(RuntimeError):
    """Raised when ESPN returns something we cannot use."""


class NotFound(ESPNError):
    """League/season combination does not exist (or we lack access)."""


class AuthError(ESPNError):
    """League is private and our cookies were rejected."""


def current_season() -> int:
    """The NFL season year ESPN is currently serving.

    ESPN rolls a new season over in the summer, well before Week 1.
    """
    today = date.today()
    return today.year if today.month >= 6 else today.year - 1


class ESPNClient:
    def __init__(self, config: Config, *, refresh: bool = False, throttle: float = 0.4):
        self.config = config
        self.refresh = refresh
        self.throttle = throttle
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
        if config.cookies:
            self.session.cookies.update(config.cookies)
        self._last_call = 0.0

    # ---------------------------------------------------------------- caching

    def _cache_path(self, season: int, key: str) -> Path:
        digest = hashlib.sha1(key.encode()).hexdigest()[:16]
        season_dir = RAW_DIR / str(self.config.league_id) / str(season)
        season_dir.mkdir(parents=True, exist_ok=True)
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in key)[:60]
        return season_dir / f"{safe}.{digest}.json"

    # ------------------------------------------------------------------- http

    def _sleep(self) -> None:
        gap = time.monotonic() - self._last_call
        if gap < self.throttle:
            time.sleep(self.throttle - gap)
        self._last_call = time.monotonic()

    def _request(self, url: str, params: dict, headers: dict) -> Any:
        last_exc: Exception | None = None
        for attempt in range(MAX_RETRIES):
            self._sleep()
            try:
                resp = self.session.get(url, params=params, headers=headers, timeout=30)
            except requests.RequestException as exc:  # network hiccup -> retry
                last_exc = exc
                time.sleep(BACKOFF_BASE**attempt + random.random())
                continue

            if resp.status_code == 404:
                raise NotFound(f"404 for {resp.url}")
            if resp.status_code in (401, 403):
                raise AuthError(
                    f"{resp.status_code} for {resp.url}\n"
                    "This league is private. Set ESPN_S2 and SWID in .env "
                    "(copy them from your browser cookies on fantasy.espn.com)."
                )
            if resp.status_code == 429 or resp.status_code >= 500:
                wait = BACKOFF_BASE**attempt + random.random()
                log.warning("ESPN %s on %s; retrying in %.1fs", resp.status_code, url, wait)
                time.sleep(wait)
                last_exc = ESPNError(f"{resp.status_code} for {resp.url}")
                continue

            resp.raise_for_status()
            try:
                return resp.json()
            except ValueError as exc:
                raise ESPNError(f"Non-JSON response from {resp.url}") from exc

        raise ESPNError(f"Gave up on {url} after {MAX_RETRIES} attempts") from last_exc

    # ------------------------------------------------------------------- api

    def fetch(
        self,
        season: int,
        views: list[str] | str,
        *,
        params: dict | None = None,
        filters: dict | None = None,
        cache_key: str | None = None,
    ) -> dict:
        """Fetch one league document for `season` with the given view(s)."""
        views = [views] if isinstance(views, str) else list(views)
        params = dict(params or {})
        key = cache_key or "-".join(views) + "".join(
            f"-{k}{v}" for k, v in sorted(params.items())
        )
        path = self._cache_path(season, key)

        if path.exists() and not self.refresh:
            return json.loads(path.read_text())

        headers = {}
        if filters is not None:
            headers["x-fantasy-filter"] = json.dumps(filters)

        query = {**params, "view": views}
        modern = f"{BASE}/seasons/{season}/segments/0/leagues/{self.config.league_id}"
        history = f"{BASE}/leagueHistory/{self.config.league_id}"

        try:
            payload = self._request(modern, query, headers)
        except NotFound:
            # Older seasons only answer on the leagueHistory endpoint.
            payload = self._request(history, {**query, "seasonId": season}, headers)

        # leagueHistory wraps the league in a single-element list.
        if isinstance(payload, list):
            if not payload:
                raise NotFound(f"Empty leagueHistory payload for {season}")
            payload = payload[0]
        if not isinstance(payload, dict):
            raise ESPNError(f"Unexpected payload type {type(payload)} for {season}")

        path.write_text(json.dumps(payload))
        return payload

    def fetch_activity(self, season: int, *, offset: int = 0, limit: int = 25) -> dict:
        """Page through the league activity feed (adds, drops, trades)."""
        filters = {
            "topics": {
                "filterType": {"value": ["ACTIVITY_TRANSACTIONS"]},
                "limit": limit,
                "limitPerMessageSet": {"value": limit},
                "offset": offset,
                "sortMessageDate": {"sortPriority": 1, "sortAsc": False},
                "sortFor": {"sortPriority": 2, "sortAsc": False},
                "filterIncludeMessageTypeIds": {
                    "value": [178, 180, 179, 239, 181, 244, 224, 226]
                },
            }
        }
        key = f"activity-o{offset}-l{limit}"
        path = self._cache_path(season, key)
        if path.exists() and not self.refresh:
            return json.loads(path.read_text())

        url = f"{BASE}/seasons/{season}/segments/0/leagues/{self.config.league_id}/communication/"
        payload = self._request(
            url,
            {"view": ["kona_league_communication"]},
            {"x-fantasy-filter": json.dumps(filters)},
        )
        if isinstance(payload, list):
            payload = payload[0] if payload else {}
        path.write_text(json.dumps(payload))
        return payload

    # -------------------------------------------------------------- discovery

    def probe(self, season: int) -> dict | None:
        """Return the league's settings doc for `season`, or None if absent."""
        try:
            return self.fetch(season, ["mSettings"])
        except NotFound:
            return None
        except ESPNError as exc:
            log.warning("probe %s failed: %s", season, exc)
            return None

    def discover_seasons(self, *, floor: int = 2001) -> list[int]:
        """Walk backwards from the newest season until the league disappears.

        Tolerates a single gap year (ESPN occasionally drops one) before
        concluding the league did not exist yet.
        """
        seasons: list[int] = []
        newest = current_season()

        # The "current" season may not have started yet; step back if empty.
        while newest > floor and self.probe(newest) is None:
            log.info("season %s not available, stepping back", newest)
            newest -= 1
        if newest <= floor:
            raise ESPNError(
                f"Could not find any season for league {self.config.league_id}. "
                "Check LEAGUE_ID, and ESPN_S2/SWID if the league is private."
            )

        seasons.append(newest)
        misses = 0
        year = newest - 1
        while year >= floor and misses < 2:
            if self.probe(year) is not None:
                seasons.append(year)
                misses = 0
            else:
                misses += 1
            year -= 1
        return sorted(seasons)
