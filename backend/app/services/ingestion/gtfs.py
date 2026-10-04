"""GTFS static feed loader.

Reads a GTFS feed from a directory or .zip into pandas DataFrames and resolves
which services run on a given date. The routing network is built from this
(see app.services.routing.network).
"""

import zipfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import IO

import pandas as pd

# GTFS route_type → FastMile mode. Covers basic and extended (HVT) route types.
_BASIC_ROUTE_TYPES = {
    0: "tram",
    1: "subway",
    2: "train",
    3: "bus",
    4: "ferry",
    5: "tram",
    6: "gondola",
    7: "funicular",
    11: "bus",
    12: "train",
}


def route_type_to_mode(route_type: int) -> str:
    if route_type in _BASIC_ROUTE_TYPES:
        return _BASIC_ROUTE_TYPES[route_type]
    if 100 <= route_type < 200:
        return "train"
    if 400 <= route_type < 500:
        return "subway"
    if 700 <= route_type < 800:
        return "on_demand" if route_type == 715 else "bus"
    if 900 <= route_type < 1000:
        return "tram"
    if route_type == 1500:
        return "on_demand"
    return "bus"


def parse_gtfs_time(value: str) -> int:
    """Parse a GTFS HH:MM:SS time (may exceed 24:00) into seconds since service-day midnight."""
    h, m, s = value.strip().split(":")
    return int(h) * 3600 + int(m) * 60 + int(s)


def _parse_time_column(values: pd.Series) -> pd.Series:
    """Vectorized parse_gtfs_time for a whole column (stop_times can have millions of rows)."""
    parts = values.str.strip().str.split(":", expand=True).astype(int)
    return parts[0] * 3600 + parts[1] * 60 + parts[2]


@dataclass
class GtfsFeed:
    """Parsed GTFS tables. Times in stop_times are seconds since service-day midnight."""

    stops: pd.DataFrame
    routes: pd.DataFrame
    trips: pd.DataFrame
    stop_times: pd.DataFrame
    calendar: pd.DataFrame | None
    calendar_dates: pd.DataFrame | None
    source: str
    attribution: str | None = None  # Who to credit, from attributions.txt / feed_info.txt

    def active_service_ids(self, day: date) -> set[str]:
        """Service IDs running on `day`, applying calendar_dates exceptions."""
        active: set[str] = set()
        ymd = day.strftime("%Y%m%d")
        if self.calendar is not None and not self.calendar.empty:
            weekday = day.strftime("%A").lower()
            cal = self.calendar
            running = cal[
                (cal[weekday] == "1") & (cal["start_date"] <= ymd) & (cal["end_date"] >= ymd)
            ]
            active.update(running["service_id"])
        if self.calendar_dates is not None and not self.calendar_dates.empty:
            exceptions = self.calendar_dates[self.calendar_dates["date"] == ymd]
            active.update(exceptions[exceptions["exception_type"] == "1"]["service_id"])
            active.difference_update(exceptions[exceptions["exception_type"] == "2"]["service_id"])
        return active


def _read_tables(path: Path) -> dict[str, pd.DataFrame]:
    names = [
        "agency",
        "stops",
        "routes",
        "trips",
        "stop_times",
        "calendar",
        "calendar_dates",
        "attributions",
        "feed_info",
    ]
    tables: dict[str, pd.DataFrame] = {}

    def read(buffer: IO[bytes] | Path) -> pd.DataFrame:
        df = pd.read_csv(buffer, dtype=str, keep_default_na=False, encoding="utf-8-sig")
        df.columns = [c.strip() for c in df.columns]
        return df

    if path.is_dir():
        for name in names:
            file = path / f"{name}.txt"
            if file.exists():
                tables[name] = read(file)
    else:
        with zipfile.ZipFile(path) as zf:
            members = {Path(m).name: m for m in zf.namelist()}
            for name in names:
                if f"{name}.txt" in members:
                    with zf.open(members[f"{name}.txt"]) as f:
                        tables[name] = read(f)
    return tables


def load_gtfs(path: str | Path) -> GtfsFeed:
    """Load a GTFS feed from a directory or zip file."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"GTFS feed not found: {path}")
    tables = _read_tables(path)
    for required in ("stops", "routes", "trips", "stop_times"):
        if required not in tables:
            raise ValueError(f"GTFS feed {path} is missing {required}.txt")

    stops = tables["stops"]
    # Entrances, generic nodes and boarding areas may have no coordinates; they are never routed
    stops["stop_lat"] = pd.to_numeric(stops["stop_lat"], errors="coerce")
    stops["stop_lon"] = pd.to_numeric(stops["stop_lon"], errors="coerce")
    stops = stops.dropna(subset=["stop_lat", "stop_lon"]).reset_index(drop=True)
    if "parent_station" not in stops:
        stops["parent_station"] = ""

    routes = tables["routes"]
    for col in ("route_short_name", "route_long_name", "route_color"):
        if col not in routes:
            routes[col] = ""
    routes["route_type"] = routes["route_type"].astype(int)
    routes["mode"] = routes["route_type"].map(route_type_to_mode)
    agency = tables.get("agency")
    operators = (
        dict(zip(agency["agency_id"], agency["agency_name"], strict=True))
        if agency is not None and "agency_id" in agency
        else {}
    )
    routes["agency_name"] = (
        routes["agency_id"].map(operators).fillna("") if "agency_id" in routes else ""
    )

    stop_times = tables["stop_times"]
    # Some feeds leave intermediate times blank; drop those rows (no interpolation here).
    stop_times = stop_times[
        (stop_times["arrival_time"] != "") & (stop_times["departure_time"] != "")
    ]
    stop_times = stop_times.assign(
        arrival=_parse_time_column(stop_times["arrival_time"]),
        departure=_parse_time_column(stop_times["departure_time"]),
        stop_sequence=stop_times["stop_sequence"].astype(int),
        # 2 = phone the operator to book (call-ahead taxis); kept for the planner
        pickup_type=stop_times["pickup_type"] if "pickup_type" in stop_times else "",
    )[["trip_id", "stop_id", "stop_sequence", "arrival", "departure", "pickup_type"]]
    stop_times = stop_times.sort_values(["trip_id", "stop_sequence"]).reset_index(drop=True)

    attribution = None
    if "attributions" in tables and "organization_name" in tables["attributions"]:
        orgs = [o.strip() for o in tables["attributions"]["organization_name"] if o.strip()]
        attribution = ", ".join(dict.fromkeys(orgs)) or None
    elif "feed_info" in tables and "feed_publisher_name" in tables["feed_info"]:
        attribution = tables["feed_info"]["feed_publisher_name"].iloc[0] or None

    return GtfsFeed(
        attribution=attribution,
        stops=stops,
        routes=routes,
        trips=tables["trips"],
        stop_times=stop_times,
        calendar=tables.get("calendar"),
        calendar_dates=tables.get("calendar_dates"),
        source=str(path),
    )
