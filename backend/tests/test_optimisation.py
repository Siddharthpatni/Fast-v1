"""Tests for the optimisation check and the ML models it relies on."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from app.services.ml import CapacityModel
from app.services.optimisation import (
    CheckContext,
    DisruptionImpact,
    check_plan,
    rank_plans,
    transfer_success_probability,
)
from app.services.routing.types import Leg, Plan, StopCall

TZ = ZoneInfo("Europe/Berlin")
T0 = datetime(2026, 10, 5, 7, 0, tzinfo=TZ)


def leg(mode, start_min, end_min, line=None, route_id=None, **kw):
    dep, arr = T0 + timedelta(minutes=start_min), T0 + timedelta(minutes=end_min)
    stops = [StopCall("A", "A", 0, 0, dep), StopCall("B", "B", 0, 0, arr)] if line else []
    return Leg(
        mode,
        "A",
        "B",
        0,
        0,
        0,
        0,
        dep,
        arr,
        1.0,
        line_name=line,
        route_id=route_id,
        stops=stops,
        **kw,
    )


def test_transfer_probability_increases_with_slack():
    probs = [transfer_success_probability(s) for s in (0, 2, 5, 10)]
    assert probs == sorted(probs) and probs[-1] > 0.99


def test_missed_connection_is_infeasible():
    plan = Plan([leg("bus", 0, 20, "1", "r1"), leg("bus", 18, 40, "2", "r2")])
    check_plan(plan, CheckContext())
    assert not plan.feasible
    assert "missed" in plan.issues[0].message


def test_tight_transfer_lowers_reliability():
    tight = check_plan(
        Plan([leg("bus", 0, 20, "1", "r1"), leg("bus", 21, 40, "2", "r2")]), CheckContext()
    )
    easy = check_plan(
        Plan([leg("bus", 0, 20, "1", "r1"), leg("bus", 30, 50, "2", "r2")]), CheckContext()
    )
    assert tight.feasible and tight.reliability < easy.reliability
    assert any("Tight transfer" in i.message for i in tight.issues)


def test_no_shared_vehicle_is_infeasible():
    plan = check_plan(Plan([leg("bike", 0, 10, vehicles_available=0)]), CheckContext())
    assert not plan.feasible


def test_disruption_effects():
    plan = lambda: Plan([leg("bus", 0, 20, "830", "r830")])  # noqa: E731
    suspended = DisruptionImpact(1, "830 blocked", "high", {"r830"}, effect="suspended")
    delayed = DisruptionImpact(2, "830 late", "low", {"r830"}, effect="delayed", delay_minutes=5)
    assert not check_plan(plan(), CheckContext(disruptions=[suspended])).feasible
    late = check_plan(plan(), CheckContext(disruptions=[delayed]))
    assert late.feasible and late.reliability < 1
    # Not active during the leg → no effect
    later = DisruptionImpact(
        3, "x", "high", {"r830"}, start=T0 + timedelta(hours=3), effect="suspended"
    )
    assert check_plan(plan(), CheckContext(disruptions=[later])).feasible


def test_line_the_report_recommends_is_preferred():
    """The language model's proposal (the report's alternative line) steers the ranking."""
    closed = DisruptionImpact(
        1, "830 blocked", "high", {"r830"}, effect="suspended", recommended_lines={"831"}
    )
    ctx = CheckContext(disruptions=[closed])
    other = Plan([leg("walk", 0, 5), leg("bus", 5, 30, "832", "r832")])
    proposed = Plan([leg("walk", 0, 5), leg("bus", 5, 31, "Linie 831", "r831")])
    ranked = rank_plans([other, proposed], ctx)
    assert ranked[0] is proposed and proposed.follows_report and not other.follows_report
    assert any("recommends" in i.message for i in proposed.issues)
    # The bonus is gone once the disruption is over
    later = CheckContext(
        disruptions=[
            DisruptionImpact(
                2, "x", "high", {"r830"}, end=T0 - timedelta(hours=1), recommended_lines={"831"}
            )
        ]
    )
    assert not check_plan(proposed, later).follows_report


def test_rank_drops_dominated_and_labels():
    fast = Plan([leg("walk", 0, 30)])
    slow = Plan([leg("walk", 0, 45)])  # Same departure, later arrival, higher cost
    ranked = rank_plans([slow, fast], CheckContext())
    assert ranked == [fast] and fast.label == "Fastest"


def test_capacity_heuristic_peaks():
    model = CapacityModel()
    peak = model.predict("r1", datetime(2026, 10, 5, 7, 45))
    midday = model.predict("r1", datetime(2026, 10, 5, 12, 0))
    sunday = model.predict("r1", datetime(2026, 10, 4, 7, 45))
    assert peak > midday and peak > sunday


def test_capacity_model_learns_route_differences(tmp_path):
    rng = np.random.default_rng(0)
    rows = []
    for day in range(28):
        for hour in range(5, 23):
            for route, base in (("busy", 0.8), ("quiet", 0.2)):
                ts = datetime(2026, 9, 1) + timedelta(days=day, hours=hour)
                rows.append(
                    {
                        "route_id": route,
                        "timestamp": ts,
                        "occupancy": float(np.clip(base + rng.normal(0, 0.05), 0, 1)),
                    }
                )
    model = CapacityModel.fit(pd.DataFrame(rows))
    assert model.metrics["mae"] < model.metrics["mae_heuristic"]
    assert model.predict("busy", datetime(2026, 10, 1, 12)) > model.predict(
        "quiet", datetime(2026, 10, 1, 12)
    )

    path = tmp_path / "capacity.joblib"
    model.save(path)
    loaded = CapacityModel.load_or_default(path)
    assert loaded.trained and loaded.predict("busy", datetime(2026, 10, 1, 12)) > 0.5
    assert not CapacityModel.load_or_default(tmp_path / "missing.joblib").trained


def test_phone_booked_trip_stays_offered_with_a_booking_warning():
    ast = leg(
        "bus",
        5,
        30,
        line="AST 7",
        route_id="15047",
        on_request=True,
        provider="Harzer Verkehrsbetriebe GmbH",
    )
    plan = check_plan(Plan(legs=[leg("walk", 0, 5), ast]), CheckContext())
    assert plan.feasible and plan.reliability < 1
    assert any("runs only if booked" in i.message and "Harzer" in i.message for i in plan.issues)


def test_issue_messages_follow_the_requested_language():
    late = leg("bus", 10, 40, line="830", route_id="r")
    plan = check_plan(
        Plan(legs=[leg("bus", 0, 12, line="RB82", route_id="x"), late]), CheckContext(lang="de")
    )
    assert any("Anschluss an 830 wird um 2 Min. verpasst" == i.message for i in plan.issues)
