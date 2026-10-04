"""Tests for the GTFS loader, network, CSA and planner on the dev fixture."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

import numpy as np
import pytest

from app.services.ingestion.gtfs import parse_gtfs_time, route_type_to_mode
from app.services.routing import Blocking, earliest_arrival, plan_candidates
from app.services.routing.csa import Ride
from app.services.routing.network import normalize_line
from app.state import state

TZ = ZoneInfo("Europe/Berlin")
MONDAY = date(2026, 10, 5)


@pytest.fixture
def net():
    return state.network_for(MONDAY)


def lines_used(net, journey):
    return [net.route_short[net.trip_route[p.trip]] for p in journey.parts if isinstance(p, Ride)]


def test_gtfs_basics(net):
    assert parse_gtfs_time("25:10:30") == 25 * 3600 + 10 * 60 + 30
    assert route_type_to_mode(3) == "bus" and route_type_to_mode(109) == "train"
    assert route_type_to_mode(715) == "on_demand"
    assert normalize_line("Buslinie 830") == "830" and normalize_line("re 10") == "RE10"
    assert state.feed.active_service_ids(date(2025, 1, 1)) == set()  # Outside the calendar
    assert net.n_connections > 0
    assert np.all(np.diff(net.c_dep) >= 0), "connections must be sorted by departure"


def test_csa_direct_bus(net):
    gs, cz = net.stop_index["GS_BF"], net.stop_index["CZ_UNI"]
    journey = earliest_arrival(net, {gs: 7 * 3600}, {cz: 0})
    assert lines_used(net, journey) == ["830"]
    assert journey.arrival_at_stop == 7 * 3600 + 43 * 60  # 07:00 departure + 43 min


def test_csa_routes_around_blocked_line(net):
    gs, zob = net.stop_index["GS_BF"], net.stop_index["CZ_ZOB"]
    mask = np.isin(net.c_route, [net.route_index["r830"]])
    journey = earliest_arrival(net, {gs: 7 * 3600}, {zob: 0}, Blocking(conn_mask=mask))
    assert lines_used(net, journey) == ["810", "840"]  # Via Bad Harzburg, as the report advises


def test_csa_respects_minimum_transfer(net):
    gs, zob = net.stop_index["GS_BF"], net.stop_index["CZ_ZOB"]
    mask = np.isin(net.c_route, [net.route_index["r830"]])
    journey = earliest_arrival(
        net, {gs: 7 * 3600}, {zob: 0}, Blocking(conn_mask=mask), min_transfer=30 * 60
    )
    rides = journey.rides
    assert net.l_dep[rides[1].board_conn] - net.l_arr[rides[0].alight_conn] >= 30 * 60


def test_csa_blocked_stop_cannot_be_used(net):
    gs, bs = net.stop_index["GS_BF"], net.stop_index["BS_HBF"]
    assert earliest_arrival(net, {gs: 7 * 3600}, {bs: 0}) is not None
    blocked = Blocking(stop_windows={bs: [(0, 30 * 3600)]})
    assert earliest_arrival(net, {gs: 7 * 3600}, {bs: 0}, blocked) is None


def test_planner_offers_transit_and_sharing(net):
    ctx = state.planning_context(net)
    when = datetime(2026, 10, 5, 7, 10, tzinfo=TZ)
    plans = plan_candidates(ctx, ("Home", 51.9050, 10.4270), ("TU", 51.8040, 10.3390), when)
    modes = {leg.mode for p in plans for leg in p.legs}
    assert {"bus", "walk", "car_share", "on_demand"} <= modes
    for p in plans:
        assert p.departure >= when
        assert p.legs[0].from_name == "Home" and p.legs[-1].to_name == "TU"


def test_planner_short_trip_offers_walking(net):
    ctx = state.planning_context(net)
    when = datetime(2026, 10, 5, 12, 0, tzinfo=TZ)
    plans = plan_candidates(ctx, ("A", 51.9086, 10.4283), ("B", 51.9057, 10.4295), when, ["walk"])
    assert [leg.mode for leg in plans[0].legs] == ["walk"]


def test_arrive_by_leaves_as_late_as_possible(net):
    from app.services.routing.planner import plan_arrive_by

    ctx = state.planning_context(net)
    deadline = datetime(2026, 10, 5, 8, 30, tzinfo=TZ)
    plans = plan_arrive_by(
        ctx, ("Home", 51.9050, 10.4270), ("TU", 51.8040, 10.3390), deadline, ["bus", "walk"]
    )
    assert plans and all(p.arrival <= deadline for p in plans)
    latest = max(plans, key=lambda p: p.departure)
    # The 07:30 bus from Goslar Bahnhof (boarded at Marktplatz 07:35, arrives 08:13) is the
    # last one that makes it; the 08:00 bus arrives 08:43
    transit = [leg for leg in latest.legs if leg.line_name]
    assert [(leg.line_name, leg.arrival.strftime("%H:%M")) for leg in transit] == [("830", "08:13")]
    # Never departs before `earliest`, and an impossible window returns nothing
    assert (
        plan_arrive_by(
            ctx,
            ("Home", 51.9050, 10.4270),
            ("TU", 51.8040, 10.3390),
            deadline,
            ["bus", "walk"],
            earliest=datetime(2026, 10, 5, 8, 0, tzinfo=TZ),
        )
        == []
    )


def test_planner_offers_later_departures(net):
    ctx = state.planning_context(net)
    when = datetime(2026, 10, 5, 7, 10, tzinfo=TZ)
    plans = plan_candidates(
        ctx, ("Home", 51.9050, 10.4270), ("TU", 51.8040, 10.3390), when, ["bus", "walk"]
    )
    buses = sorted(
        leg.departure.strftime("%H:%M") for p in plans for leg in p.legs if leg.line_name == "830"
    )
    assert buses[:3] == ["07:30", "08:00", "08:30"]  # The 830 every 30 minutes
