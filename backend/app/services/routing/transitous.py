"""Germany-wide transit routing through Transitous, for trips outside the loaded feed.

FastMile's own planner (csa.py) holds one region's timetable in memory; the national
timetable is ~100x larger and does not fit. Transitous (https://transitous.org) is a free,
community-run MOTIS server with the DELFI timetable for all of Germany and live delays.
Its itineraries are converted to Plans, so they go through the same optimisation check,
disruption matching and Jev review as FastMile's own plans.

Fair use: requests carry an identifying User-Agent, at most two run at a time, and
identical requests are cached for a minute.
"""

import asyncio
import logging
import time
from datetime import datetime
from typing import Any

import httpx

from app.config import settings
from app.services.departures import Departure
from app.services.routing.types import Leg, Plan, StopCall
from app.utils.geo import haversine_distance

logger = logging.getLogger(__name__)

SOURCE = "transitous"
CACHE_SECONDS = 60
CACHE_SIZE = 200

# MOTIS mode → FastMile mode
MODES = {
    "WALK": "walk",
    "BIKE": "bike",
    "BUS": "bus",
    "COACH": "bus",
    "TRAM": "tram",
    "SUBWAY": "subway",
    "FERRY": "ferry",
    "AERIAL_LIFT": "gondola",
    "FUNICULAR": "funicular",
    "RAIL": "train",
    "SUBURBAN": "train",
    "REGIONAL_RAIL": "train",
    "REGIONAL_FAST_RAIL": "train",
    "HIGHSPEED_RAIL": "train",
    "LONG_DISTANCE": "train",
    "NIGHT_RAIL": "train",
}
# Long-distance trains are known by train number ("ICE 651"), not by route ("19")
TRAIN_NUMBER_MODES = {"HIGHSPEED_RAIL", "LONG_DISTANCE", "NIGHT_RAIL"}
# FastMile mode → MOTIS transit modes to allow
TRANSIT_FILTER = {
    "bus": ["BUS", "COACH"],
    "tram": ["TRAM"],
    "subway": ["SUBWAY"],
    "train": ["RAIL"],  # RAIL covers every rail mode in MOTIS
    "ferry": ["FERRY"],
}


class TransitousError(Exception):
    pass


def decode_polyline(points: str, precision: int = 6) -> list[tuple[float, float]]:
    """Google encoded polyline → [(lat, lon)]."""
    coords: list[tuple[float, float]] = []
    index = lat = lon = 0
    factor = 10**precision
    while index < len(points):
        deltas = []
        for _ in range(2):
            shift = result = 0
            while True:
                b = ord(points[index]) - 63
                index += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            deltas.append(~(result >> 1) if result & 1 else result >> 1)
        lat += deltas[0]
        lon += deltas[1]
        coords.append((lat / factor, lon / factor))
    return coords


def _time(value: str, tz) -> datetime:  # noqa: ANN001
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(tz)


def _line_name(leg: dict) -> str | None:
    if leg["mode"] in TRAIN_NUMBER_MODES and leg.get("tripShortName"):
        return str(leg["tripShortName"])
    return leg.get("routeShortName") or leg.get("displayName")


def _leg(raw: dict, tz) -> Leg:  # noqa: ANN001
    mode = MODES.get(raw["mode"], raw["mode"].lower())
    start, end = raw["from"], raw["to"]
    geometry = raw.get("legGeometry") or {}
    path = (
        decode_polyline(geometry["points"], int(geometry.get("precision", 6)))
        if geometry.get("points")
        else []
    ) or [(start["lat"], start["lon"]), (end["lat"], end["lon"])]
    departure, arrival = _time(raw["startTime"], tz), _time(raw["endTime"], tz)
    leg = Leg(
        mode=mode,
        from_name=start["name"],
        to_name=end["name"],
        from_lat=start["lat"],
        from_lon=start["lon"],
        to_lat=end["lat"],
        to_lon=end["lon"],
        departure=departure,
        arrival=arrival,
        distance_km=0.0,
        source=SOURCE,
    )
    if mode == "walk" or "routeShortName" not in raw and "tripId" not in raw:
        leg.distance_km = round((raw.get("distance") or 0) / 1000, 2)
        leg.path = path
        return leg
    calls = [(start, "departure")]
    calls += [(s, "arrival") for s in raw.get("intermediateStops", [])]
    calls += [(end, "arrival")]
    leg.stops = [
        StopCall(
            s.get("stopId") or s["name"],
            s["name"],
            s["lat"],
            s["lon"],
            _time(s.get(key) or s.get("departure") or s.get("arrival") or raw["startTime"], tz),
        )
        for s, key in calls
    ]
    leg.path = path
    leg.distance_km = round(
        sum(
            haversine_distance(a[0], a[1], b[0], b[1]) for a, b in zip(path, path[1:], strict=False)
        ),
        2,
    )
    leg.line_name = _line_name(raw)
    leg.line_color = f"#{raw['routeColor']}" if raw.get("routeColor") else None
    leg.route_id = raw.get("routeId")
    leg.trip_id = raw.get("tripId")
    leg.headsign = raw.get("headsign")
    leg.provider = raw.get("agencyName")
    leg.platform = start.get("track") or start.get("scheduledTrack")
    if raw.get("realTime") and raw.get("scheduledStartTime"):
        leg.delay_min = round(
            (departure - _time(raw["scheduledStartTime"], tz)).total_seconds() / 60
        )
    leg.cancelled = bool(raw.get("cancelled"))
    return leg


def itinerary_to_plan(itinerary: dict, tz) -> Plan:  # noqa: ANN001
    legs = [_leg(raw, tz) for raw in itinerary["legs"]]
    plan = Plan(legs=legs, source=SOURCE)
    return plan


class TransitousClient:
    def __init__(
        self, url: str | None = None, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.url = (url or settings.transitous_url).rstrip("/")
        self._client = httpx.AsyncClient(
            timeout=20.0,
            transport=transport,
            headers={"User-Agent": "FastMile/0.1 (TU Clausthal student research project)"},
        )
        self._slots = asyncio.Semaphore(2)
        self._cache: dict[tuple, tuple[float, list[dict]]] = {}
        self.last_error: str | None = None
        self.requests = 0

    def describe(self) -> dict[str, object]:
        return {
            "url": self.url,
            "requests": self.requests,
            "status": "untested" if not self.requests else ("error" if self.last_error else "ok"),
            "last_error": self.last_error,
        }

    async def plan(
        self,
        origin: tuple[str, float, float],
        destination: tuple[str, float, float],
        when: datetime,
        arrive_by: datetime | None = None,
        modes: list[str] | None = None,
        results: int = 6,
    ) -> list[Plan]:
        params: dict[str, str | int] = {
            "fromPlace": f"{origin[1]:.6f},{origin[2]:.6f}",
            "toPlace": f"{destination[1]:.6f},{destination[2]:.6f}",
            "time": (arrive_by or when).isoformat(timespec="seconds"),
            "arriveBy": "true" if arrive_by else "false",
            "numItineraries": results,
        }
        if modes is not None:
            allowed = sorted({m for mode in modes for m in TRANSIT_FILTER.get(mode, [])})
            if not allowed:
                return []  # Only sharing/on-demand modes: Transitous can't plan those for us
            params["transitModes"] = ",".join(allowed)
        itineraries = await self._fetch(params)
        plans = [itinerary_to_plan(it, when.tzinfo) for it in itineraries]
        for plan in plans:
            if plan.legs:
                plan.legs[0].from_name = origin[0]
                plan.legs[-1].to_name = destination[0]
        return plans

    async def departures(
        self,
        lat: float,
        lon: float,
        tz,
        n: int = 12,  # noqa: ANN001
    ) -> tuple[dict[str, object], list[Departure]] | None:
        """Next departures at the stop nearest (lat, lon), or None if there is none."""
        stops = await self._get(
            "/api/v1/reverse-geocode", {"place": f"{lat},{lon}", "type": "STOP"}
        )
        stop = next((s for s in stops if s.get("type") == "STOP" and s.get("id")), None)
        if stop is None:
            return None
        data = await self._get("/api/v5/stoptimes", {"stopId": stop["id"], "n": n})
        departures = []
        for item in data.get("stopTimes", []):
            place = item["place"]
            when = place.get("departure") or place.get("arrival")
            if not when:
                continue
            live = _time(when, tz)
            scheduled = _time(place.get("scheduledDeparture") or when, tz)
            departures.append(
                Departure(
                    time=live,
                    scheduled_time=scheduled,
                    delay_minutes=round((live - scheduled).total_seconds() / 60)
                    if item.get("realTime")
                    else None,
                    line_name=_line_name(item) or MODES.get(item.get("mode", ""), "transit"),
                    line_color=f"#{item['routeColor']}" if item.get("routeColor") else None,
                    mode=MODES.get(item.get("mode", ""), "bus"),
                    headsign=item.get("headsign") or "",
                    platform=place.get("track") or place.get("scheduledTrack"),
                    cancelled=bool(item.get("cancelled") or place.get("cancelled")),
                )
            )
        return {"name": stop["name"], "lat": stop["lat"], "lon": stop["lon"]}, departures

    async def _get(self, path: str, params: dict[str, str | int]) -> Any:
        async with self._slots:
            self.requests += 1
            try:
                response = await self._client.get(f"{self.url}{path}", params=params)
                response.raise_for_status()
                result = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"[:200]
                logger.warning("Transitous request failed: %s", exc)
                raise TransitousError("Germany-wide timetable (Transitous) is unavailable") from exc
        self.last_error = None
        return result

    async def _fetch(self, params: dict[str, str | int]) -> list[dict]:
        key = tuple(sorted(params.items()))
        cached = self._cache.get(key)
        if cached and time.monotonic() - cached[0] < CACHE_SECONDS:
            return cached[1]
        async with self._slots:
            self.requests += 1
            try:
                response = await self._client.get(f"{self.url}/api/v5/plan", params=params)
                response.raise_for_status()
                itineraries = response.json().get("itineraries", [])
            except (httpx.HTTPError, ValueError) as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"[:200]
                logger.warning("Transitous request failed: %s", exc)
                raise TransitousError("Germany-wide routing (Transitous) is unavailable") from exc
        self.last_error = None
        self._cache[key] = (time.monotonic(), itineraries)
        if len(self._cache) > CACHE_SIZE:
            self._cache.pop(next(iter(self._cache)))
        return itineraries

    async def close(self) -> None:
        await self._client.aclose()
