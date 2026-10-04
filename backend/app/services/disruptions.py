"""Disruption handling: match extracted reports to the network and block them in routing.

The special case from the brief: a report is read (llm.extraction), matched to
lines and stops here, and turned into CSA blocking so replanning avoids it.
"""

import difflib
import json
import logging
import re
import unicodedata
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import numpy as np

from app.models import Disruption
from app.services.decisions import JevError, classify_disruption_severity, get_jev_client
from app.services.optimisation.check import DisruptionImpact
from app.services.routing.csa import Blocking
from app.services.routing.network import TransitNetwork, normalize_line

logger = logging.getLogger(__name__)

SEVERITY_BY_EFFECT = {
    "suspended": "high",
    "station_closed": "high",
    "stops_not_served": "medium",
    "delayed": "low",
    "reduced_availability": "low",
    "other": "medium",
}


async def rate_severity(
    text: str, lines: list[str], effect: str, delay_minutes: int | None
) -> tuple[str, float | None, float | None]:
    """(severity, Jev confidence, P(needs reroute)): Jev when configured, else from the effect."""
    severity = SEVERITY_BY_EFFECT.get(effect, "medium")
    if effect == "delayed" and (delay_minutes or 0) >= 15:
        severity = "medium"
    jev = get_jev_client()
    if jev is None:
        return severity, None, None
    try:
        decision = await classify_disruption_severity(jev, text, lines)
    except JevError as exc:
        logger.warning("Jev classification failed, using effect-based severity: %s", exc)
        return severity, None, None
    answer = decision.get_answer("severity")
    if answer is not None:
        severity = str(answer.value)
    return severity, answer.confidence if answer else None, decision.get_noul("needs_reroute")


_ABBREVIATIONS = [
    (r"\bhauptbahnhof\b", "hbf"),
    (r"\bbahnhof\b", "bf"),
    (r"\bzentraler omnibusbahnhof\b", "zob"),
    (r"\bbusbahnhof\b", "zob"),
    (r"\bstrasse\b", "str"),
    (r"\bstraße\b", "str"),
    (r"\bplatz\b", "pl"),
]


def normalize_stop(name: str) -> str:
    name = name.lower().replace("ß", "ss")
    for pattern, short in _ABBREVIATIONS:
        name = re.sub(pattern, short, name)
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", name).strip()


def match_lines(net: TransitNetwork, lines: list[str]) -> list[str]:
    route_ids: list[str] = []
    for line in lines:
        route_ids += [net.route_ids[r] for r in net.routes_for_line(line)]
    return list(dict.fromkeys(route_ids))


MAX_CONTAINMENT_HITS = 3  # More matches than this means a town or area name, not a stop


def match_stops(net: TransitNetwork, names: list[str], cutoff: float = 0.8) -> list[str]:
    """Match stop names from a report to stop IDs (exact, then containment, then fuzzy).

    A bare town name ("Goslar") would contain-match every stop in town and block them all,
    so containment matches that hit more than a few stops are discarded as ambiguous.
    """
    normalized = [normalize_stop(n) for n in net.stop_names]
    by_name: dict[str, list[int]] = {}
    for i, n in enumerate(normalized):
        by_name.setdefault(n, []).append(i)
    found: list[str] = []
    for raw in names:
        query = normalize_stop(raw)
        if not query:
            continue
        hits = by_name.get(query)
        if not hits:
            # "Marktplatz" in the report vs "Goslar Marktplatz" in GTFS
            hits = [
                i
                for i, n in enumerate(normalized)
                if re.search(rf"(^| ){re.escape(query)}( |$)", n)
            ]
            if len(hits) > MAX_CONTAINMENT_HITS:
                continue
        if not hits:
            close = difflib.get_close_matches(query, list(by_name), n=1, cutoff=cutoff)
            hits = by_name[close[0]] if close else []
        found += [net.stop_ids[i] for i in hits]
    return list(dict.fromkeys(found))


def window(
    report_time: datetime, start_hhmm: str | None, end_hhmm: str | None
) -> tuple[datetime, datetime | None]:
    """Turn HH:MM strings from the report into datetimes on the report's day."""

    def on_day(hhmm: str) -> datetime:
        h, m = (int(x) for x in hhmm.split(":"))
        return datetime.combine(report_time.date(), time(h, m), tzinfo=report_time.tzinfo)

    start = on_day(start_hhmm) if start_hhmm else report_time
    if start - report_time > timedelta(hours=12):
        start -= timedelta(days=1)  # Filed at 07:00 saying "seit 22:00": that was last night
    end = on_day(end_hhmm) if end_hhmm else None
    if end is not None and end <= start:
        end += timedelta(days=1)  # "22:00 bis 02:00"
    return start, end


def impact_from_row(row: Disruption, tz: ZoneInfo) -> DisruptionImpact:
    """DB row → impact. Times are stored as naive local time."""

    def local(dt: datetime | None) -> datetime | None:
        return dt.replace(tzinfo=tz) if dt is not None and dt.tzinfo is None else dt

    lines = {normalize_line(x) for x in json.loads(row.affected_lines or "[]")}
    proposed = {normalize_line(x) for x in json.loads(row.alternative_lines or "[]")}
    return DisruptionImpact(
        id=row.id,
        summary=row.summary or row.raw_text[:120],
        severity=row.severity,
        route_ids=set(json.loads(row.affected_routes or "[]")),
        stop_ids=set(json.loads(row.affected_stops or "[]")),
        start=local(row.start_time),
        end=local(row.end_time),
        effect=row.effect or "other",
        delay_minutes=row.delay_minutes,
        lines=lines,
        recommended_lines=proposed - lines,  # A report never recommends the line it closes
    )


def attach_stop_points(impacts: list[DisruptionImpact], net: TransitNetwork) -> None:
    """Give each impact its stops' coordinates, to match legs planned from another feed."""
    for impact in impacts:
        impact.stop_points = [
            net.stop_coords(net.stop_index[s]) for s in impact.stop_ids if s in net.stop_index
        ]


def blocking_for(net: TransitNetwork, impacts: list[DisruptionImpact], tz: ZoneInfo) -> Blocking:
    """Block connections on affected lines and boarding/alighting at affected stops, per window."""
    d: date = net.service_date
    midnight = datetime(d.year, d.month, d.day, tzinfo=tz)
    horizon = 48 * 3600

    def seconds(dt: datetime | None, default: int) -> int:
        return default if dt is None else int((dt - midnight).total_seconds())

    mask = np.zeros(net.n_connections, dtype=bool)
    stop_windows: dict[int, list[tuple[int, int]]] = {}
    for impact in impacts:
        start, end = seconds(impact.start, -horizon), seconds(impact.end, horizon)
        routes = [net.route_index[r] for r in impact.blocked_routes if r in net.route_index]
        if routes:
            mask |= np.isin(net.c_route, routes) & (net.c_arr >= start) & (net.c_dep <= end)
        for stop_id in impact.blocked_stops:
            if stop_id in net.stop_index:
                stop_windows.setdefault(net.stop_index[stop_id], []).append((start, end))
    return Blocking(conn_mask=mask if mask.any() else None, stop_windows=stop_windows)
