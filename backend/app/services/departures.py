"""Live departures from a stop: FastMile's own network inside its region, Transitous elsewhere.

Each departure carries its live delay, platform, booking need, and the active FastMile
disruption on its line (if any), so the board shows what the planner would avoid.
"""

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta

import numpy as np

from app.i18n import tr
from app.services.optimisation.check import DisruptionImpact
from app.services.routing.network import TransitNetwork, normalize_line

SAME_STATION_KM = 0.3  # Platforms of one station: same name, this close
FIND_STOP_KM = 0.6


@dataclass
class Departure:
    time: datetime  # Expected (live) departure
    scheduled_time: datetime
    delay_minutes: int | None  # None = no live data
    line_name: str
    line_color: str | None
    mode: str
    headsign: str
    platform: str | None = None
    cancelled: bool = False
    on_request: bool = False
    disruption_id: int | None = None
    disruption: str | None = None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def _affecting(
    d: list[DisruptionImpact], when: datetime, route_id: str | None, line: str, in_region: bool
) -> DisruptionImpact | None:
    for impact in d:
        if not impact.active_between(when, when):
            continue
        if route_id is not None and route_id in impact.route_ids:
            return impact
        if in_region and route_id is None and normalize_line(line) in impact.lines:
            return impact
    return None


def _destination(net: TransitNetwork, trip: int, here: str, lang: str) -> str:
    """Where a trip goes. A loop back to this station is named by its halfway stop."""
    conns = net.trip_conns[trip]
    last = net.stop_names[net.l_arr_stop[conns[-1]]]
    if last != here:
        return last
    halfway = net.stop_names[net.l_arr_stop[conns[len(conns) // 2]]]
    return tr(lang, f"Loop via {halfway}", f"Ringlinie über {halfway}")


def network_departures(
    net: TransitNetwork,
    midnight: datetime,
    lat: float,
    lon: float,
    when: datetime,
    impacts: list[DisruptionImpact],
    n: int = 12,
    lang: str = "en",
) -> tuple[dict[str, object], list[Departure]] | None:
    """Next departures at the station nearest (lat, lon), or None if no stop is near."""
    near = net.nearby_stops(lat, lon, FIND_STOP_KM)
    if not near:
        return None
    first = near[0][0]
    name = net.stop_names[first]
    station = [
        s
        for s, km in net.nearby_stops(*net.stop_coords(first), SAME_STATION_KM)
        if net.stop_names[s] == name
    ]
    now = int((when - midnight).total_seconds()) - 60  # Include what is leaving right now
    picked = np.nonzero(np.isin(net.c_dep_stop, station) & (net.c_dep >= now))[0][: n * 2]
    departures, seen = [], set()
    for c in picked.tolist():
        trip = int(net.c_trip[c])
        if trip in seen:
            continue
        seen.add(trip)
        route = int(net.trip_route[trip])
        delay = net.trip_delay.get(trip)
        live = midnight + timedelta(seconds=int(net.c_dep[c]))
        line = net.route_short[route] or net.route_long[route]
        impact = _affecting(impacts, live, net.route_ids[route], line, True)
        departures.append(
            Departure(
                time=live,
                scheduled_time=live - timedelta(seconds=delay or 0),
                delay_minutes=None if delay is None else round(delay / 60),
                line_name=line,
                line_color=net.route_color[route],
                mode=net.route_mode[route],
                headsign=net.trip_headsign[trip] or _destination(net, trip, name, lang),
                platform=net.stop_platform[int(net.c_dep_stop[c])] or None,
                on_request=trip in net.trip_on_request,
                disruption_id=impact.id if impact else None,
                disruption=impact.summary if impact else None,
            )
        )
        if len(departures) == n:
            break
    stop_lat, stop_lon = net.stop_coords(first)
    return {"name": name, "lat": stop_lat, "lon": stop_lon}, departures


def mark_disruptions(
    departures: list[Departure], impacts: list[DisruptionImpact], in_region: bool
) -> None:
    """Departures from another feed: match FastMile disruptions by line name in the region."""
    for dep in departures:
        impact = _affecting(impacts, dep.time, None, dep.line_name, in_region)
        if impact:
            dep.disruption_id, dep.disruption = impact.id, impact.summary
