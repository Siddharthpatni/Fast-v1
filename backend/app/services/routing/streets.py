"""Street-network travel times and geometry for walk, bike, scooter, car and on-demand legs.

Uses the OSRM HTTP API. The default is the FOSSGIS public servers that
openstreetmap.org itself uses (free, no key, fair use; one server per profile).
They answer single routes quickly but rate-limit concurrent requests, so calls
are spaced out and limited to two at a time. For heavy use, run your own OSRM
with a Germany extract and point STREET_ROUTING_URL at it.

Every call is cached. A failure opens a short circuit breaker, so routing falls
back to straight-line estimates instead of waiting on a server that is down.
"""

import logging
import threading
import time
from collections import OrderedDict
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# FastMile mode → OSRM profile
PROFILES = {
    "walk": "foot",
    "bike": "bike",
    "scooter": "bike",
    "car_share": "car",
    "on_demand": "car",
}
# OSRM profiles assume a speed; adjust for modes that share a profile
SPEED_FACTOR = {"scooter": 15 / 18, "on_demand": 1.15}  # Scooters are faster; shuttles pool
CACHE_SIZE = 5000
BREAKER_SECONDS = 60
RATE_LIMITED_SECONDS = 10  # A 429 pauses briefly; other failures pause for BREAKER_SECONDS
MIN_INTERVAL_S = 0.2  # Polite to the free public servers
Coord = tuple[float, float]  # lat, lon


class StreetRouter:
    def __init__(
        self,
        url_template: str | None = None,
        timeout: float = 4.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.url_template = url_template or settings.street_routing_url
        self._client = httpx.Client(
            timeout=timeout,
            transport=transport,
            headers={"User-Agent": "FastMile/0.1 (TU Clausthal student research project)"},
        )
        self._cache: OrderedDict[tuple[Any, ...], tuple[float, float, list[Coord]]] = OrderedDict()
        self._lock = threading.Lock()  # The planner runs in worker threads
        self._slots = threading.Semaphore(2)  # At most two requests in flight
        self._next_slot = 0.0
        self._down_until = 0.0
        self.calls = 0
        self.failures = 0

    def route(self, mode: str, a: Coord, b: Coord) -> tuple[float, float, list[Coord]] | None:
        """(minutes, km, geometry as lat/lon points) along streets, or None."""
        profile = PROFILES.get(mode)
        if profile is None:
            return None
        key = (profile, _r(a), _r(b))
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                self._cache.move_to_end(key)
        if cached is None:
            cached = self._fetch(profile, a, b)
            if cached is None:
                return None
            with self._lock:
                self._cache[key] = cached
                if len(self._cache) > CACHE_SIZE:
                    self._cache.popitem(last=False)
        minutes, km, geometry = cached
        return minutes * SPEED_FACTOR.get(mode, 1.0), km, geometry

    def close(self) -> None:
        self._client.close()

    def describe(self) -> dict[str, Any]:
        return {
            "url": self.url_template,
            "calls": self.calls,
            "failures": self.failures,
            "available": time.monotonic() >= self._down_until,
        }

    def _fetch(self, profile: str, a: Coord, b: Coord) -> tuple[float, float, list[Coord]] | None:
        if time.monotonic() < self._down_until:
            return None
        url = (
            f"{self.url_template.format(profile=profile)}/route/v1/driving/"
            f"{a[1]:.6f},{a[0]:.6f};{b[1]:.6f},{b[0]:.6f}"
        )
        with self._slots:
            with self._lock:  # Space requests out across threads
                start = max(time.monotonic(), self._next_slot)
                self._next_slot = start + MIN_INTERVAL_S
            time.sleep(max(0.0, start - time.monotonic()))
            self.calls += 1
            try:
                response = self._client.get(
                    url, params={"overview": "simplified", "geometries": "geojson"}
                )
                if response.status_code == 429:
                    self._fail(RATE_LIMITED_SECONDS, "rate-limited")
                    return None
                response.raise_for_status()
                data = response.json()
                if data.get("code") != "Ok" or not data.get("routes"):
                    raise ValueError(data.get("message", data.get("code")))
            except (httpx.HTTPError, ValueError) as exc:
                self._fail(BREAKER_SECONDS, str(exc))
                return None
        best = data["routes"][0]
        geometry = [(lat, lon) for lon, lat in best["geometry"]["coordinates"]]
        return best["duration"] / 60, best["distance"] / 1000, geometry

    def _fail(self, pause: float, reason: str) -> None:
        self.failures += 1
        self._down_until = time.monotonic() + pause
        logger.warning("Street routing paused for %ss: %s", pause, reason)


def _r(c: Coord) -> Coord:
    return round(c[0], 5), round(c[1], 5)


def make_street_router() -> StreetRouter | None:
    if settings.street_routing.lower() in ("", "none", "off"):
        return None
    return StreetRouter()
