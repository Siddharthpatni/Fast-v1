"""Time-expanded transit network for one service day, built from a GTFS feed.

Connections (one vehicle hop between consecutive stops) are stored in parallel
arrays sorted by departure time, which is the layout the Connection Scan
Algorithm needs (see csa.py).
"""

import math
import re
from collections import defaultdict
from datetime import date

import numpy as np

from app.services.ingestion.gtfs import GtfsFeed
from app.services.ingestion.gtfs_rt import RealtimeState
from app.services.routing.modes import TRANSFER_WALK_RADIUS_KM, WALK
from app.utils.geo import haversine_np


def normalize_line(name: str) -> str:
    """'RE 10' / 're10' / 'Linie 830' → 'RE10' / 'RE10' / '830'."""
    name = re.sub(r"(?i)^(buslinie|linie|line|bus|tram)\s*", "", name.strip())
    return re.sub(r"[\s\-_]", "", name).upper()


class TransitNetwork:
    def __init__(
        self,
        feed: GtfsFeed,
        service_date: date,
        realtime: RealtimeState | None = None,
        transfer_radius_km: float = TRANSFER_WALK_RADIUS_KM,
    ) -> None:
        self.service_date = service_date
        self.source = feed.source

        # ── Stops ──
        stops = feed.stops
        if "location_type" in stops:
            stops = stops[stops["location_type"].isin(["", "0"])]
        stops = stops.reset_index(drop=True)
        self.stop_ids: list[str] = stops["stop_id"].tolist()
        self.stop_names: list[str] = stops["stop_name"].tolist()
        self.stop_platform: list[str] = (
            stops["platform_code"].tolist() if "platform_code" in stops else [""] * len(stops)
        )
        self.stop_lats = stops["stop_lat"].to_numpy(dtype=float)
        self.stop_lons = stops["stop_lon"].to_numpy(dtype=float)
        self.stop_index = {sid: i for i, sid in enumerate(self.stop_ids)}

        # ── Routes ──
        routes = feed.routes.reset_index(drop=True)
        self.route_ids: list[str] = routes["route_id"].tolist()
        self.route_short: list[str] = [
            s or long
            for s, long in zip(routes["route_short_name"], routes["route_long_name"], strict=True)
        ]
        self.route_long: list[str] = routes["route_long_name"].tolist()
        self.route_mode: list[str] = routes["mode"].tolist()
        self.route_agency: list[str] = (
            [a or "" for a in routes["agency_name"]]
            if "agency_name" in routes
            else [""] * len(routes)
        )
        self.route_color: list[str | None] = [f"#{c}" if c else None for c in routes["route_color"]]
        self.route_index = {rid: i for i, rid in enumerate(self.route_ids)}
        self._routes_by_line: dict[str, list[int]] = defaultdict(list)
        for i, short in enumerate(self.route_short):
            self._routes_by_line[normalize_line(short)].append(i)

        # ── Trips running today ──
        services = feed.active_service_ids(service_date)
        trips = feed.trips[feed.trips["service_id"].isin(services)]
        if realtime:
            trips = trips[~trips["trip_id"].isin(realtime.canceled)]
        trips = trips[trips["route_id"].isin(self.route_index)].reset_index(drop=True)
        self.trip_ids: list[str] = trips["trip_id"].tolist()
        self.trip_route = np.array([self.route_index[r] for r in trips["route_id"]], dtype=np.int64)
        headsigns = trips["trip_headsign"] if "trip_headsign" in trips else [""] * len(trips)
        self.trip_headsign: list[str] = list(headsigns)
        trip_index = {tid: i for i, tid in enumerate(self.trip_ids)}

        # ── Connections ──
        st = feed.stop_times
        st = st[st["trip_id"].isin(trip_index) & st["stop_id"].isin(self.stop_index)]
        # Trips that run only when booked by phone (GTFS pickup_type 2), e.g. AST call-ahead taxis
        self.trip_on_request: set[int] = (
            {trip_index[t] for t in st.loc[st["pickup_type"] == "2", "trip_id"].unique()}
            if "pickup_type" in st
            else set()
        )
        trip_idx = st["trip_id"].map(trip_index).to_numpy(dtype=np.int64)
        stop_idx = st["stop_id"].map(self.stop_index).to_numpy(dtype=np.int64)
        arr = st["arrival"].to_numpy(dtype=np.int64)
        dep = st["departure"].to_numpy(dtype=np.int64)
        # Live delay (seconds) of trips the realtime feed reports on; missing = no live data
        self.trip_delay: dict[int, int] = {}
        if realtime and realtime.delays:
            delay_by_trip = np.zeros(len(self.trip_ids), dtype=np.int64)
            for tid, delay in realtime.delays.items():
                if tid in trip_index:
                    delay_by_trip[trip_index[tid]] = delay
                    self.trip_delay[trip_index[tid]] = int(delay)
            arr = arr + delay_by_trip[trip_idx]
            dep = dep + delay_by_trip[trip_idx]

        same_trip = trip_idx[:-1] == trip_idx[1:]  # stop_times are sorted by trip, sequence
        c_trip = trip_idx[:-1][same_trip]
        c_dep_stop = stop_idx[:-1][same_trip]
        c_arr_stop = stop_idx[1:][same_trip]
        c_dep = dep[:-1][same_trip]
        c_arr = arr[1:][same_trip]

        order = np.lexsort((c_arr, c_dep))
        self.c_trip = c_trip[order]
        self.c_dep_stop = c_dep_stop[order]
        self.c_arr_stop = c_arr_stop[order]
        self.c_dep = c_dep[order]
        self.c_arr = c_arr[order]
        self.c_route = (
            self.trip_route[self.c_trip] if len(self.c_trip) else np.array([], dtype=np.int64)
        )

        # Python lists for the scan loop (much faster element access than numpy scalars)
        self.l_trip: list[int] = self.c_trip.tolist()
        self.l_dep_stop: list[int] = self.c_dep_stop.tolist()
        self.l_arr_stop: list[int] = self.c_arr_stop.tolist()
        self.l_dep: list[int] = self.c_dep.tolist()
        self.l_arr: list[int] = self.c_arr.tolist()

        # Connections of each trip, in travel order
        by_trip = np.argsort(self.c_trip, kind="stable")
        bounds = np.searchsorted(self.c_trip[by_trip], np.arange(len(self.trip_ids) + 1))
        self.trip_conns: list[list[int]] = [
            by_trip[bounds[t] : bounds[t + 1]].tolist() for t in range(len(self.trip_ids))
        ]

        # Departures per stop: a cheap "hub" score for picking access stops
        self.stop_departures = np.bincount(self.c_dep_stop, minlength=len(self.stop_ids))

        self.footpaths = self._build_footpaths(transfer_radius_km)

    def _build_footpaths(self, radius_km: float) -> list[list[tuple[int, int]]]:
        """Walking transfers between nearby stops, via a coarse grid."""
        cell = radius_km / 111.0  # degrees latitude per cell
        if not len(self.stop_lats):
            return []
        max_lat = min(float(np.max(np.abs(self.stop_lats))), 85.0)
        lon_cells = math.ceil(1 / math.cos(math.radians(max_lat)))
        grid: dict[tuple[int, int], list[int]] = defaultdict(list)
        keys = [
            (int(la // cell), int(lo // cell))
            for la, lo in zip(self.stop_lats, self.stop_lons, strict=True)
        ]
        for i, key in enumerate(keys):
            grid[key].append(i)
        footpaths: list[list[tuple[int, int]]] = [[] for _ in self.stop_ids]
        for i, (ky, kx) in enumerate(keys):
            cand = [
                j
                for dy in (-1, 0, 1)
                for dx in range(-lon_cells, lon_cells + 1)
                for j in grid.get((ky + dy, kx + dx), ())
                if j != i
            ]
            if not cand:
                continue
            idx = np.array(cand)
            d = haversine_np(
                self.stop_lats[i], self.stop_lons[i], self.stop_lats[idx], self.stop_lons[idx]
            )
            for j, km in zip(idx[d <= radius_km].tolist(), d[d <= radius_km].tolist(), strict=True):
                footpaths[i].append((j, max(60, int(WALK.minutes(km) * 60))))
        return footpaths

    # ── Lookups ──

    @property
    def n_connections(self) -> int:
        return len(self.l_dep)

    def nearby_stops(self, lat: float, lon: float, radius_km: float) -> list[tuple[int, float]]:
        """Stops within radius_km, nearest first."""
        if not self.stop_ids:
            return []
        d = haversine_np(lat, lon, self.stop_lats, self.stop_lons)
        idx = np.nonzero(d <= radius_km)[0]
        idx = idx[np.argsort(d[idx])]
        return [(int(i), float(d[i])) for i in idx]

    def routes_for_line(self, line: str) -> list[int]:
        return self._routes_by_line.get(normalize_line(line), [])

    def stop_coords(self, stop: int) -> tuple[float, float]:
        return float(self.stop_lats[stop]), float(self.stop_lons[stop])
