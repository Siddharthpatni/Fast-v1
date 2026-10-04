"""Tests for Germany-wide routing through Transitous and its disruption matching."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest

from app.services.optimisation import CheckContext, DisruptionImpact, check_plan
from app.services.routing.transitous import (
    TransitousClient,
    TransitousError,
    decode_polyline,
    itinerary_to_plan,
)

TZ = ZoneInfo("Europe/Berlin")
HARZ = (51.70, 10.15, 52.35, 10.75)


def _stop(name, lat, lon, **times):
    return {"name": name, "stopId": f"de-DELFI_{name}", "lat": lat, "lon": lon, **times}


# Shaped like a real /api/v5/plan itinerary: walk, bus 830 (4 min late), walk
ITINERARY = {
    "startTime": "2026-10-05T05:00:00Z",
    "endTime": "2026-10-05T05:40:00Z",
    "legs": [
        {
            "mode": "WALK",
            "from": _stop("START", 51.9100, 10.4200),
            "to": _stop("Goslar, ZOB", 51.9080, 10.4250),
            "startTime": "2026-10-05T05:00:00Z",
            "endTime": "2026-10-05T05:04:00Z",
            "distance": 250.0,
            "legGeometry": {"points": "", "precision": 6},
        },
        {
            "mode": "BUS",
            "from": _stop(
                "Goslar, ZOB", 51.9080, 10.4250, departure="2026-10-05T05:08:00Z", track="3"
            ),
            "to": _stop(
                "Clausthal-Zellerfeld, ZOB", 51.8047, 10.3393, arrival="2026-10-05T05:36:00Z"
            ),
            "intermediateStops": [
                _stop(
                    "Hahnenklee",
                    51.86,
                    10.34,
                    arrival="2026-10-05T05:20:00Z",
                    departure="2026-10-05T05:20:00Z",
                )
            ],
            "startTime": "2026-10-05T05:08:00Z",
            "endTime": "2026-10-05T05:36:00Z",
            "scheduledStartTime": "2026-10-05T05:04:00Z",
            "realTime": True,
            "routeShortName": "830",
            "routeColor": "a23b72",
            "routeId": "de-DELFI_830",
            "tripId": "trip-830",
            "headsign": "Clausthal-Zellerfeld",
            "agencyName": "RBB",
            "legGeometry": {"points": "", "precision": 6},
        },
        {
            "mode": "WALK",
            "from": _stop("Clausthal-Zellerfeld, ZOB", 51.8047, 10.3393),
            "to": _stop("END", 51.8040, 10.3400),
            "startTime": "2026-10-05T05:36:00Z",
            "endTime": "2026-10-05T05:40:00Z",
            "distance": 90.0,
            "legGeometry": {"points": "", "precision": 6},
        },
    ],
}


def test_decode_polyline_precision_6():
    # Google's reference example, at precision 6 the coordinates are 10x smaller
    points = decode_polyline("_p~iF~ps|U_ulLnnqC_mqNvxq`@", precision=5)
    assert points == [(38.5, -120.2), (40.7, -120.95), (43.252, -126.453)]
    assert decode_polyline("_p~iF~ps|U", precision=6)[0] == pytest.approx((3.85, -12.02))


def test_itinerary_becomes_a_plan_with_live_delay_and_platform():
    plan = itinerary_to_plan(ITINERARY, TZ)
    assert plan.source == "transitous"
    assert [leg.mode for leg in plan.legs] == ["walk", "bus", "walk"]
    bus = plan.legs[1]
    assert bus.line_name == "830" and bus.line_color == "#a23b72"
    assert bus.delay_min == 4 and bus.platform == "3"
    assert [s.name for s in bus.stops] == ["Goslar, ZOB", "Hahnenklee", "Clausthal-Zellerfeld, ZOB"]
    assert bus.departure == datetime(2026, 10, 5, 7, 8, tzinfo=TZ)
    assert plan.legs[0].distance_km == 0.25


def test_long_distance_trains_are_named_by_train_number():
    raw = dict(
        ITINERARY["legs"][1], mode="HIGHSPEED_RAIL", routeShortName="19", tripShortName="ICE 651"
    )
    plan = itinerary_to_plan(dict(ITINERARY, legs=[raw]), TZ)
    assert plan.legs[0].mode == "train" and plan.legs[0].line_name == "ICE 651"


def _impact(effect, **kw):
    return DisruptionImpact(
        id=1,
        summary="Bus 830 closed",
        severity="high",
        route_ids={"22661"},  # FastMile's own feed ID: never equal to a Transitous ID
        lines={"830"},
        effect=effect,
        start=datetime(2026, 10, 5, 6, 0, tzinfo=TZ),
        end=datetime(2026, 10, 5, 18, 0, tzinfo=TZ),
        **kw,
    )


def test_suspended_line_blocks_transitous_leg_by_name_inside_region():
    plan = itinerary_to_plan(ITINERARY, TZ)
    check_plan(plan, CheckContext(disruptions=[_impact("suspended")], region=HARZ))
    assert not plan.feasible
    assert any("Affected by disruption" in i.message for i in plan.issues)


def test_same_line_number_elsewhere_in_germany_is_not_blocked():
    plan = itinerary_to_plan(ITINERARY, TZ)
    elsewhere = (53.0, 13.0, 54.0, 14.0)  # The loaded region is somewhere else
    check_plan(plan, CheckContext(disruptions=[_impact("suspended")], region=elsewhere))
    assert plan.feasible


def test_closed_stop_blocks_transitous_leg_by_position():
    plan = itinerary_to_plan(ITINERARY, TZ)
    closed = _impact("station_closed", stop_ids={"414153"}, stop_points=[(51.8048, 10.3394)])
    closed.route_ids, closed.lines = set(), set()
    check_plan(plan, CheckContext(disruptions=[closed], region=HARZ))
    assert not plan.feasible


async def test_client_sends_modes_and_names_the_trip_ends():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.url.params)
        seen["agent"] = request.headers["user-agent"]
        return httpx.Response(200, json={"itineraries": [ITINERARY]})

    client = TransitousClient("https://transitous.test", transport=httpx.MockTransport(handler))
    when = datetime(2026, 10, 5, 7, 0, tzinfo=TZ)
    plans = await client.plan(
        ("Home", 51.91, 10.42), ("Work", 51.80, 10.34), when, modes=["bus", "walk"]
    )
    again = await client.plan(
        ("Home", 51.91, 10.42), ("Work", 51.80, 10.34), when, modes=["bus", "walk"]
    )
    await client.close()

    assert seen["transitModes"] == "BUS,COACH" and seen["arriveBy"] == "false"
    assert "FastMile" in seen["agent"]
    assert plans[0].legs[0].from_name == "Home" and plans[0].legs[-1].to_name == "Work"
    assert len(again) == 1 and client.requests == 1  # Second call served from the cache
    assert (
        await TransitousClient("https://x.test").plan(
            ("A", 0, 0), ("B", 0, 0), when, modes=["bike"]
        )
        == []
    )  # Nothing Transitous could plan for us


async def test_client_error_is_reported():
    client = TransitousClient(
        "https://transitous.test", transport=httpx.MockTransport(lambda r: httpx.Response(503))
    )
    with pytest.raises(TransitousError):
        await client.plan(("A", 51, 10), ("B", 52, 13), datetime.now(TZ) + timedelta(hours=1))
    assert client.describe()["status"] == "error"
    await client.close()
