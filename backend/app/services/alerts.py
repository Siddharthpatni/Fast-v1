"""Automatic disruption ingestion from GTFS-Realtime service alerts.

Replaces the manual paste for official alerts: every refresh, alerts that touch
the regional network become stored disruptions, which routing then avoids.

- The alert's own route/stop IDs and active periods are used directly; they are
  exact, unlike names read from text.
- Its GTFS-RT effect decides what is blocked. When the effect is unknown, the
  LLM (or the rule-based fallback) reads the alert text to classify it.
- Jev rates severity, as for manual reports.
- Alerts that disappear from the feed are marked resolved.
"""

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Disruption
from app.services.disruptions import match_lines, rate_severity
from app.services.ingestion.gtfs_rt import ALERT_EFFECTS, ServiceAlert
from app.services.llm import extract_disruption
from app.services.routing.network import TransitNetwork

logger = logging.getLogger(__name__)

SOURCE_PREFIX = "gtfs-rt:"
MAX_NEW_PER_SYNC = 20  # Bounds LLM and Jev calls per refresh
# Feeds attach vehicle features and attributions as "alerts"; they are not disruptions
_NOT_A_DISRUPTION = re.compile(
    r"(?i)^(niederflur|rollstuhl|gelenkbus|stadtbahn|fahrradmitnahme|klimatisiert|"
    r"echtzeitdaten aufbereitet)"
)
# Lift and escalator outages: they matter for step-free travel, which FastMile doesn't plan
# yet, but they never stop a train or bus, so they must not block a station
_STATION_EQUIPMENT = re.compile(
    r"(?i)\b(aufzug|fahrstuhl|fahrtreppe|rolltreppe|elevator|escalator|lift)\b"
)


@dataclass
class SyncResult:
    regional: int = 0
    created: int = 0
    resolved: int = 0
    skipped_by_limit: int = 0


def regional_alerts(
    alerts: list[ServiceAlert],
    net: TransitNetwork,
    region: tuple[float, float, float, float] | None = None,
) -> list[ServiceAlert]:
    """Alerts that name a route or stop of the loaded network and describe a disruption.

    Alerts that only name individual trips are left out: blocking their whole line would
    over-block, and trip cancellations already arrive as TripUpdates. With a `region`
    (south, west, north, east), a stop counts only inside it: long-distance trips bring
    their far-away stops (Munich, Nuremberg) into the network too.
    """

    def in_region(stop_id: str) -> bool:
        if stop_id not in net.stop_index:
            return False
        if region is None:
            return True
        lat, lon = net.stop_coords(net.stop_index[stop_id])
        return region[0] <= lat <= region[2] and region[1] <= lon <= region[3]

    out = []
    for alert in alerts:
        if alert.effect in ALERT_EFFECTS and ALERT_EFFECTS[alert.effect] is None:
            continue  # Explicitly informational (NO_EFFECT, accessibility notes)
        if not alert.text or _NOT_A_DISRUPTION.match(alert.text):
            continue
        if _STATION_EQUIPMENT.search(alert.header or alert.text):
            continue
        if alert.stop_ids:
            if any(in_region(s) for s in alert.stop_ids):
                out.append(alert)
        elif any(r in net.route_index for r in alert.route_ids):
            out.append(alert)
    return out


def _period(alert: ServiceAlert, tz: ZoneInfo) -> tuple[datetime | None, datetime | None]:
    """Overall window as naive local times (how disruptions are stored)."""

    def local(epoch: int | None) -> datetime | None:
        if not epoch:
            return None
        return datetime.fromtimestamp(epoch, tz).replace(tzinfo=None)

    starts = [local(s) for s, _ in alert.periods]
    ends = [local(e) for _, e in alert.periods]
    start = min((s for s in starts if s), default=None)
    end = None if not ends or any(e is None for e in ends) else max(e for e in ends if e)
    return start, end


async def sync_alerts(
    session: AsyncSession,
    alerts: list[ServiceAlert],
    net: TransitNetwork,
    llm: object | None,
    tz: ZoneInfo,
    region: tuple[float, float, float, float] | None = None,
) -> SyncResult:
    result = SyncResult()
    relevant = regional_alerts(alerts, net, region)
    result.regional = len(relevant)
    keys = {SOURCE_PREFIX + a.key for a in relevant}

    rows = await session.execute(
        select(Disruption).where(
            Disruption.source.like(f"{SOURCE_PREFIX}%"), Disruption.status == "active"
        )
    )
    active = {row.source: row for row in rows.scalars().all()}
    now = datetime.now(tz).replace(tzinfo=None)
    for source, row in active.items():
        if source not in keys:  # The operator withdrew the alert
            row.status = "resolved"
            row.end_time = row.end_time or now
            result.resolved += 1

    new = [a for a in relevant if SOURCE_PREFIX + a.key not in active]
    result.skipped_by_limit = max(0, len(new) - MAX_NEW_PER_SYNC)
    for alert in new[:MAX_NEW_PER_SYNC]:
        route_ids = sorted(r for r in alert.route_ids if r in net.route_index)
        stop_ids = sorted(s for s in alert.stop_ids if s in net.stop_index)
        effect = ALERT_EFFECTS.get(alert.effect)
        extraction = None
        if effect is None:  # Unknown/other effect: read the text
            extraction = await extract_disruption(llm, alert.text, net.stop_names)  # type: ignore[arg-type]
            effect = extraction.effect
            if not route_ids:
                route_ids = match_lines(net, extraction.affected_lines)
        lines = sorted({net.route_short[net.route_index[r]] for r in route_ids})
        severity, confidence, needs_reroute = await rate_severity(
            alert.text, lines, effect, extraction.delay_minutes if extraction else None
        )
        start, end = _period(alert, tz)
        session.add(
            Disruption(
                raw_text=alert.text[:4000],
                source=SOURCE_PREFIX + alert.key,
                status="active",
                summary=(extraction.summary if extraction else alert.header)[:500] or None,
                severity=severity,
                confidence=confidence,
                needs_reroute=needs_reroute,
                effect=effect,
                disruption_type=extraction.disruption_type if extraction else None,
                delay_minutes=extraction.delay_minutes if extraction else None,
                extraction_method=(extraction.method if extraction else "gtfs-rt"),
                affected_lines=json.dumps(lines, ensure_ascii=False),
                affected_routes=json.dumps(route_ids),
                affected_stops=json.dumps(stop_ids),
                alternatives=json.dumps(
                    extraction.alternatives if extraction else [], ensure_ascii=False
                ),
                alternative_lines=json.dumps(
                    extraction.alternative_lines if extraction else [], ensure_ascii=False
                ),
                start_time=start or now,
                end_time=end,
            )
        )
        result.created += 1
    await session.commit()
    return result
