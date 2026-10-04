"""GTFS-Realtime parsing and automatic disruption ingestion from service alerts."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from google.transit import gtfs_realtime_pb2

from app.services.alerts import regional_alerts, sync_alerts
from app.services.ingestion.gtfs_rt import parse_feed
from app.state import state
from tests.conftest import FakeSession

TZ = ZoneInfo("Europe/Berlin")
START = int(datetime(2026, 10, 5, 7, 0, tzinfo=TZ).timestamp())
END = int(datetime(2026, 10, 5, 10, 0, tzinfo=TZ).timestamp())
E = gtfs_realtime_pb2.Alert


def build_feed() -> bytes:
    msg = gtfs_realtime_pb2.FeedMessage()
    msg.header.gtfs_realtime_version = "2.0"
    msg.header.timestamp = START

    def alert(eid, header, effect=None, route=None, stop=None, trip=None, text=None):
        e = msg.entity.add(id=eid)
        if effect is not None:
            e.alert.effect = effect
        e.alert.header_text.translation.add(text=header, language="de")
        if text:
            e.alert.description_text.translation.add(text=text, language="de")
        ie = e.alert.informed_entity.add()
        if route:
            ie.route_id = route
        if stop:
            ie.stop_id = stop
        if trip:
            ie.trip.trip_id = trip
        p = e.alert.active_period.add()
        p.start, p.end = START, END

    up = msg.entity.add(id="u1").trip_update
    up.trip.trip_id = "r830_0_003"
    up.stop_time_update.add(stop_sequence=2).departure.delay = 240
    cancel = msg.entity.add(id="u2").trip_update
    cancel.trip.trip_id = "r810_0_001"
    cancel.trip.schedule_relationship = gtfs_realtime_pb2.TripDescriptor.CANCELED

    alert(
        "a1",
        "Linie 830 gesperrt",
        E.NO_SERVICE,
        route="r830",
        text="Unfall zwischen Goslar und Hahnenklee.",
    )
    alert("a2", "Niederflur", route="r830")  # Vehicle feature, not a disruption
    alert("a3", "Haltestelle verlegt", E.NO_EFFECT, stop="GS_MARKT")  # Informational
    alert("a4", "Fahrt fällt aus", E.NO_SERVICE, trip="r840_0_002")  # Trip-only
    alert("a5", "Stuttgart: Bauarbeiten", E.DETOUR, route="somewhere_else")  # Not regional
    alert(
        "a6", "Wegen einer Veranstaltung wird Linie 11 umgeleitet", stop="GS_RATHAUS"
    )  # Unknown effect
    return msg.SerializeToString()


def test_parse_feed_reads_delays_cancellations_and_alerts():
    rt = parse_feed(build_feed())
    assert rt.delays == {"r830_0_003": 240}
    assert rt.canceled == {"r810_0_001"}
    assert len(rt.alerts) == 6
    a1 = rt.alerts[0]
    assert a1.effect == "NO_SERVICE" and a1.route_ids == {"r830"}
    assert a1.periods == [(START, END)] and "Unfall" in a1.text


def test_only_regional_disruptions_are_kept():
    net = state.network_for(datetime(2026, 10, 5).date())
    kept = regional_alerts(parse_feed(build_feed()).alerts, net)
    assert [a.header for a in kept] == [
        "Linie 830 gesperrt",
        "Wegen einer Veranstaltung wird Linie 11 umgeleitet",
    ]


async def test_sync_creates_then_resolves_disruptions():
    net = state.network_for(datetime(2026, 10, 5).date())
    session = FakeSession()
    alerts = parse_feed(build_feed()).alerts

    first = await sync_alerts(session, alerts, net, llm=None, tz=TZ)
    assert (first.regional, first.created, first.resolved) == (2, 2, 0)
    by_header = {r.raw_text.splitlines()[0]: r for r in session.rows}
    closed = by_header["Linie 830 gesperrt"]
    assert closed.effect == "suspended" and closed.affected_routes == '["r830"]'
    assert closed.extraction_method == "gtfs-rt"  # Effect came from the feed, no LLM call
    assert closed.start_time == datetime(2026, 10, 5, 7, 0) and closed.end_time == datetime(
        2026, 10, 5, 10, 0
    )
    assert closed.severity == "high"
    detour = by_header["Wegen einer Veranstaltung wird Linie 11 umgeleitet"]
    assert detour.extraction_method == "rules"  # Unknown effect: the text was read
    assert detour.effect == "stops_not_served" and detour.affected_stops == '["GS_RATHAUS"]'

    again = await sync_alerts(session, alerts, net, llm=None, tz=TZ)
    assert again.created == 0  # De-duplicated

    gone = await sync_alerts(
        session, [a for a in alerts if a.header != "Linie 830 gesperrt"], net, None, TZ
    )
    assert gone.resolved == 1 and closed.status == "resolved"


def test_lift_outages_and_far_away_stations_are_not_regional_disruptions():
    from app.services.alerts import regional_alerts
    from app.services.ingestion.gtfs_rt import ServiceAlert

    net = state.network_for(date(2026, 10, 5))
    stop = net.stop_ids[0]
    lat, lon = net.stop_coords(0)
    lift = ServiceAlert(
        "k1",
        "Der Aufzug zu Gleis 3 ist außer Betrieb",
        "",
        "OTHER_CAUSE",
        "OTHER_EFFECT",
        stop_ids={stop},
    )
    closure = ServiceAlert(
        "k2", "Bahnhof gesperrt", "Polizeieinsatz", "POLICE_ACTIVITY", "NO_SERVICE", stop_ids={stop}
    )
    assert [a.key for a in regional_alerts([lift, closure], net)] == ["k2"]
    elsewhere = (lat + 1, lon + 1, lat + 2, lon + 2)  # The stop lies outside this region
    assert regional_alerts([closure], net, elsewhere) == []
