"""Tests for the FastMile API endpoints (dev network, fake DB)."""

from tests.conftest import MONDAY_0710

GOSLAR_ALTSTADT = {"origin_lat": 51.9050, "origin_lon": 10.4270}
TU_CLAUSTHAL = {"destination_lat": 51.8040, "destination_lon": 10.3390}
ACCIDENT_830 = (
    "Wegen eines Unfalls auf der Strecke zwischen Goslar und Clausthal-Zellerfeld ist die "
    "Buslinie 830 seit 07:00 Uhr gesperrt. Voraussichtliche Dauer bis ca. 10:00 Uhr."
)


async def test_health_check(client):
    """Health endpoint returns 200 with service info."""
    response = await client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["service"] == "fastmile"
    assert data["version"] == "0.1.0"
    assert isinstance(data["jev_enabled"], bool)
    assert data["network_loaded"] is True


async def test_plan_route_returns_timed_multimodal_chains(client):
    payload = {**GOSLAR_ALTSTADT, **TU_CLAUSTHAL, "departure_time": MONDAY_0710}
    response = await client.post("/api/routes/plan", json=payload)
    assert response.status_code == 200
    routes = response.json()["routes"]
    assert routes
    assert any(leg["line_name"] == "830" for r in routes for leg in r["legs"])
    for route in routes:
        assert 0 <= route["reliability_score"] <= 1
        assert route["legs"][0]["path"], "every leg needs a path for the map"
        # Legs are contiguous in time
        for prev, nxt in zip(route["legs"], route["legs"][1:], strict=False):
            assert nxt["departure_time"] >= prev["arrival_time"]


async def test_plan_respects_preferred_modes(client):
    payload = {
        **GOSLAR_ALTSTADT,
        **TU_CLAUSTHAL,
        "departure_time": MONDAY_0710,
        "preferred_modes": ["bus"],
    }
    routes = (await client.post("/api/routes/plan", json=payload)).json()["routes"]
    assert routes
    assert {leg["mode"] for r in routes for leg in r["legs"]} <= {"bus", "walk"}


async def test_route_validation_rejects_bad_coords(client):
    """Route planning rejects invalid coordinates."""
    payload = {"origin_lat": 999, "origin_lon": 10.4282, **TU_CLAUSTHAL}
    response = await client.post("/api/routes/plan", json=payload)
    assert response.status_code == 422  # Pydantic validation error


async def test_disruption_is_extracted_matched_and_avoided(client):
    created = await client.post(
        "/api/disruptions",
        json={"raw_text": ACCIDENT_830, "source": "VRB", "reported_at": "2026-10-05T07:05:00"},
    )
    assert created.status_code == 200
    d = created.json()
    assert d["effect"] == "suspended"
    assert d["affected_lines"] == ["830"]
    assert d["affected_routes"] == ["r830"]
    assert d["severity"] == "high"  # From the effect, since Jev isn't configured in tests
    assert d["end_time"].startswith("2026-10-05T10:00")

    payload = {**GOSLAR_ALTSTADT, **TU_CLAUSTHAL, "departure_time": MONDAY_0710}
    plan = (await client.post("/api/routes/plan", json=payload)).json()
    assert plan["disruptions_considered"] == [d["id"]]
    assert all(leg["line_name"] != "830" for r in plan["routes"] for leg in r["legs"])


async def test_replan_detects_broken_trip_and_offers_alternatives(client):
    payload = {
        **GOSLAR_ALTSTADT,
        **TU_CLAUSTHAL,
        "departure_time": MONDAY_0710,
        "preferred_modes": ["bus", "walk"],
    }
    routes = (await client.post("/api/routes/plan", json=payload)).json()["routes"]
    trip = next(r for r in routes if any(leg["line_name"] == "830" for leg in r["legs"]))

    # Still valid before the disruption exists
    before = await client.post("/api/routes/replan", json={"route": trip, "at_time": MONDAY_0710})
    assert before.json()["original_still_valid"] is True

    await client.post(
        "/api/disruptions",
        json={"raw_text": ACCIDENT_830, "source": "VRB", "reported_at": "2026-10-05T07:05:00"},
    )
    after = (
        await client.post("/api/routes/replan", json={"route": trip, "at_time": MONDAY_0710})
    ).json()
    assert after["original_still_valid"] is False
    assert any("disruption" in i["message"] for i in after["original_issues"])
    assert after["routes"]
    assert all(leg["line_name"] != "830" for r in after["routes"] for leg in r["legs"])


async def test_resolve_disruption(client):
    created = (
        await client.post("/api/disruptions", json={"raw_text": ACCIDENT_830, "source": "VRB"})
    ).json()
    resolved = await client.patch(f"/api/disruptions/{created['id']}", json={"status": "resolved"})
    assert resolved.status_code == 200
    assert resolved.json()["status"] == "resolved"
    missing = await client.patch("/api/disruptions/999", json={"status": "resolved"})
    assert missing.status_code == 404


async def test_list_providers(client):
    """Providers report what is actually loaded."""
    response = await client.get("/api/providers")
    assert response.status_code == 200
    providers = {p["name"]: p for p in response.json()["providers"]}
    assert len(providers) == 5
    assert providers["Public Transport (GTFS)"]["status"] == "live"
    assert providers["Micromobility (GBFS)"]["status"] == "live"


async def test_stops_near_point(client):
    response = await client.get(
        "/api/stops", params={"lat": 51.9086, "lon": 10.4283, "radius_km": 0.5}
    )
    names = [s["name"] for s in response.json()]
    assert names[0] == "Goslar Bahnhof"


async def test_network_status(client):
    data = (await client.get("/api/network/status")).json()
    assert data["stops"] == 15
    assert data["shared_vehicles"]["bike"] > 0
    assert data["models"]["capacity"]["trained"] is False


async def test_arrive_by_endpoint(client):
    payload = {
        **GOSLAR_ALTSTADT,
        **TU_CLAUSTHAL,
        "arrive_by": "2026-10-05T08:30:00",
        "departure_time": "2026-10-05T05:00:00",
        "preferred_modes": ["bus", "walk"],
    }
    routes = (await client.post("/api/routes/plan", json=payload)).json()["routes"]
    assert routes and all(r["arrival_time"] <= "2026-10-05T08:30" for r in routes)
    assert max(r["departure_time"] for r in routes) >= "2026-10-05T07:2"
    latest = max(routes, key=lambda r: r["departure_time"])
    assert latest["label"] == "Leave latest" and all(r["label"] != "Fastest" for r in routes)


async def test_geocode_returns_stops_first(client, monkeypatch):
    from app.state import state as app_state

    class FakeGeocoder:
        async def search(self, q, limit):
            from app.services.geocoding import Place

            return [Place("Rammelsberg", "38640 Goslar", 51.89, 10.42, "attraction")]

    monkeypatch.setattr(app_state, "geocoder", FakeGeocoder())
    results = (await client.get("/api/geocode", params={"q": "Goslar Bahnhof"})).json()["results"]
    assert results[0] == {
        "name": "Goslar Bahnhof",
        "label": "Stop",
        "lat": 51.9086,
        "lon": 10.4283,
        "kind": "stop",
    }
    assert results[-1]["name"] == "Rammelsberg"
    assert (await client.get("/api/geocode", params={"q": "Go"})).json()["results"] == []


async def test_departures_board_from_the_regions_timetable(client, monkeypatch):
    from datetime import datetime

    import app.api as api_module

    class Monday(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.fromisoformat(MONDAY_0710).replace(tzinfo=tz)

    monkeypatch.setattr(api_module, "datetime", Monday)
    board = (
        await client.get("/api/departures", params={"lat": 51.9086, "lon": 10.4283, "lang": "de"})
    ).json()
    assert board["source"] == "fastmile" and board["stop"]["name"] == "Goslar Bahnhof"
    times = [d["time"] for d in board["departures"]]
    assert times and times == sorted(times)
    assert all(d["line_name"] and d["headsign"] for d in board["departures"])


async def test_plan_messages_in_german(client):
    payload = {
        **GOSLAR_ALTSTADT,
        **TU_CLAUSTHAL,
        "departure_time": "2026-10-05T03:00:00",
        "lang": "de",
    }
    body = (await client.post("/api/routes/plan", json=payload)).json()
    assert body["routes"] or body["message"].startswith("Keine Verbindung")
