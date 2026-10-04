"""GTFS-Realtime: per-trip delays and cancellations (TripUpdates) and service alerts.

Needs GTFS_RT_URL; without it, routing uses the static timetable only. The free nationwide feed (https://realtime.gtfs.de/realtime-free.pb)
is ~50 MB, so it is parsed in a worker thread and refreshed every few minutes.
"""

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)

# GTFS-RT Alert.Effect names → FastMile disruption effect (see llm.extraction.Effect).
# None = informational only; anything missing here is left for the LLM to read.
ALERT_EFFECTS: dict[str, str | None] = {
    "NO_SERVICE": "suspended",
    "SIGNIFICANT_DELAYS": "delayed",
    "DETOUR": "stops_not_served",
    "STOP_MOVED": "stops_not_served",
    "NO_EFFECT": None,
    "ACCESSIBILITY_ISSUE": None,
}


@dataclass
class ServiceAlert:
    key: str  # Stable hash of the alert, for de-duplication across fetches
    header: str
    description: str
    cause: str
    effect: str  # GTFS-RT effect name, e.g. "NO_SERVICE"
    route_ids: set[str] = field(default_factory=set)
    stop_ids: set[str] = field(default_factory=set)
    trip_ids: set[str] = field(default_factory=set)
    periods: list[tuple[int | None, int | None]] = field(default_factory=list)  # epoch seconds

    @property
    def text(self) -> str:
        return f"{self.header}\n{self.description}".strip()


@dataclass
class RealtimeState:
    delays: dict[str, int] = field(default_factory=dict)  # trip_id → delay seconds
    canceled: set[str] = field(default_factory=set)  # trip_ids
    alerts: list[ServiceAlert] = field(default_factory=list)
    fetched_at: float = 0.0
    error: str | None = None


def _text(translated: object) -> str:
    translations = list(getattr(translated, "translation", []))
    german = [t.text for t in translations if t.language in ("de", "")]
    return (german or [t.text for t in translations] or [""])[0].strip()


def parse_feed(payload: bytes) -> RealtimeState:
    """Parse TripUpdates (latest known delay per trip) and Alerts."""
    from google.transit import gtfs_realtime_pb2  # type: ignore[import-untyped]

    message = gtfs_realtime_pb2.FeedMessage()
    message.ParseFromString(payload)
    # The timestamp keys the network cache, so it must change with every fetch
    state = RealtimeState(fetched_at=float(message.header.timestamp) or time.time())
    canceled = gtfs_realtime_pb2.TripDescriptor.ScheduleRelationship.Value("CANCELED")
    effect_name = gtfs_realtime_pb2.Alert.Effect.Name
    cause_name = gtfs_realtime_pb2.Alert.Cause.Name
    for entity in message.entity:
        if entity.HasField("trip_update"):
            update = entity.trip_update
            trip_id = update.trip.trip_id
            if update.trip.schedule_relationship == canceled:
                state.canceled.add(trip_id)
                continue
            if update.HasField("delay"):
                state.delays[trip_id] = update.delay
            for stu in update.stop_time_update:
                event = stu.departure if stu.HasField("departure") else stu.arrival
                if event.HasField("delay"):
                    state.delays[trip_id] = event.delay  # Latest stop update wins
        elif entity.HasField("alert"):
            a = entity.alert
            header, description = _text(a.header_text), _text(a.description_text)
            alert = ServiceAlert(
                key=hashlib.sha1(f"{entity.id}|{header}|{description}".encode()).hexdigest()[:16],
                header=header,
                description=description,
                cause=cause_name(a.cause) if a.HasField("cause") else "UNKNOWN_CAUSE",
                effect=effect_name(a.effect) if a.HasField("effect") else "UNKNOWN_EFFECT",
                periods=[
                    (p.start if p.HasField("start") else None, p.end if p.HasField("end") else None)
                    for p in a.active_period
                ],
            )
            for ie in a.informed_entity:
                if ie.route_id:
                    alert.route_ids.add(ie.route_id)
                if ie.stop_id:
                    alert.stop_ids.add(ie.stop_id)
                if ie.HasField("trip") and ie.trip.trip_id:
                    alert.trip_ids.add(ie.trip.trip_id)
            state.alerts.append(alert)
    return state


async def fetch_realtime(url: str, timeout: float = 60.0) -> RealtimeState:
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()
        return await asyncio.to_thread(parse_feed, response.content)
    except ImportError:
        return RealtimeState(error="gtfs-realtime-bindings not installed")
    except Exception as exc:  # noqa: BLE001 — a bad feed must never take routing down
        logger.warning("GTFS-RT fetch failed: %s", exc)
        return RealtimeState(error=str(exc))
