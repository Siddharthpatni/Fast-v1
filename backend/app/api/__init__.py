"""API route handlers for FastMile.

Organized by domain: system, routing, disruptions, network.
"""

import json
import logging
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.i18n import tr
from app.models import Disruption
from app.ratelimit import rate_limit
from app.schemas import (
    DepartureOut,
    DeparturesResponse,
    DisruptionCreate,
    DisruptionOut,
    DisruptionUpdate,
    HealthResponse,
    IssueOut,
    NetworkStatus,
    Position,
    ReplanRequest,
    ReplanResponse,
    RerouteDecision,
    RouteRequest,
    RouteResponse,
    StopOut,
    StopRef,
)
from app.services.decisions import (
    JevError,
    decide_reroute_strategy,
    get_jev_client,
)
from app.services.departures import mark_disruptions, network_departures
from app.services.disruptions import (
    attach_stop_points,
    blocking_for,
    impact_from_row,
    match_lines,
    match_stops,
    rate_severity,
    window,
)
from app.services.geocoding import dedupe, search_stops
from app.services.llm import extract_disruption
from app.services.optimisation import (
    CheckContext,
    DisruptionImpact,
    check_plan,
    label_plans,
    rank_plans,
)
from app.services.optimisation.jev_review import review_transfers
from app.services.replanning import locate, option_to_plan, plan_to_option
from app.services.routing import Plan, plan_candidates
from app.services.routing.modes import ACCESS_MODES
from app.services.routing.planner import plan_arrive_by, refine_with_streets
from app.services.routing.transitous import TransitousError
from app.state import state

logger = logging.getLogger(__name__)

router = APIRouter()

JEV_ACT_CONFIDENCE = 0.6  # Below this, Jev's reroute advice is shown but not acted on


def _local(dt: datetime | None) -> datetime:
    """Interpret naive datetimes as local time; default to now."""
    if dt is None:
        return datetime.now(state.tz)
    return dt.replace(tzinfo=state.tz) if dt.tzinfo is None else dt.astimezone(state.tz)


def _naive_local(dt: datetime | None) -> datetime | None:
    return None if dt is None else dt.astimezone(state.tz).replace(tzinfo=None)


async def _active_impacts(db: AsyncSession, at: datetime) -> list[DisruptionImpact]:
    """Unresolved disruptions not over by `at`. Routing works without them if the DB is down."""
    try:
        result = await db.execute(select(Disruption).where(Disruption.status == "active"))
        impacts = [impact_from_row(row, state.tz) for row in result.scalars().all()]
        return [d for d in impacts if d.end is None or d.end >= at]
    except (SQLAlchemyError, OSError) as exc:
        logger.warning("Could not load disruptions, planning without them: %s", exc)
        return []


# ── Health ──


@router.get("/health", response_model=HealthResponse, tags=["system"])
async def health_check():
    """Health check endpoint — returns service status."""
    return HealthResponse(
        jev_enabled=bool(settings.jev_api_key),
        llm=state.llm.describe() if state.llm else None,
        network_loaded=state.feed is not None,
        data_attribution=state.feed.attribution if state.feed else None,
        coverage=list(state.coverage) if state.coverage else None,
        germany_routing=state.transitous is not None,
    )


@router.get("/network/status", response_model=NetworkStatus, tags=["system"])
async def network_status():
    """What data and models are loaded right now."""
    net = state.network_for(datetime.now(state.tz).date())
    rt = state.realtime
    return NetworkStatus(
        gtfs_source=state.feed.source if state.feed else None,
        gtfs_error=state.feed_error,
        service_date=net.service_date.isoformat() if net else None,
        stops=len(net.stop_ids) if net else 0,
        routes=len(net.route_ids) if net else 0,
        trips_today=len(net.trip_ids) if net else 0,
        connections_today=net.n_connections if net else 0,
        shared_vehicles=state.gbfs.count_by_mode(),
        gbfs_sources=state.gbfs.sources,
        gbfs_errors=state.gbfs.errors,
        on_demand_zones=[z.name for z in state.zones],
        realtime=None
        if rt is None
        else {
            "delayed_trips": len(rt.delays),
            "canceled_trips": len(rt.canceled),
            "error": rt.error,
            "alerts": vars(state.alert_sync) if state.alert_sync else None,
            "alert_sync_error": state.alert_sync_error,
        },
        models={
            "capacity": state.capacity.describe(),
            "on_demand_wait": state.wait_model.describe(),
        },
        llm=state.llm.describe() if state.llm else None,
        jev_enabled=bool(settings.jev_api_key),
        street_routing=state.streets.describe() if state.streets else None,
        germany_routing=state.transitous.describe() if state.transitous else None,
    )


# ── Routes / Routing ──


async def _plan(
    db: AsyncSession,
    origin: tuple[str, float, float],
    destination: tuple[str, float, float],
    when: datetime,
    preferred_modes: list[str] | None,
    arrive_by: datetime | None = None,
    limit: int = 5,
    lang: str = "en",
) -> tuple[list[Plan], list[DisruptionImpact]]:
    """Plan leaving at `when`, or, with `arrive_by`, leaving as late as possible to make it.

    Trips with both ends inside the loaded feed's region use FastMile's own router (with
    disruption blocking and street refinement); any other trip in Germany goes to
    Transitous. Both pass the same optimisation check and Jev review.
    """
    day = (arrive_by or when).date()
    # Building a day's network can take seconds on a real feed; keep it off the event loop
    net = await run_in_threadpool(state.network_for, day) if state.feed is not None else None
    impacts = await _active_impacts(db, when)
    if net is not None:
        attach_stop_points(impacts, net)
    check = CheckContext(
        capacity=state.capacity,
        disruptions=impacts,
        arrive_by=arrive_by,
        region=state.coverage,
        lang=lang,
    )
    local = net is not None and state.covers(*origin[1:]) and state.covers(*destination[1:])

    if local:
        blocking = blocking_for(net, impacts, state.tz) if impacts else None
        ctx = state.planning_context(net)
        if arrive_by is not None:
            candidates = await run_in_threadpool(
                plan_arrive_by, ctx, origin, destination, arrive_by, preferred_modes, blocking, when
            )
        else:
            candidates = await run_in_threadpool(
                plan_candidates, ctx, origin, destination, when, preferred_modes, blocking
            )
        ranked = rank_plans(candidates, check, limit=limit)
        # Real street times and lines for the offered plans, then re-check: a longer real
        # walk can miss its connection
        if await run_in_threadpool(refine_with_streets, state.streets, ranked, when):
            for plan in ranked:
                check_plan(plan, check)
            label_plans(ranked, arrive_by=arrive_by is not None)
    elif state.transitous is not None:
        try:
            # Extra results: some may be dropped by the check (e.g. a closed line)
            candidates = await state.transitous.plan(
                origin, destination, when, arrive_by, preferred_modes, results=limit + 3
            )
        except TransitousError as exc:
            raise HTTPException(
                503,
                tr(
                    lang,
                    str(exc),
                    "Der deutschlandweite Fahrplan (Transitous) ist nicht erreichbar",
                ),
            ) from exc
        ranked = rank_plans(candidates, check, limit=limit)
    elif net is None:
        raise HTTPException(
            503, f"No transit network loaded: {state.feed_error or 'set GTFS_PATH'}"
        )
    else:
        raise HTTPException(
            422,
            tr(
                lang,
                "This trip leaves the region FastMile has timetables for, and Germany-wide "
                "routing (TRANSITOUS_URL) is switched off.",
                "Diese Fahrt verlässt das Gebiet mit FastMile-Fahrplänen, und die "
                "deutschlandweite Suche (TRANSITOUS_URL) ist ausgeschaltet.",
            ),
        )

    jev = get_jev_client()
    if jev is not None:
        # One batched call; skipped on any Jev error
        await review_transfers(jev, ranked, arrive_by=arrive_by is not None, lang=lang)
    return ranked, impacts


@router.post(
    "/routes/plan",
    response_model=RouteResponse,
    tags=["routing"],
    dependencies=[Depends(rate_limit("plans", "rate_limit_plans_per_min"))],
)
async def plan_route(request: RouteRequest, db: AsyncSession = Depends(get_db)):
    """Plan door-to-door chains across transit, sharing and on-demand, checked and ranked.

    Give `arrive_by` without `departure_time` to plan backwards from a deadline.
    """
    when = _local(request.departure_time)
    plans, impacts = await _plan(
        db,
        ("Start", request.origin_lat, request.origin_lon),
        ("Destination", request.destination_lat, request.destination_lon),
        when,
        request.preferred_modes,
        _local(request.arrive_by) if request.arrive_by else None,
        request.max_results,
        request.lang,
    )
    lang = request.lang
    message = None
    if not plans and request.arrive_by is not None:
        message = tr(
            lang,
            "No connection arrives in time. Try a later arrival or allow more modes.",
            "Keine Verbindung kommt rechtzeitig an. "
            "Später ankommen oder mehr Verkehrsmittel erlauben.",
        )
    elif not plans:
        message = tr(
            lang,
            "No connection found. Try a later departure or allow more modes.",
            "Keine Verbindung gefunden. Später losfahren oder mehr Verkehrsmittel erlauben.",
        )
    elif not any(p.feasible for p in plans):
        message = tr(
            lang,
            "No option passes every check; showing the best available.",
            "Keine Verbindung besteht jede Prüfung; hier die besten.",
        )
    return RouteResponse(
        routes=[plan_to_option(p, i + 1) for i, p in enumerate(plans)],
        computed_at=datetime.now(state.tz),
        disruptions_considered=[d.id for d in impacts],
        message=message,
    )


@router.post(
    "/routes/replan",
    response_model=ReplanResponse,
    tags=["routing"],
    dependencies=[Depends(rate_limit("plans", "rate_limit_plans_per_min"))],
)
async def replan_route(request: ReplanRequest, db: AsyncSession = Depends(get_db)):
    """Re-check an active trip against disruptions and replan from where the traveller is."""
    at = _local(request.at_time)
    original = option_to_plan(request.route)
    located = locate(original, at)
    last = original.legs[-1]
    if located is None:
        return ReplanResponse(
            position=Position(name=last.to_name, lat=last.to_lat, lon=last.to_lon, time=at),
            arrived=True,
            original_still_valid=True,
            computed_at=datetime.now(state.tz),
        )
    (name, lat, lon, pos_time), remaining = located

    impacts = await _active_impacts(db, at)
    remaining_plan = check_plan(
        Plan(legs=remaining),
        CheckContext(
            capacity=state.capacity, disruptions=impacts, region=state.coverage, lang=request.lang
        ),
    )
    blocking_issues = [i for i in remaining_plan.issues if i.severity == "error"]

    decision = None
    jev = get_jev_client()
    if jev is not None and blocking_issues:
        try:
            d = await decide_reroute_strategy(
                jev,
                current_route_legs=[
                    {
                        "mode": leg.mode,
                        "line": leg.line_name,
                        "from": leg.from_name,
                        "to": leg.to_name,
                        "departure": leg.departure.strftime("%H:%M"),
                    }
                    for leg in remaining
                ],
                disruption={"issues": [i.message for i in blocking_issues]},
                alternative_modes=request.preferred_modes or ACCESS_MODES,
                passenger_location={"lat": lat, "lon": lon},
            )
            strategy, mode = d.get_answer("strategy"), d.get_answer("preferred_alternative_mode")
            decision = RerouteDecision(
                strategy=d.get_choice("strategy"),
                strategy_confidence=strategy.confidence if strategy else None,
                urgency=d.get_score("urgency"),
                preferred_alternative_mode=d.get_choice("preferred_alternative_mode"),
                mode_confidence=mode.confidence if mode else None,
                model=d.model,
            )
        except JevError as exc:
            logger.warning("Jev reroute decision failed: %s", exc)

    routes = []
    if blocking_issues:
        plans, _ = await _plan(
            db,
            (name, lat, lon),
            (last.to_name, last.to_lat, last.to_lon),
            max(pos_time, at),
            request.preferred_modes,
            lang=request.lang,
        )
        # Act on Jev's call only when it is confident: lead with its preferred alternative mode
        if (
            decision
            and decision.preferred_alternative_mode
            and (decision.mode_confidence or 0) >= JEV_ACT_CONFIDENCE
            and decision.strategy in ("switch_mode", "skip_segment", "full_reroute")
        ):
            preferred = decision.preferred_alternative_mode
            plans.sort(key=lambda p: preferred not in {leg.mode for leg in p.legs})
            decision.applied = any(preferred in {leg.mode for leg in p.legs} for p in plans)
        routes = [plan_to_option(p, i + 1) for i, p in enumerate(plans)]

    return ReplanResponse(
        position=Position(name=name, lat=lat, lon=lon, time=pos_time),
        original_still_valid=not blocking_issues,
        original_issues=[
            IssueOut(severity=i.severity, message=i.message, leg_index=i.leg_index)
            for i in remaining_plan.issues
        ],
        decision=decision,
        routes=routes,
        computed_at=datetime.now(state.tz),
    )


@router.get(
    "/departures",
    response_model=DeparturesResponse,
    tags=["routing"],
    dependencies=[Depends(rate_limit("plans", "rate_limit_plans_per_min"))],
)
async def departures(
    lat: float,
    lon: float,
    n: int = Query(12, ge=1, le=30),
    lang: Literal["en", "de"] = "en",
    db: AsyncSession = Depends(get_db),
):
    """Next departures at the stop nearest a point, with live delays and active disruptions."""
    now = datetime.now(state.tz)
    impacts = await _active_impacts(db, now)
    in_region = state.covers(lat, lon)
    found, source = None, "fastmile"
    if in_region and state.feed is not None:
        net = await run_in_threadpool(state.network_for, now.date())
        if net is not None:
            midnight = state.planning_context(net).midnight()
            found = network_departures(net, midnight, lat, lon, now, impacts, n, lang)
    if found is None and state.transitous is not None:
        source = "transitous"
        try:
            found = await state.transitous.departures(lat, lon, state.tz, n)
        except TransitousError as exc:
            raise HTTPException(503, str(exc)) from exc
        if found is not None:
            mark_disruptions(found[1], impacts, in_region)
    if found is None:
        raise HTTPException(
            404, tr(lang, "No stop near this place", "Keine Haltestelle in der Nähe")
        )
    stop, deps = found
    return DeparturesResponse(
        stop=StopRef(**stop),
        source=source,
        departures=[DepartureOut(**d.as_dict()) for d in deps],
        computed_at=now,
    )


@router.get("/legal", tags=["system"])
async def legal():
    """Who runs this site (from OPERATOR_* settings) and which outside services get data."""
    operator = None
    if settings.operator_name:
        operator = {
            "name": settings.operator_name,
            "address": settings.operator_address.replace("\\n", "\n"),
            "email": settings.operator_email,
            "phone": settings.operator_phone,
        }
    services = ["photon", "osrm"]
    if state.transitous is not None:
        services.append("transitous")
    if state.llm is not None:
        services.append("openrouter")
    if settings.jev_api_key:
        services.append("typesafe")
    return {"operator": operator, "services": services}


# ── Disruptions ──


@router.post(
    "/disruptions",
    response_model=DisruptionOut,
    tags=["disruptions"],
    dependencies=[Depends(rate_limit("reports", "rate_limit_reports_per_min"))],
)
async def create_disruption(
    payload: DisruptionCreate,
    db: AsyncSession = Depends(get_db),
):
    """Read a free-text disruption report and store it as a structured, routable event.

    1. The LLM (or the rule-based fallback) extracts lines, stops, time window and effect.
    2. Names are matched to the loaded network so routing can block them.
    3. Jev classifies severity and whether travellers must be rerouted (when configured).
    """
    reported_at = _local(payload.reported_at)
    net = await run_in_threadpool(state.network_for, reported_at.date())
    extraction = await extract_disruption(
        state.llm, payload.raw_text, net.stop_names if net else None
    )
    route_ids = match_lines(net, extraction.affected_lines) if net else []
    stop_ids = match_stops(net, extraction.affected_stops) if net else []
    start, end = window(reported_at, extraction.start_time, extraction.end_time)

    severity, confidence, needs_reroute = await rate_severity(
        payload.raw_text, extraction.affected_lines, extraction.effect, extraction.delay_minutes
    )

    disruption = Disruption(
        raw_text=payload.raw_text,
        source=payload.source,
        status="active",
        summary=extraction.summary,
        severity=severity,
        confidence=confidence,
        needs_reroute=needs_reroute,
        effect=extraction.effect,
        disruption_type=extraction.disruption_type,
        delay_minutes=extraction.delay_minutes,
        extraction_method=extraction.method,
        affected_lines=json.dumps(extraction.affected_lines, ensure_ascii=False),
        affected_routes=json.dumps(route_ids),
        affected_stops=json.dumps(stop_ids),
        alternatives=json.dumps(extraction.alternatives, ensure_ascii=False),
        alternative_lines=json.dumps(extraction.alternative_lines, ensure_ascii=False),
        start_time=_naive_local(start),
        end_time=_naive_local(end),
    )
    db.add(disruption)
    await db.flush()
    await db.refresh(disruption)
    return disruption


@router.get("/disruptions", response_model=list[DisruptionOut], tags=["disruptions"])
async def list_disruptions(
    status: str | None = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
):
    """List disruption events by status; "active" leaves out ones already over."""
    query = select(Disruption).order_by(Disruption.created_at.desc()).limit(min(max(limit, 1), 200))
    if status:
        query = query.where(Disruption.status == status)
    if status == "active":
        now = _naive_local(datetime.now(state.tz))
        query = query.where(or_(Disruption.end_time.is_(None), Disruption.end_time >= now))
    result = await db.execute(query)
    return result.scalars().all()


@router.patch("/disruptions/{disruption_id}", response_model=DisruptionOut, tags=["disruptions"])
async def update_disruption(
    disruption_id: int,
    payload: DisruptionUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Resolve (or reactivate) a disruption."""
    disruption = await db.get(Disruption, disruption_id)
    if disruption is None:
        raise HTTPException(404, f"Disruption {disruption_id} not found")
    disruption.status = payload.status
    if payload.status == "resolved" and disruption.end_time is None:
        disruption.end_time = _naive_local(datetime.now(state.tz))
    await db.flush()
    await db.refresh(disruption)
    return disruption


# ── Network ──


@router.get("/stops", response_model=list[StopOut], tags=["network"])
async def list_stops(
    lat: float | None = None,
    lon: float | None = None,
    radius_km: float = 1.0,
    q: str | None = None,
    limit: int = 50,
):
    """Stops near a point and/or matching a name."""
    net = state.network_for(datetime.now(state.tz).date())
    if net is None:
        raise HTTPException(503, "No transit network loaded")
    if lat is not None and lon is not None:
        candidates = net.nearby_stops(lat, lon, min(radius_km, 20.0))
    else:
        candidates = [(i, None) for i in range(len(net.stop_ids))]  # type: ignore[misc]
    if q:
        needle = q.lower()
        candidates = [(i, d) for i, d in candidates if needle in net.stop_names[i].lower()]
    return [
        StopOut(
            stop_id=net.stop_ids[i],
            name=net.stop_names[i],
            lat=float(net.stop_lats[i]),
            lon=float(net.stop_lons[i]),
            departures_today=int(net.stop_departures[i]),
            distance_km=None if d is None else round(d, 3),
        )
        for i, d in candidates[: min(max(limit, 1), 500)]
    ]


@router.get(
    "/geocode",
    tags=["network"],
    dependencies=[Depends(rate_limit("geocode", "rate_limit_geocode_per_min"))],
)
async def geocode(q: str, limit: int = 6):
    """Search places by name: matching transit stops first, then addresses and POIs."""
    q = q.strip()
    if len(q) < 3:
        return {"results": []}
    net = await run_in_threadpool(state.network_for, datetime.now(state.tz).date())
    stops = search_stops(net, q)
    places = await state.geocoder.search(q, min(max(limit, 1), 10)) if state.geocoder else []
    # A region stop found by both searches is listed once, as the region's stop
    results = dedupe(stops + places)[: max(limit, len(stops))]
    return {"results": [p.as_dict() for p in results]}


@router.get("/providers", tags=["providers"])
async def list_providers():
    """Transport providers and whether their data is loaded."""
    by_mode = state.gbfs.count_by_mode()
    net = state.network_for(datetime.now(state.tz).date())
    transit_modes = sorted(set(net.route_mode)) if net else ["bus", "tram", "train"]
    rt = state.realtime
    return {
        "providers": [
            {
                "name": "Public Transport (GTFS)",
                "status": "live" if net else "unavailable",
                "modes": transit_modes,
                "detail": state.feed.source if state.feed else state.feed_error,
            },
            {
                "name": "Realtime (GTFS-RT)",
                "status": "live" if rt and not rt.error else ("error" if rt else "not configured"),
                "modes": transit_modes,
                "detail": rt.error if rt else None,
            },
            {
                "name": "Micromobility (GBFS)",
                "status": "live"
                if (by_mode.get("bike") or by_mode.get("scooter"))
                else "no vehicles",
                "modes": ["bike", "scooter"],
                "detail": f"{by_mode.get('bike', 0)} bikes, {by_mode.get('scooter', 0)} scooters",
            },
            {
                "name": "On-Demand Shuttle",
                "status": "live" if state.zones else "not configured",
                "modes": ["on_demand"],
                "detail": ", ".join(z.name for z in state.zones) or None,
            },
            {
                "name": "Car-Sharing (GBFS)",
                "status": "live" if by_mode.get("car_share") else "no vehicles",
                "modes": ["car_share"],
                "detail": f"{by_mode.get('car_share', 0)} cars",
            },
        ],
        "vehicles_updated": (
            datetime.fromtimestamp(state.gbfs.fetched_at, state.tz).isoformat()
            if state.gbfs.fetched_at
            else None
        ),
    }
