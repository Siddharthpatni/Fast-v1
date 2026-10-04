"""Door-to-door multimodal planner.

The regular case from the brief: build candidate chains (first mile → transit
→ last mile, plus direct options), then hand them to the optimisation check,
which scores and filters them as a whole.

For each first-mile mode (walk, bike, scooter, car-sharing, on-demand) and
last-mile mode, the reachable stops become CSA sources/targets with their
door-to-stop times; one CSA run per combination yields one candidate chain.
"""

import math
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np

from app.services.ingestion.gbfs import GbfsSnapshot
from app.services.ingestion.on_demand import OnDemandZone, zone_serving
from app.services.ml.models import OnDemandWaitModel
from app.services.routing.csa import Blocking, Journey, Ride, Transfer, earliest_arrival
from app.services.routing.modes import (
    ACCESS_MODES,
    EGRESS_MODES,
    MAX_DIRECT_WALK_KM,
    MAX_WALK_TO_VEHICLE_KM,
    MODES,
    TRANSIT_MODES,
    WALK,
)
from app.services.routing.network import TransitNetwork
from app.services.routing.streets import PROFILES, StreetRouter
from app.services.routing.types import Leg, Plan, StopCall
from app.utils.geo import haversine_distance

ACCESS_WALK_KM = 1.2
CANDIDATE_STOPS = 12  # Per access/egress mode
LATER_DEPARTURES = 2  # Extra "next connection" options after the fastest one
Point = tuple[str, float, float]  # name, lat, lon


@dataclass
class Segment:
    """An untimed piece of a first/last-mile leg."""

    mode: str
    start: Point
    end: Point
    minutes: float
    km: float
    provider: str | None = None
    vehicles_available: int | None = None
    pickup_in_future: bool = False
    wait_min: float = 0.0


@dataclass
class PlanningContext:
    network: TransitNetwork
    gbfs: GbfsSnapshot = field(default_factory=GbfsSnapshot)
    zones: list[OnDemandZone] = field(default_factory=list)
    wait_model: OnDemandWaitModel = field(default_factory=OnDemandWaitModel)
    tz: ZoneInfo = field(default_factory=lambda: ZoneInfo("Europe/Berlin"))
    streets: StreetRouter | None = None  # None: straight-line × detour estimates

    def midnight(self) -> datetime:
        d = self.network.service_date
        return datetime(d.year, d.month, d.day, tzinfo=self.tz)

    def seconds(self, when: datetime) -> int:
        return int((when - self.midnight()).total_seconds())

    def at(self, seconds: float) -> datetime:
        return self.midnight() + timedelta(seconds=seconds)


# ── First / last mile ──


def _hub_stops(
    ctx: PlanningContext, lat: float, lon: float, radius_km: float
) -> list[tuple[int, float]]:
    """Stops in range, preferring well-served ones over merely close ones."""
    net = ctx.network
    near = [(s, d) for s, d in net.nearby_stops(lat, lon, radius_km) if net.stop_departures[s] > 0]
    near.sort(key=lambda sd: -net.stop_departures[sd[0]] / (1 + sd[1]))
    return near[:CANDIDATE_STOPS]


def _stop_point(net: TransitNetwork, stop: int) -> Point:
    lat, lon = net.stop_coords(stop)
    return net.stop_names[stop], lat, lon


def _walk(a: Point, b: Point) -> Segment:
    km = haversine_distance(a[1], a[2], b[1], b[2])
    return Segment("walk", a, b, WALK.minutes(km), km * WALK.detour)


def first_mile(
    ctx: PlanningContext, origin: Point, mode: str, when: datetime
) -> dict[int, list[Segment]]:
    """Ways to get from the origin to stops with `mode`: stop → segments."""
    net, (_, lat, lon) = ctx.network, origin
    options: dict[int, list[Segment]] = {}
    if mode == "walk":
        for stop, _d in net.nearby_stops(lat, lon, ACCESS_WALK_KM)[:30]:
            options[stop] = [_walk(origin, _stop_point(net, stop))]
    elif mode in ("bike", "scooter", "car_share"):
        profile = MODES[mode]
        found = ctx.gbfs.nearest_available(lat, lon, mode, MAX_WALK_TO_VEHICLE_KM)
        if found is None:
            return {}
        spot, _ = found
        spot_point = (spot.name, spot.lat, spot.lon)
        for stop, km in _hub_stops(ctx, spot.lat, spot.lon, profile.max_km):
            if km < 0.8:
                continue  # Walking is better than renting for a few hundred metres
            # Car-sharing is booked as a round trip; bikes and scooters must be left legally
            if mode != "car_share" and not ctx.gbfs.can_drop_off(spot, *net.stop_coords(stop)):
                continue
            options[stop] = [
                _walk(origin, spot_point),
                Segment(
                    mode,
                    spot_point,
                    _stop_point(net, stop),
                    profile.minutes(km),
                    km * profile.detour,
                    provider=spot.provider,
                    vehicles_available=spot.available.get(mode, 0),
                ),
            ]
    elif mode == "on_demand":
        for zone in (z for z in ctx.zones if z.contains(lat, lon)):
            wait = ctx.wait_model.predict(zone.id, when, zone.wait_min)
            for stop, km in _hub_stops(ctx, lat, lon, zone.radius_km * 2):
                stop_lat, stop_lon = net.stop_coords(stop)
                if km < 1.0 or not zone.contains(stop_lat, stop_lon):
                    continue
                minutes = wait + km * MODES["on_demand"].detour / zone.speed_kmh * 60
                if stop not in options or minutes < sum(s.minutes for s in options[stop]):
                    options[stop] = [
                        Segment(
                            "on_demand",
                            origin,
                            _stop_point(net, stop),
                            minutes,
                            km * MODES["on_demand"].detour,
                            provider=zone.provider,
                            wait_min=wait,
                        )
                    ]
    return options


def last_mile(
    ctx: PlanningContext, destination: Point, mode: str, when: datetime
) -> dict[int, list[Segment]]:
    """Ways to get from stops to the destination with `mode`: stop → segments."""
    net, (_, lat, lon) = ctx.network, destination
    options: dict[int, list[Segment]] = {}
    if mode == "walk":
        for stop, _d in net.nearby_stops(lat, lon, ACCESS_WALK_KM)[:30]:
            options[stop] = [_walk(_stop_point(net, stop), destination)]
    elif mode in ("bike", "scooter"):
        profile = MODES[mode]
        for stop, km in _hub_stops(ctx, lat, lon, profile.max_km):
            if km < 0.8:
                continue
            stop_point = _stop_point(net, stop)
            found = ctx.gbfs.nearest_available(stop_point[1], stop_point[2], mode, 0.3)
            if found is None:
                continue
            spot, _ = found
            if not ctx.gbfs.can_drop_off(spot, lat, lon):
                continue
            spot_point = (spot.name, spot.lat, spot.lon)
            ride_km = haversine_distance(spot.lat, spot.lon, lat, lon)
            options[stop] = [
                _walk(stop_point, spot_point),
                Segment(
                    mode,
                    spot_point,
                    destination,
                    profile.minutes(ride_km),
                    ride_km * profile.detour,
                    provider=spot.provider,
                    vehicles_available=spot.available.get(mode, 0),
                    pickup_in_future=True,
                ),
            ]
    elif mode == "on_demand":
        for zone in (z for z in ctx.zones if z.contains(lat, lon)):
            wait = ctx.wait_model.predict(zone.id, when, zone.wait_min)
            for stop, km in _hub_stops(ctx, lat, lon, zone.radius_km * 2):
                stop_lat, stop_lon = net.stop_coords(stop)
                if km < 1.0 or not zone.contains(stop_lat, stop_lon):
                    continue
                minutes = wait + km * MODES["on_demand"].detour / zone.speed_kmh * 60
                if stop not in options or minutes < sum(s.minutes for s in options[stop]):
                    options[stop] = [
                        Segment(
                            "on_demand",
                            _stop_point(net, stop),
                            destination,
                            minutes,
                            km * MODES["on_demand"].detour,
                            provider=zone.provider,
                            wait_min=wait,
                        )
                    ]
    return options


# ── Building timed plans ──


def _segment_leg(seg: Segment, start: datetime) -> Leg:
    return Leg(
        mode=seg.mode,
        from_name=seg.start[0],
        to_name=seg.end[0],
        from_lat=seg.start[1],
        from_lon=seg.start[2],
        to_lat=seg.end[1],
        to_lon=seg.end[2],
        departure=start,
        arrival=start + timedelta(minutes=seg.minutes),
        distance_km=round(seg.km, 2),
        provider=seg.provider,
        vehicles_available=seg.vehicles_available,
        pickup_in_future=seg.pickup_in_future,
        wait_min=round(seg.wait_min, 1),
        path=[(seg.start[1], seg.start[2]), (seg.end[1], seg.end[2])],
    )


def _segments_forward(segments: list[Segment], start: datetime) -> list[Leg]:
    legs = []
    for seg in segments:
        if seg.mode == "walk" and seg.km < 0.02:
            continue  # Vehicle is right at the door
        legs.append(_segment_leg(seg, start))
        start = legs[-1].arrival
    return legs


def _segments_backward(segments: list[Segment], end: datetime) -> list[Leg]:
    total = sum(s.minutes for s in segments if not (s.mode == "walk" and s.km < 0.02))
    return _segments_forward(segments, end - timedelta(minutes=total))


def _ride_leg(ctx: PlanningContext, ride: Ride) -> Leg:
    net = ctx.network
    conns = net.trip_conns[ride.trip]
    seq = conns[conns.index(ride.board_conn) : conns.index(ride.alight_conn) + 1]
    calls = [(net.l_dep_stop[seq[0]], net.l_dep[seq[0]])]
    calls += [(net.l_arr_stop[c], net.l_arr[c]) for c in seq]
    stops = []
    for stop, t in calls:
        lat, lon = net.stop_coords(stop)
        stops.append(StopCall(net.stop_ids[stop], net.stop_names[stop], lat, lon, ctx.at(t)))
    km = sum(
        haversine_distance(a.lat, a.lon, b.lat, b.lon)
        for a, b in zip(stops, stops[1:], strict=False)
    )
    route = int(net.trip_route[ride.trip])
    delay = net.trip_delay.get(ride.trip)
    return Leg(
        mode=net.route_mode[route],
        from_name=stops[0].name,
        to_name=stops[-1].name,
        from_lat=stops[0].lat,
        from_lon=stops[0].lon,
        to_lat=stops[-1].lat,
        to_lon=stops[-1].lon,
        departure=ctx.at(net.l_dep[ride.board_conn]),
        arrival=ctx.at(net.l_arr[ride.alight_conn]),
        distance_km=round(km, 2),
        line_name=net.route_short[route],
        line_color=net.route_color[route],
        route_id=net.route_ids[route],
        trip_id=net.trip_ids[ride.trip],
        headsign=net.trip_headsign[ride.trip],
        delay_min=None if delay is None else round(delay / 60),
        on_request=ride.trip in net.trip_on_request,
        provider=net.route_agency[route] if ride.trip in net.trip_on_request else None,
        stops=stops,
        path=[(s.lat, s.lon) for s in stops],
    )


def _transfer_leg(ctx: PlanningContext, transfer: Transfer, start: datetime) -> Leg:
    a, b = _stop_point(ctx.network, transfer.from_stop), _stop_point(ctx.network, transfer.to_stop)
    seg = _walk(a, b)
    seg.minutes = transfer.seconds / 60
    return _segment_leg(seg, start)


def build_plan(
    ctx: PlanningContext,
    journey: Journey,
    access: list[Segment],
    egress: list[Segment],
    earliest: datetime,
) -> Plan:
    # Leading walks between stops are timed backwards from the first boarding
    first_ride = next(i for i, p in enumerate(journey.parts) if isinstance(p, Ride))
    lead: list[Leg] = []
    t = ctx.at(ctx.network.l_dep[journey.parts[first_ride].board_conn])  # type: ignore[union-attr]
    for part in reversed(journey.parts[:first_ride]):
        assert isinstance(part, Transfer)
        t -= timedelta(seconds=part.seconds)
        lead.insert(0, _transfer_leg(ctx, part, t))

    middle: list[Leg] = []
    for part in journey.parts[first_ride:]:
        if isinstance(part, Ride):
            middle.append(_ride_leg(ctx, part))
        else:
            middle.append(_transfer_leg(ctx, part, middle[-1].arrival))

    board_at = (lead or middle)[0].departure
    # Arrive a minute early when there is time; never leave before the requested departure
    access_legs = _segments_backward(access, board_at - timedelta(minutes=1))
    if access_legs and access_legs[0].departure < earliest:
        access_legs = _segments_forward(access, earliest)
    egress_legs = _segments_forward(egress, middle[-1].arrival)
    return Plan(legs=access_legs + lead + middle + egress_legs)


def direct_plans(
    ctx: PlanningContext, origin: Point, destination: Point, when: datetime, modes: Iterable[str]
) -> list[Plan]:
    plans: list[Plan] = []
    km = haversine_distance(origin[1], origin[2], destination[1], destination[2])
    for mode in modes:
        if mode == "walk" and km <= MAX_DIRECT_WALK_KM:
            seg = _walk(origin, destination)
            plans.append(Plan(legs=_segments_forward([seg], when)))
        elif mode in ("bike", "scooter", "car_share"):
            profile = MODES[mode]
            found = ctx.gbfs.nearest_available(origin[1], origin[2], mode, MAX_WALK_TO_VEHICLE_KM)
            if found is None:
                continue
            spot, _ = found
            spot_point = (spot.name, spot.lat, spot.lon)
            ride_km = haversine_distance(spot.lat, spot.lon, destination[1], destination[2])
            if ride_km > profile.max_km:
                continue
            if mode != "car_share" and not ctx.gbfs.can_drop_off(spot, *destination[1:]):
                continue
            segs = [
                _walk(origin, spot_point),
                Segment(
                    mode,
                    spot_point,
                    destination,
                    profile.minutes(ride_km),
                    ride_km * profile.detour,
                    provider=spot.provider,
                    vehicles_available=spot.available.get(mode, 0),
                ),
            ]
            plans.append(Plan(legs=_segments_forward(segs, when)))
        elif mode == "on_demand":
            zone = zone_serving(ctx.zones, origin[1:], destination[1:])  # type: ignore[arg-type]
            if zone is None:
                continue
            wait = ctx.wait_model.predict(zone.id, when, zone.wait_min)
            minutes = wait + km * MODES["on_demand"].detour / zone.speed_kmh * 60
            seg = Segment(
                "on_demand",
                origin,
                destination,
                minutes,
                km * MODES["on_demand"].detour,
                provider=zone.provider,
                wait_min=wait,
            )
            plans.append(Plan(legs=_segments_forward([seg], when)))
    return plans


def _extra_minutes(leg: Leg) -> float:
    """Time on a leg that isn't travel: on-demand pickup wait, or unlocking a shared vehicle."""
    if leg.mode == "on_demand":
        return leg.wait_min
    profile = MODES.get(leg.mode)
    return profile.setup_min if profile and profile.shared else 0.0


def refine_with_streets(streets: StreetRouter | None, plans: list[Plan], earliest: datetime) -> int:
    """Re-time and redraw the walk/bike/car legs of final plans along real streets.

    The search uses straight-line estimates (fast, no requests); only the legs of
    the plans actually offered are routed. First-mile legs are re-timed backwards
    from the first boarding (never before `earliest`), last-mile legs forwards from
    the last alighting. Callers should re-run the optimisation check afterwards: a
    longer real walk can now miss its connection. Returns the number of legs routed.
    """
    if streets is None:
        return 0
    routed_count = 0
    for plan in plans:
        legs = plan.legs
        minutes: dict[int, float] = {}
        for i, leg in enumerate(legs):
            if leg.mode not in PROFILES or leg.distance_km < 0.05:
                continue
            routed = streets.route(leg.mode, (leg.from_lat, leg.from_lon), (leg.to_lat, leg.to_lon))
            if routed is None:
                continue
            street_min, km, geometry = routed
            minutes[i] = street_min + _extra_minutes(leg)
            leg.distance_km = round(km, 2)
            if len(geometry) > 1:
                leg.path = geometry
        if not minutes:
            continue
        routed_count += len(minutes)

        def duration(i: int) -> timedelta:
            return (
                timedelta(minutes=minutes[i])
                if i in minutes
                else legs[i].arrival - legs[i].departure
            )

        def forward(indices: range, t: datetime) -> None:
            for i in indices:
                legs[i].departure, legs[i].arrival = t, t + duration(i)
                t = legs[i].arrival

        transit = [i for i, leg in enumerate(legs) if leg.is_transit]
        if not transit:
            forward(range(len(legs)), legs[0].departure)
            continue
        first, last = transit[0], transit[-1]
        t = legs[first].departure - timedelta(minutes=1)  # Be at the stop a minute early
        for i in reversed(range(first)):
            legs[i].arrival, legs[i].departure = t, t - duration(i)
            t = legs[i].departure
        if first and legs[0].departure < earliest:
            forward(range(first), earliest)  # The check will flag a missed connection
        forward(range(last + 1, len(legs)), legs[last].arrival)
    return routed_count


def _mode_blocking(net: TransitNetwork, allowed_transit: set[str]) -> Blocking | None:
    """Block transit modes the traveller excluded (e.g. buses only).

    Trips booked by phone (call-ahead taxis) count as on-demand, whatever their route type.
    """
    blocked = [i for i, m in enumerate(net.route_mode) if m not in allowed_transit]
    mask = np.isin(net.c_route, blocked) if blocked else None
    if "on_demand" not in allowed_transit and net.trip_on_request:
        on_request = np.isin(net.c_trip, list(net.trip_on_request))
        mask = on_request if mask is None else mask | on_request
    if mask is None or not mask.any():
        return None
    return Blocking(conn_mask=mask)


def plan_candidates(
    ctx: PlanningContext,
    origin: Point,
    destination: Point,
    when: datetime,
    preferred_modes: list[str] | None = None,
    blocking: Blocking | None = None,
) -> list[Plan]:
    """All candidate chains for a trip, unscored. The optimisation check ranks them."""
    pref = set(preferred_modes or [])
    use = lambda m: m == "walk" or not pref or m in pref  # noqa: E731
    transit_pref = {m for m in pref if m in TRANSIT_MODES}
    use_transit = not pref or bool(transit_pref) or "transit" in pref

    plans = direct_plans(ctx, origin, destination, when, [m for m in ACCESS_MODES if use(m)])
    if not use_transit:
        return plans

    mode_block = None
    if transit_pref and "transit" not in pref:
        # GTFS on-demand routes (route_type 715) count as on-demand, not as transit
        allowed = transit_pref | ({"on_demand"} if "on_demand" in pref else set())
        mode_block = _mode_blocking(ctx.network, allowed)
    if mode_block is not None:
        blocking = mode_block if blocking is None else blocking.merged(mode_block)
    t0 = ctx.seconds(when)

    egress_by_mode = {m: last_mile(ctx, destination, m, when) for m in EGRESS_MODES if use(m)}
    found: list[
        tuple[
            Plan, dict[int, int], dict[int, int], dict[int, list[Segment]], dict[int, list[Segment]]
        ]
    ] = []
    for access_mode in (m for m in ACCESS_MODES if use(m)):
        access = first_mile(ctx, origin, access_mode, when)
        if not access:
            continue
        sources = {
            s: t0 + math.ceil(sum(seg.minutes for seg in segs) * 60) for s, segs in access.items()
        }
        for egress in egress_by_mode.values():
            if not egress:
                continue
            targets = {
                s: math.ceil(sum(seg.minutes for seg in segs) * 60) for s, segs in egress.items()
            }
            journey = earliest_arrival(ctx.network, sources, targets, blocking)
            if journey is None:
                continue
            plan = build_plan(
                ctx, journey, access[journey.access_stop], egress[journey.egress_stop], when
            )
            found.append((plan, sources, targets, access, egress))

    # The next departures of the fastest chain ("later" options), then one alternative that
    # avoids its vehicles, so there is a fallback to compare
    if found:
        best = min(found, key=lambda f: f[0].arrival)
        plan, sources, targets, access, egress = best
        later = plan
        trip_index = {tid: i for i, tid in enumerate(ctx.network.trip_ids)}
        offered: set[int] = set()
        for _ in range(LATER_DEPARTURES):
            boarding = next((leg.departure for leg in later.legs if leg.is_transit), None)
            if boarding is None:
                break
            # Leave after the previous option, and not on a vehicle already offered (the same
            # bus can often still be caught one stop further on)
            offered |= {trip_index[leg.trip_id] for leg in later.legs if leg.trip_id}
            skip = Blocking(conn_mask=np.isin(ctx.network.c_trip, sorted(offered)))
            after = ctx.seconds(boarding) + 60
            journey = earliest_arrival(
                ctx.network,
                {s: max(t, after) for s, t in sources.items()},
                targets,
                skip if blocking is None else blocking.merged(skip),
            )
            if journey is None:
                break
            later = build_plan(
                ctx, journey, access[journey.access_stop], egress[journey.egress_stop], when
            )
            found.append((later, sources, targets, access, egress))
        used = [trip_index[leg.trip_id] for leg in plan.legs if leg.trip_id]
        avoid = Blocking(conn_mask=np.isin(ctx.network.c_trip, used))
        journey = earliest_arrival(
            ctx.network, sources, targets, avoid if blocking is None else blocking.merged(avoid)
        )
        if journey is not None:
            found.append(
                (
                    build_plan(
                        ctx,
                        journey,
                        access[journey.access_stop],
                        egress[journey.egress_stop],
                        when,
                    ),
                    sources,
                    targets,
                    access,
                    egress,
                )
            )

    plans.extend(f[0] for f in found)
    unique: dict[tuple[str, ...], Plan] = {}
    for plan in plans:
        key = plan.signature
        if key not in unique or plan.arrival < unique[key].arrival:
            unique[key] = plan
    return list(unique.values())


ARRIVE_BY_WINDOW = timedelta(hours=4)
ARRIVE_BY_STEP = timedelta(minutes=1)


def plan_arrive_by(
    ctx: PlanningContext,
    origin: Point,
    destination: Point,
    deadline: datetime,
    preferred_modes: list[str] | None = None,
    blocking: Blocking | None = None,
    earliest: datetime | None = None,
) -> list[Plan]:
    """Chains that arrive by `deadline`, leaving as late as possible (never before `earliest`).

    Binary-searches the departure time with the forward planner instead of a separate
    reverse search, so every mode and check behaves exactly as in a normal plan. This
    relies on the timetable being FIFO (leaving later never arrives earlier), which holds
    for scheduled transit. Every on-time chain seen while searching is a candidate.
    """
    on_time: dict[tuple[str, ...], Plan] = {}

    def probe(leave: datetime) -> bool:
        found = False
        for plan in plan_candidates(ctx, origin, destination, leave, preferred_modes, blocking):
            if plan.arrival <= deadline:
                found = True
                key = plan.signature
                if key not in on_time or plan.departure > on_time[key].departure:
                    on_time[key] = plan
        return found

    lo, hi = deadline - ARRIVE_BY_WINDOW, deadline
    if earliest is not None:
        lo = max(lo, earliest)
    if lo >= hi:
        return []
    if not probe(lo):
        return []
    while hi - lo > ARRIVE_BY_STEP:
        mid = lo + (hi - lo) / 2
        if probe(mid):
            lo = mid
        else:
            hi = mid
    return list(on_time.values())
