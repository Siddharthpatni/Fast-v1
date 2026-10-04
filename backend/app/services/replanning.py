"""Conversions between planner objects and API schemas, and on-the-go replanning.

Replanning (brief, "Incident handling"): the traveller is already under way
when a report arrives. We locate them on their chain at the current time, check
the remaining chain against active disruptions, and if it no longer works,
plan new candidates from their position — which go through the same
optimisation check as any regular plan.
"""

from datetime import datetime

from app.schemas import IssueOut, RouteLeg, RouteOption, StopCallOut
from app.services.routing.types import Leg, Plan, StopCall

MODE_VERBS = {
    "walk": "Walk",
    "bike": "Cycle",
    "scooter": "Ride a scooter",
    "car_share": "Drive a shared car",
    "on_demand": "Take the on-demand shuttle",
}


def leg_instructions(leg: Leg) -> str:
    if leg.is_transit:
        towards = f" towards {leg.headsign}" if leg.headsign else ""
        platform = f" from platform {leg.platform}" if leg.platform else ""
        return (
            f"Take {leg.mode} {leg.line_name}{towards} at {leg.departure:%H:%M}{platform}, "
            f"get off at {leg.to_name} ({len(leg.stops) - 1} stops)"
        )
    verb = MODE_VERBS.get(leg.mode, leg.mode.title())
    extra = ""
    if leg.mode == "on_demand" and leg.wait_min:
        extra = f" (book now, pickup in about {leg.wait_min:.0f} min)"
    elif leg.mode in ("bike", "scooter", "car_share") and leg.provider:
        extra = f" ({leg.provider}, pick up at {leg.from_name})"
    return f"{verb} to {leg.to_name}{extra}"


def plan_to_option(plan: Plan, rank: int) -> RouteOption:
    return RouteOption(
        rank=rank,
        label=plan.label,
        departure_time=plan.departure,
        arrival_time=plan.arrival,
        total_duration_minutes=round(plan.duration_min, 1),
        total_distance_km=round(plan.distance_km, 2),
        transfers=plan.transfers,
        reliability_score=plan.reliability,
        feasible=plan.feasible,
        cost=plan.cost,
        jev_checked_transfers=sum(c.jev_probability is not None for c in plan.transfer_checks),
        source=plan.source,
        issues=[
            IssueOut(severity=i.severity, message=i.message, leg_index=i.leg_index)
            for i in plan.issues
        ],
        legs=[
            RouteLeg(
                mode=leg.mode,
                from_name=leg.from_name,
                to_name=leg.to_name,
                from_lat=leg.from_lat,
                from_lon=leg.from_lon,
                to_lat=leg.to_lat,
                to_lon=leg.to_lon,
                departure_time=leg.departure,
                arrival_time=leg.arrival,
                duration_minutes=round(leg.duration_min, 1),
                distance_km=leg.distance_km,
                line_name=leg.line_name,
                line_color=leg.line_color,
                route_id=leg.route_id,
                trip_id=leg.trip_id,
                headsign=leg.headsign,
                provider=leg.provider,
                vehicles_available=leg.vehicles_available,
                wait_minutes=leg.wait_min,
                occupancy=leg.occupancy,
                delay_minutes=leg.delay_min,
                platform=leg.platform,
                cancelled=leg.cancelled,
                on_request=leg.on_request,
                source=leg.source,
                instructions=leg_instructions(leg),
                stops=[
                    StopCallOut(stop_id=s.stop_id, name=s.name, lat=s.lat, lon=s.lon, time=s.time)
                    for s in leg.stops
                ],
                path=leg.path,
            )
            for leg in plan.legs
        ],
    )


def option_to_plan(option: RouteOption) -> Plan:
    return Plan(
        legs=[
            Leg(
                mode=leg.mode,
                from_name=leg.from_name,
                to_name=leg.to_name,
                from_lat=leg.from_lat,
                from_lon=leg.from_lon,
                to_lat=leg.to_lat,
                to_lon=leg.to_lon,
                departure=leg.departure_time,
                arrival=leg.arrival_time,
                distance_km=leg.distance_km,
                line_name=leg.line_name,
                line_color=leg.line_color,
                route_id=leg.route_id,
                trip_id=leg.trip_id,
                headsign=leg.headsign,
                provider=leg.provider,
                vehicles_available=leg.vehicles_available,
                wait_min=leg.wait_minutes,
                delay_min=leg.delay_minutes,
                platform=leg.platform,
                cancelled=leg.cancelled,
                on_request=leg.on_request,
                source=leg.source,
                stops=[StopCall(s.stop_id, s.name, s.lat, s.lon, s.time) for s in leg.stops],
                path=list(leg.path),
            )
            for leg in option.legs
        ],
        source=option.source,
    )


def locate(plan: Plan, at: datetime) -> tuple[tuple[str, float, float, datetime], list[Leg]] | None:
    """Where the traveller is at `at`, and the legs still ahead. None if already arrived.

    On a vehicle, the position is the next stop (the earliest place they can get off).
    Known limitation: someone mid-way on a shared bike or car is replanned as if on foot
    at the interpolated point; the planner does not yet know they already hold a vehicle.
    """
    for i, leg in enumerate(plan.legs):
        if at < leg.departure:
            return (leg.from_name, leg.from_lat, leg.from_lon, at), plan.legs[i:]
        if at < leg.arrival:
            if leg.is_transit and leg.stops:
                nxt = next(s for s in leg.stops[1:] if s.time >= at)
                return (nxt.name, nxt.lat, nxt.lon, nxt.time), plan.legs[i:]
            frac = (at - leg.departure) / (leg.arrival - leg.departure)
            lat = leg.from_lat + frac * (leg.to_lat - leg.from_lat)
            lon = leg.from_lon + frac * (leg.to_lon - leg.from_lon)
            return (f"On the way to {leg.to_name}", lat, lon, at), plan.legs[i:]
    return None
