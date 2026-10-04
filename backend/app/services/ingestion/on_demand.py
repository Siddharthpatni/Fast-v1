"""On-demand (MoD) service areas.

On-demand operators rarely publish a standard feed, so service areas are
configured as circles with a typical wait time and speed. When trip data from
the operator is available, the wait-time model in app.services.ml replaces the
fixed `wait_min`.
"""

import json
from dataclasses import dataclass
from pathlib import Path

from app.utils.geo import haversine_distance


@dataclass(frozen=True)
class OnDemandZone:
    id: str
    name: str
    lat: float
    lon: float
    radius_km: float
    wait_min: float
    speed_kmh: float
    provider: str

    def contains(self, lat: float, lon: float) -> bool:
        return haversine_distance(self.lat, self.lon, lat, lon) <= self.radius_km


def load_zones(path: str | Path) -> list[OnDemandZone]:
    path = Path(path)
    if not path.exists():
        return []
    raw = json.loads(path.read_text())
    return [OnDemandZone(**z) for z in raw.get("zones", [])]


def zone_serving(
    zones: list[OnDemandZone], a: tuple[float, float], b: tuple[float, float]
) -> OnDemandZone | None:
    """The zone with the shortest wait that contains both endpoints, if any."""
    serving = [z for z in zones if z.contains(*a) and z.contains(*b)]
    return min(serving, key=lambda z: z.wait_min) if serving else None
