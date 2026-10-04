"""Regression tests for bugs found in the line-by-line review."""

import io
import zipfile
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from app.services.disruptions import match_stops, window
from app.services.ingestion.gbfs import GbfsSnapshot, VehicleSpot
from app.services.ingestion.gtfs import load_gtfs
from app.services.llm import extract_with_rules
from app.services.llm.extraction import DisruptionExtraction
from app.services.ml import CapacityModel
from app.services.optimisation import CheckContext, DisruptionImpact, check_plan
from app.services.routing import plan_candidates
from app.services.routing.types import Leg, Plan, StopCall
from app.state import state

TZ = ZoneInfo("Europe/Berlin")
T0 = datetime(2026, 10, 5, 7, 0, tzinfo=TZ)


def test_gtfs_tolerates_stops_without_coordinates(tmp_path):
    src = state.feed.source
    path = tmp_path / "feed.zip"
    with zipfile.ZipFile(path, "w") as zf:
        for name in ("routes", "trips", "stop_times", "calendar", "agency"):
            zf.write(f"{src}/{name}.txt", f"{name}.txt")
        stops = pd.read_csv(f"{src}/stops.txt", dtype=str)
        entrance = pd.DataFrame(
            [{"stop_id": "ENTR", "stop_name": "Entrance", "stop_lat": "", "stop_lon": ""}]
        )
        buf = io.StringIO()
        pd.concat([stops, entrance]).to_csv(buf, index=False)
        zf.writestr("stops.txt", buf.getvalue())
    feed = load_gtfs(path)
    assert "ENTR" not in set(feed.stops["stop_id"])
    assert feed.stop_times["arrival"].dtype.kind == "i"


def test_planner_never_starts_before_requested_time():
    net = state.network_for(T0.date())
    ctx = state.planning_context(net)
    # Leave exactly when a 830 bus can just be reached on foot
    for minute in range(0, 60, 3):
        when = T0 + timedelta(minutes=minute)
        for plan in plan_candidates(ctx, ("A", 51.9050, 10.4270), ("B", 51.8040, 10.3390), when):
            assert plan.departure >= when, (minute, [leg.mode for leg in plan.legs])


def test_vehicle_count_is_per_mode_and_bikes_need_a_return_station():
    spots = [
        VehicleSpot("P", "st1", "Station 1", 51.90, 10.40, "station", {"bike": 6, "car_share": 1}),
        VehicleSpot("P", "st2", "Station 2", 51.80, 10.30, "station", {"bike": 2}),
    ]
    gbfs = GbfsSnapshot(spots=spots)
    assert gbfs.can_drop_off(spots[0], 51.8001, 10.3001)  # Next to station 2
    assert not gbfs.can_drop_off(spots[0], 51.70, 10.20)  # No station there
    free = VehicleSpot("P", "sc", "Scooter", 51.9, 10.4, "free_floating", {"scooter": 1})
    assert gbfs.can_drop_off(free, 51.70, 10.20)

    net = state.network_for(T0.date())
    ctx = state.planning_context(net)
    for plan in plan_candidates(ctx, ("A", 51.9086, 10.4283), ("B", 51.8040, 10.3390), T0):
        for leg in plan.legs:
            if leg.mode == "car_share":
                assert leg.vehicles_available == 1  # Not 7 (bikes + car at Goslar Bahnhof)


def _leg(mode, start, end, line=None, route_id=None):
    dep, arr = T0 + timedelta(minutes=start), T0 + timedelta(minutes=end)
    stops = [StopCall("A", "A", 0, 0, dep), StopCall("B", "B", 0, 0, arr)] if line else []
    return Leg(
        mode, "A", "B", 0, 0, 0, 0, dep, arr, 1.0, line_name=line, route_id=route_id, stops=stops
    )


def test_reported_delay_eats_into_the_next_transfer():
    plan = lambda: Plan([_leg("bus", 0, 20, "810", "r810"), _leg("bus", 24, 50, "840", "r840")])  # noqa: E731
    on_time = check_plan(plan(), CheckContext())
    late = DisruptionImpact(1, "810 late", "low", {"r810"}, effect="delayed", delay_minutes=10)
    delayed = check_plan(plan(), CheckContext(disruptions=[late]))
    assert delayed.reliability < on_time.reliability
    assert any("at risk" in i.message for i in delayed.issues)
    assert delayed.feasible  # A delay warns; it doesn't make the chain impossible


def test_town_names_do_not_block_every_stop_in_town():
    net = state.network_for(T0.date())
    assert match_stops(net, ["Goslar"]) == []  # 4 Goslar stops: ambiguous
    assert match_stops(net, ["Goslar Bahnhof"]) == ["GS_BF"]


def test_window_since_last_night():
    start, end = window(datetime(2026, 10, 5, 7, 0, tzinfo=TZ), "22:00", None)
    assert start == datetime(2026, 10, 4, 22, 0, tzinfo=TZ) and end is None


def test_rules_ignore_dates_and_read_until_times():
    result = extract_with_rules("Am 05.10. ist die Linie 830 bis ca. 10:00 Uhr gesperrt.")
    assert (result.start_time, result.end_time) == (None, "10:00")
    scooters = extract_with_rules("15 vehicles unavailable. Expected back online by 16:00.")
    assert (scooters.start_time, scooters.end_time) == (None, "16:00")


def test_llm_times_are_normalized_or_dropped():
    base = dict(
        summary="x",
        affected_lines=[],
        affected_stops=[],
        duration_unknown=True,
        disruption_type="other",
        effect="other",
        delay_minutes=None,
        alternatives=[],
    )
    ok = DisruptionExtraction(**base, start_time="8:15", end_time="10.00")
    assert (ok.start_time, ok.end_time) == ("08:15", "10:00")
    bad = DisruptionExtraction(**base, start_time="ca. 10 Uhr", end_time=None)
    assert bad.start_time is None


def test_model_worse_than_heuristic_is_not_loaded(tmp_path):
    model = CapacityModel(estimator="placeholder", metrics={"mae": 0.3, "mae_heuristic": 0.2})
    path = tmp_path / "worse.joblib"
    model.save(path)
    assert not CapacityModel.load_or_default(path).trained


async def test_expired_disruptions_are_not_used_for_planning(client, fake_db):
    from app.models import Disruption

    fake_db.rows.append(
        Disruption(
            id=1,
            raw_text="old",
            source="t",
            status="active",
            summary="old",
            effect="suspended",
            affected_routes='["r830"]',
            affected_stops="[]",
            start_time=datetime(2026, 10, 5, 5, 0),
            end_time=datetime(2026, 10, 5, 6, 0),
        )
    )
    payload = {
        "origin_lat": 51.905,
        "origin_lon": 10.427,
        "destination_lat": 51.804,
        "destination_lon": 10.339,
        "departure_time": "2026-10-05T07:10:00",
    }
    plan = (await client.post("/api/routes/plan", json=payload)).json()
    assert plan["disruptions_considered"] == []
    assert any(leg["line_name"] == "830" for r in plan["routes"] for leg in r["legs"])
