"""Optimisation check: test each candidate chain against vehicles, capacity and time.

From the brief: "Tests each candidate against vehicles, capacity and time."
Every candidate — from the regular planner or from disruption replanning —
passes through here before it is offered. The check:

- vehicles: a shared bike/scooter/car must be available at the pickup spot
- capacity: predicted occupancy of each transit leg (CapacityModel)
- time:     transfer slack between legs, and an optional arrive-by deadline
- disruptions: no leg may use a line or stop blocked by an active disruption
  (Transitous legs come from another feed, so they match by line name inside the
  loaded region, and by stop position)
- report proposals: a chain riding a line the language model read as the report's
  recommended alternative is noted and preferred (REPORT_ALTERNATIVE_BONUS_MIN)

It sets `feasible`, a `reliability` score (probability the chain works as
planned) and human-readable issues, then ranks the feasible plans by a
generalized cost.
"""

import math
from dataclasses import dataclass, field
from datetime import datetime

from app.i18n import tr, vehicle
from app.services.ml.models import CapacityModel
from app.services.routing.modes import MODES
from app.services.routing.network import normalize_line
from app.services.routing.types import Issue, Leg, Plan, TransferCheck
from app.utils.geo import haversine_distance

FULL_OCCUPANCY = 0.97  # Treat as "you probably won't get on"
CROWDED_OCCUPANCY = 0.8
TRANSFER_PENALTY_MIN = 4.0
UNRELIABILITY_PENALTY_MIN = 25.0  # Minutes of cost for a chain that certainly fails
DEFAULT_DELAY_MIN = 5.0  # Assumed when a report says "delayed" without a number
SAME_STOP_KM = 0.15  # A stop from another feed this close to a blocked stop is the same stop
REPORT_ALTERNATIVE_BONUS_MIN = 5.0  # Preference for the line a disruption report recommends


@dataclass
class DisruptionImpact:
    """What an active disruption blocks, already matched to network IDs."""

    id: int
    summary: str
    severity: str | None
    route_ids: set[str] = field(default_factory=set)
    stop_ids: set[str] = field(default_factory=set)
    start: datetime | None = None
    end: datetime | None = None
    effect: str = "other"
    delay_minutes: int | None = None
    lines: set[str] = field(default_factory=set)  # Normalized line names, e.g. {"830", "RB82"}
    stop_points: list[tuple[float, float]] = field(default_factory=list)  # Coordinates of stop_ids
    recommended_lines: set[str] = field(default_factory=set)  # Normalized; the report's proposal

    # Blocking policy: a delay or a sharing-fleet outage never blocks transit; a suspended line
    # blocks the line (its stops only if no line could be matched, e.g. a closed station node).
    @property
    def blocked_routes(self) -> set[str]:
        return self.route_ids if self.effect in ("suspended", "other") else set()

    @property
    def blocked_stops(self) -> set[str]:
        if self.effect in ("stops_not_served", "station_closed"):
            return self.stop_ids
        if self.effect in ("suspended", "other") and not self.route_ids:
            return self.stop_ids
        return set()

    @property
    def delayed_routes(self) -> set[str]:
        return self.route_ids if self.effect == "delayed" else set()

    @property
    def blocked_points(self) -> list[tuple[float, float]]:
        return self.stop_points if self.blocked_stops else []

    def active_between(self, a: datetime, b: datetime) -> bool:
        return (self.start is None or self.start <= b) and (self.end is None or self.end >= a)


@dataclass
class CheckContext:
    capacity: CapacityModel = field(default_factory=CapacityModel)
    disruptions: list[DisruptionImpact] = field(default_factory=list)
    arrive_by: datetime | None = None
    # The loaded feed's (south, west, north, east)
    region: tuple[float, float, float, float] | None = None
    lang: str = "en"  # Language of the issue messages ("en" or "de")


def _in_region(leg: Leg, region: tuple[float, float, float, float] | None) -> bool:
    if region is None:
        return False
    south, west, north, east = region
    return any(south <= s.lat <= north and west <= s.lon <= east for s in leg.stops)


def _rides(leg: Leg, routes: set[str], d: "DisruptionImpact", ctx: CheckContext) -> bool:
    """Whether the leg rides one of `routes` (a disruption's blocked or delayed lines)."""
    if not routes:
        return False
    if leg.source != "transitous":
        return leg.route_id is not None and leg.route_id in routes
    return (
        leg.line_name is not None
        and normalize_line(leg.line_name) in d.lines
        and _in_region(leg, ctx.region)
    )


def _uses_blocked_stop(leg: Leg, d: "DisruptionImpact") -> bool:
    ends = leg.stops[:1] + leg.stops[-1:]
    if leg.source != "transitous":
        return bool(d.blocked_stops & {s.stop_id for s in ends})
    return any(
        haversine_distance(s.lat, s.lon, lat, lon) <= SAME_STOP_KM
        for s in ends
        for lat, lon in d.blocked_points
    )


def _follows_report(leg: Leg, d: "DisruptionImpact", ctx: CheckContext) -> bool:
    """Whether the leg rides a line the disruption's report recommends instead."""
    if not d.recommended_lines or not leg.is_transit or leg.line_name is None:
        return False
    if leg.source == "transitous" and not _in_region(leg, ctx.region):
        return False
    return normalize_line(leg.line_name) in d.recommended_lines


def transfer_success_probability(slack_min: float) -> float:
    """P(making a connection) given scheduled slack; ~2 min typical delay spread."""
    return 1 / (1 + math.exp(-(slack_min - 1.0) / 1.5))


def _reported_delay(leg: Leg, ctx: CheckContext) -> float:
    """Minutes this leg's line is reported late during the leg (0 if no report)."""
    delays = [
        float(d.delay_minutes or DEFAULT_DELAY_MIN)
        for d in ctx.disruptions
        if _rides(leg, d.delayed_routes, d, ctx) and d.active_between(leg.departure, leg.arrival)
    ]
    return max(delays, default=0.0)


def check_plan(plan: Plan, ctx: CheckContext) -> Plan:
    issues: list[Issue] = []
    reliability = 1.0
    feasible = True
    carried_delay = 0.0  # Reported delay of the last vehicle, eating into the next transfer
    follows_report = False
    plan.transfer_checks = []
    lang = ctx.lang

    for i, leg in enumerate(plan.legs):
        # ── Time: connection slack before boarding a vehicle ──
        if leg.is_transit and i > 0:
            slack = (leg.departure - plan.legs[i - 1].arrival).total_seconds() / 60
            # Feeders with uncertain arrival times: an earlier vehicle, or an on-demand pickup
            uncertain_feeder = (
                any(prev.is_transit for prev in plan.legs[:i])
                or plan.legs[i - 1].mode == "on_demand"
            )
            if slack < 0:
                feasible = False
                issues.append(
                    Issue(
                        "error",
                        tr(
                            lang,
                            f"Connection to {leg.line_name} is missed by {-slack:.0f} min",
                            f"Anschluss an {leg.line_name} wird um {-slack:.0f} Min. verpasst",
                        ),
                        i,
                    )
                )
            elif uncertain_feeder:
                effective = slack - carried_delay
                p = transfer_success_probability(effective)
                reliability *= p
                plan.transfer_checks.append(TransferCheck(i, slack, carried_delay, p))
                if carried_delay and effective < 0:
                    issues.append(
                        Issue(
                            "warning",
                            tr(
                                lang,
                                f"Connection to {leg.line_name} at risk: the previous line is "
                                f"running about {carried_delay:.0f} min late",
                                f"Anschluss an {leg.line_name} gefährdet: die Linie davor hat "
                                f"etwa {carried_delay:.0f} Min. Verspätung",
                            ),
                            i,
                        )
                    )
                elif p < 0.7:
                    issues.append(
                        Issue(
                            "warning",
                            tr(
                                lang,
                                f"Tight transfer to {leg.line_name}: {slack:.0f} min",
                                f"Knapper Umstieg in {leg.line_name}: {slack:.0f} Min.",
                            ),
                            i,
                        )
                    )
        if leg.is_transit:
            carried_delay = _reported_delay(leg, ctx)

        if leg.cancelled:
            feasible = False
            issues.append(
                Issue(
                    "error",
                    tr(lang, f"{leg.line_name} is cancelled", f"{leg.line_name} fällt aus"),
                    i,
                )
            )
        if leg.on_request:
            # Unbooked, it doesn't come: the chain depends on the traveller phoning ahead
            reliability *= 0.9
            operator = f" ({leg.provider})" if leg.provider else ""
            issues.append(
                Issue(
                    "warning",
                    tr(
                        lang,
                        f"{leg.line_name} runs only if booked: phone the operator{operator} ahead",
                        f"{leg.line_name} fährt nur nach Anmeldung: "
                        f"vorher beim Betreiber{operator} anrufen",
                    ),
                    i,
                )
            )

        # ── Capacity ──
        if leg.is_transit and leg.route_id:
            occ = ctx.capacity.predict(leg.route_id, leg.departure, leg.mode)
            leg.occupancy = round(occ, 2)
            if occ >= FULL_OCCUPANCY:
                reliability *= 0.5
                issues.append(
                    Issue(
                        "warning",
                        tr(
                            lang,
                            f"{leg.line_name} is expected to be full ({occ:.0%})",
                            f"{leg.line_name} ist voraussichtlich voll ({occ:.0%})",
                        ),
                        i,
                    )
                )
            elif occ >= CROWDED_OCCUPANCY:
                reliability *= 0.95
                issues.append(
                    Issue(
                        "info",
                        tr(
                            lang,
                            f"{leg.line_name} is expected to be crowded ({occ:.0%})",
                            f"{leg.line_name} ist voraussichtlich stark besetzt ({occ:.0%})",
                        ),
                        i,
                    )
                )

        # ── Vehicles ──
        if leg.mode in ("bike", "scooter", "car_share"):
            n = leg.vehicles_available or 0
            if n == 0:
                feasible = False
                issues.append(
                    Issue(
                        "error",
                        tr(
                            lang,
                            f"No {vehicle(lang, leg.mode)} available at {leg.from_name}",
                            f"Kein {vehicle(lang, leg.mode)} frei an {leg.from_name}",
                        ),
                        i,
                    )
                )
            elif leg.pickup_in_future:
                reliability *= 0.8
                issues.append(
                    Issue(
                        "info",
                        tr(
                            lang,
                            f"{n} {vehicle(lang, leg.mode)}(s) at {leg.from_name} now; "
                            "availability on arrival is not guaranteed",
                            f"Jetzt {n}× {vehicle(lang, leg.mode)} an {leg.from_name}; "
                            "ob bei Ankunft noch eins frei ist, ist nicht sicher",
                        ),
                        i,
                    )
                )
            elif n == 1:
                reliability *= 0.85
                issues.append(
                    Issue(
                        "info",
                        tr(
                            lang,
                            f"Only 1 {vehicle(lang, leg.mode)} left at {leg.from_name}",
                            f"Nur noch 1 {vehicle(lang, leg.mode)} an {leg.from_name}",
                        ),
                        i,
                    )
                )
        if leg.mode == "on_demand":
            reliability *= 0.9
            if leg.wait_min > 20:
                issues.append(
                    Issue(
                        "warning",
                        tr(
                            lang,
                            f"On-demand pickup wait is about {leg.wait_min:.0f} min",
                            f"Wartezeit auf das Rufangebot etwa {leg.wait_min:.0f} Min.",
                        ),
                        i,
                    )
                )

        # ── Disruptions ──
        for d in ctx.disruptions:
            if not d.active_between(leg.departure, leg.arrival):
                continue
            if _rides(leg, d.delayed_routes, d, ctx):
                reliability *= 0.85
                issues.append(
                    Issue(
                        "warning",
                        tr(
                            lang,
                            f"{leg.line_name} is delayed"
                            + (f" by up to {d.delay_minutes} min" if d.delay_minutes else ""),
                            f"{leg.line_name} hat Verspätung"
                            + (f" (bis zu {d.delay_minutes} Min.)" if d.delay_minutes else ""),
                        ),
                        i,
                    )
                )
                continue
            hits_route = _rides(leg, d.blocked_routes, d, ctx)
            hits_stop = leg.is_transit and _uses_blocked_stop(leg, d)
            if hits_route or hits_stop:
                feasible = False
                issues.append(
                    Issue(
                        "error",
                        tr(
                            lang,
                            f"Affected by disruption: {d.summary}",
                            f"Von Störung betroffen: {d.summary}",
                        ),
                        i,
                    )
                )
            elif _follows_report(leg, d, ctx):
                follows_report = True
                issues.append(
                    Issue(
                        "info",
                        tr(
                            lang,
                            f"{leg.line_name} is the alternative the notice recommends",
                            f"{leg.line_name} ist die von der Meldung empfohlene Alternative",
                        ),
                        i,
                    )
                )

    if ctx.arrive_by and plan.arrival > ctx.arrive_by:
        late = (plan.arrival - ctx.arrive_by).total_seconds() / 60
        feasible = False
        issues.append(
            Issue(
                "error",
                tr(
                    lang,
                    f"Arrives {late:.0f} min after the requested time",
                    f"Kommt {late:.0f} Min. nach der gewünschten Zeit an",
                ),
            )
        )

    plan.feasible = feasible
    plan.reliability = round(max(0.0, min(1.0, reliability)), 3)
    plan.issues = issues
    plan.follows_report = follows_report
    plan.cost = _cost(plan)
    return plan


def _cost(plan: Plan) -> float:
    """Generalized cost in minutes: time, transfers, mode effort, and expected failure."""
    return round(
        plan.duration_min
        + TRANSFER_PENALTY_MIN * plan.transfers
        + sum(MODES[leg.mode].cost_min for leg in plan.legs if leg.mode in MODES)
        + UNRELIABILITY_PENALTY_MIN * (1 - plan.reliability)
        - (REPORT_ALTERNATIVE_BONUS_MIN if plan.follows_report else 0.0),
        1,
    )


def blend_transfer_estimate(
    plan: Plan, check: TransferCheck, jev_probability: float, lang: str = "en"
) -> None:
    """Average a second opinion (Jev) into one transfer's success probability."""
    if check.probability <= 0:
        return
    combined = (check.probability + jev_probability) / 2
    plan.reliability = round(max(0.0, min(1.0, plan.reliability / check.probability * combined)), 3)
    check.jev_probability = jev_probability
    check.probability = combined
    if jev_probability < 0.5:
        leg = plan.legs[check.leg_index]
        plan.issues.append(
            Issue(
                "warning",
                tr(
                    lang,
                    f"Jev rates the transfer to {leg.line_name} at {leg.from_name} as risky",
                    f"Jev hält den Umstieg in {leg.line_name} an {leg.from_name} für riskant",
                ),
                check.leg_index,
            )
        )
    plan.cost = _cost(plan)


def label_plans(ranked: list[Plan], arrive_by: bool = False) -> list[Plan]:
    """Sort by cost (feasible first) and label the fastest, most reliable and recommended.

    With a deadline (arrive-by), the useful extreme is the latest departure, not the
    earliest arrival.
    """
    ranked.sort(key=lambda p: (not p.feasible, p.cost))
    for plan in ranked:
        plan.label = None
    if ranked:
        if arrive_by:
            max(ranked, key=lambda p: (p.feasible, p.departure)).label = "Leave latest"
        else:
            min(ranked, key=lambda p: p.arrival).label = "Fastest"
        most_reliable = max(ranked, key=lambda p: (p.reliability, -p.cost))
        most_reliable.label = most_reliable.label or "Most reliable"
        ranked[0].label = ranked[0].label or "Recommended"
    return ranked


def rank_plans(plans: list[Plan], ctx: CheckContext, limit: int = 5) -> list[Plan]:
    """Check every candidate, drop infeasible ones (unless nothing is feasible), rank by cost."""
    for plan in plans:
        check_plan(plan, ctx)
    pool = [p for p in plans if p.feasible] or plans
    # Drop dominated chains: another leaves no earlier, arrives no later, and costs less
    pool = [
        b
        for b in pool
        if not any(
            a is not b and a.departure >= b.departure and a.arrival <= b.arrival and a.cost < b.cost
            for a in pool
        )
    ]
    ranked = sorted(pool, key=lambda p: (not p.feasible, p.cost))[:limit]
    return label_plans(ranked, arrive_by=ctx.arrive_by is not None)
