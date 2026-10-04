"""Street routing via an OSRM-compatible server (mocked here; FOSSGIS servers in production)."""

from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

import app.services.routing.streets as streets_module
from app.services.optimisation import CheckContext, rank_plans
from app.services.routing import plan_candidates
from app.services.routing.planner import refine_with_streets
from app.services.routing.streets import StreetRouter
from app.state import state

TZ = ZoneInfo("Europe/Berlin")


def osrm(log: list[str] | None = None, status: int = 200, minutes: float = 15.0) -> StreetRouter:
    def handler(request: httpx.Request) -> httpx.Response:
        if log is not None:
            log.append(str(request.url))
        if status != 200:
            return httpx.Response(status)
        coords = request.url.path.split("/")[-1].split(";")
        lonlat = [list(map(float, c.split(","))) for c in coords]
        geometry = {"type": "LineString", "coordinates": [lonlat[0], [10.0, 51.0], lonlat[-1]]}
        route = {"duration": minutes * 60, "distance": 3000.0, "geometry": geometry}
        return httpx.Response(200, json={"code": "Ok", "routes": [route]})

    return StreetRouter(
        "https://osrm.test/routed-{profile}", transport=httpx.MockTransport(handler)
    )


def test_route_profiles_geometry_and_cache(monkeypatch):
    monkeypatch.setattr(streets_module, "MIN_INTERVAL_S", 0)
    log: list[str] = []
    router = osrm(log)
    minutes, km, geometry = router.route("walk", (51.9, 10.4), (51.8, 10.3))
    assert (minutes, km) == (15.0, 3.0)
    assert geometry[0] == (51.9, 10.4) and geometry[1] == (51.0, 10.0)  # lat/lon order
    assert log[0].startswith("https://osrm.test/routed-foot/route/v1/driving/10.400000,51.900000;")
    scooter_minutes, _, _ = router.route("scooter", (51.9, 10.4), (51.8, 10.3))
    assert "routed-bike" in log[-1] and scooter_minutes < 15.0  # Scooters: bike profile, faster
    router.route("walk", (51.9, 10.4), (51.8, 10.3))
    assert len(log) == 2  # Cached
    router.close()


def test_failures_open_the_breaker(monkeypatch):
    monkeypatch.setattr(streets_module, "MIN_INTERVAL_S", 0)
    log: list[str] = []
    router = osrm(log, status=503)
    assert router.route("walk", (51.9, 10.4), (51.8, 10.3)) is None
    assert router.route("walk", (51.7, 10.2), (51.8, 10.3)) is None
    assert len(log) == 1 and router.describe()["available"] is False
    router.close()


def test_rate_limit_pauses_only_briefly(monkeypatch):
    monkeypatch.setattr(streets_module, "MIN_INTERVAL_S", 0)
    router = osrm(status=429)
    t0 = streets_module.time.monotonic()
    assert router.route("car_share", (51.9, 10.4), (51.8, 10.3)) is None
    assert router._down_until - t0 <= streets_module.RATE_LIMITED_SECONDS + 1
    router.close()


def test_refine_retimes_legs_and_rechecks(monkeypatch):
    monkeypatch.setattr(streets_module, "MIN_INTERVAL_S", 0)
    net = state.network_for(datetime(2026, 10, 5).date())
    ctx = state.planning_context(net)
    when = datetime(2026, 10, 5, 7, 10, tzinfo=TZ)
    check = CheckContext()
    plans = rank_plans(
        plan_candidates(
            ctx, ("A", 51.9050, 10.4270), ("B", 51.8040, 10.3390), when, ["bus", "walk"]
        ),
        check,
    )
    router = osrm(minutes=9.0)  # Every street leg takes 9 min
    assert refine_with_streets(router, plans, when) > 0
    for plan in plans:
        walks = [leg for leg in plan.legs if leg.mode == "walk" and leg.distance_km >= 0.05]
        assert all(round(leg.duration_min) == 9 and len(leg.path) == 3 for leg in walks)
        assert plan.departure >= when
        for prev, nxt in zip(plan.legs, plan.legs[1:], strict=False):
            assert nxt.departure >= prev.arrival  # Still a consistent chain
    router.close()
