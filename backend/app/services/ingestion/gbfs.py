"""GBFS (General Bikeshare Feed Specification) client.

Loads shared-vehicle availability — bike stations, free-floating scooters and
car-sharing — from GBFS v2.x/v3 feeds. A source is either a `gbfs.json`
discovery URL or a local directory holding the individual feed files.
"""

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import numpy as np

from app.utils.geo import haversine_np

logger = logging.getLogger(__name__)

# GBFS form_factor → FastMile mode
FORM_FACTOR_MODE = {
    "bicycle": "bike",
    "cargo_bicycle": "bike",
    "scooter": "scooter",
    "scooter_standing": "scooter",
    "scooter_seated": "scooter",
    "moped": "scooter",
    "car": "car_share",
}


@dataclass
class VehicleSpot:
    """A place where shared vehicles can be picked up right now."""

    provider: str
    spot_id: str
    name: str
    lat: float
    lon: float
    kind: str  # "station" or "free_floating"
    available: dict[str, int] = field(default_factory=dict)  # mode → count


@dataclass
class GbfsSnapshot:
    spots: list[VehicleSpot] = field(default_factory=list)
    fetched_at: float = 0.0
    sources: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._index()

    def _index(self) -> None:
        self._lats = np.array([s.lat for s in self.spots], dtype=float)
        self._lons = np.array([s.lon for s in self.spots], dtype=float)

    def nearest_available(
        self, lat: float, lon: float, mode: str, max_km: float
    ) -> tuple[VehicleSpot, float] | None:
        """Nearest spot with at least one vehicle of `mode`, within max_km."""
        if not self.spots:
            return None
        dists = haversine_np(lat, lon, self._lats, self._lons)
        for i in np.argsort(dists):
            if dists[i] > max_km:
                break
            if self.spots[i].available.get(mode, 0) > 0:
                return self.spots[i], float(dists[i])
        return None

    def can_drop_off(self, spot: VehicleSpot, lat: float, lon: float, max_km: float = 0.4) -> bool:
        """Free-floating vehicles can be left anywhere; station vehicles need a station nearby."""
        if spot.kind != "station":
            return True
        dists = haversine_np(lat, lon, self._lats, self._lons)
        return any(
            dists[i] <= max_km and s.kind == "station" and s.provider == spot.provider
            for i, s in enumerate(self.spots)
        )

    def count_by_mode(self) -> dict[str, int]:
        totals: dict[str, int] = {}
        for spot in self.spots:
            for mode, n in spot.available.items():
                totals[mode] = totals.get(mode, 0) + n
        return totals


async def _fetch_json(client: httpx.AsyncClient, base: str, ref: str) -> dict[str, Any]:
    """Fetch a feed file by URL, or read it from a local directory."""
    if base.startswith(("http://", "https://")):
        response = await client.get(ref)
        response.raise_for_status()
        return response.json()  # type: ignore[no-any-return]
    return json.loads((Path(base) / Path(ref).name).read_text())  # type: ignore[no-any-return]


def _feed_urls(discovery: dict[str, Any]) -> dict[str, str]:
    data = discovery.get("data", {})
    feeds = data.get("feeds")  # v3
    if feeds is None and data:  # v2: keyed by language
        lang = "en" if "en" in data else next(iter(data))
        feeds = data[lang].get("feeds", [])
    return {f["name"]: f["url"] for f in feeds or []}


async def load_gbfs_source(client: httpx.AsyncClient, source: str) -> list[VehicleSpot]:
    """Load one GBFS system into vehicle spots."""
    is_url = source.startswith(("http://", "https://"))
    discovery = await _fetch_json(client, source, source if is_url else "gbfs.json")
    urls = _feed_urls(discovery)

    async def feed(name: str) -> dict[str, Any] | None:
        if name not in urls:
            return None
        return (await _fetch_json(client, source, urls[name])).get("data", {})

    system = await feed("system_information") or {}
    provider = system.get("name", source)
    if isinstance(provider, list):  # v3 localized strings
        provider = provider[0].get("text", source) if provider else source

    vtypes = await feed("vehicle_types") or {}
    type_mode = {
        vt["vehicle_type_id"]: FORM_FACTOR_MODE.get(vt.get("form_factor", "bicycle"), "bike")
        for vt in vtypes.get("vehicle_types", [])
    }

    spots: list[VehicleSpot] = []

    info = await feed("station_information") or {}
    status = await feed("station_status") or {}
    status_by_id = {s["station_id"]: s for s in status.get("stations", [])}
    for st in info.get("stations", []):
        stat = status_by_id.get(st["station_id"], {})
        if stat and not (stat.get("is_renting", True) and stat.get("is_installed", True)):
            continue
        available: dict[str, int] = {}
        if stat.get("vehicle_types_available"):
            for vt in stat["vehicle_types_available"]:
                mode = type_mode.get(vt["vehicle_type_id"], "bike")
                available[mode] = available.get(mode, 0) + int(vt.get("count", 0))
        else:
            n = stat.get("num_bikes_available", stat.get("num_vehicles_available", 0))
            available["bike"] = int(n)
        name = st.get("name", st["station_id"])
        if isinstance(name, list):
            name = name[0].get("text", st["station_id"]) if name else st["station_id"]
        spots.append(
            VehicleSpot(
                provider,
                st["station_id"],
                name,
                float(st["lat"]),
                float(st["lon"]),
                "station",
                available,
            )
        )

    free = await feed("free_bike_status") or await feed("vehicle_status") or {}
    for v in free.get("bikes", free.get("vehicles", [])):
        if v.get("is_reserved") or v.get("is_disabled") or "lat" not in v:
            continue
        mode = type_mode.get(v.get("vehicle_type_id", ""), "bike")
        vid = v.get("bike_id", v.get("vehicle_id", ""))
        spots.append(
            VehicleSpot(
                provider,
                vid,
                f"{provider} {mode}",
                float(v["lat"]),
                float(v["lon"]),
                "free_floating",
                {mode: 1},
            )
        )
    return spots


async def load_gbfs(sources: list[str], timeout: float = 10.0) -> GbfsSnapshot:
    """Load all configured GBFS systems; a failing source is logged and skipped."""
    spots: list[VehicleSpot] = []
    errors: list[str] = []
    async with httpx.AsyncClient(timeout=timeout) as client:
        for source in sources:
            try:
                spots.extend(await load_gbfs_source(client, source))
            except (httpx.HTTPError, OSError, ValueError, KeyError) as exc:
                logger.warning("GBFS source %s failed: %s", source, exc)
                errors.append(f"{source}: {exc}")
    return GbfsSnapshot(spots=spots, fetched_at=time.time(), sources=sources, errors=errors)
