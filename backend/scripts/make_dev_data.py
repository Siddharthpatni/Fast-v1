"""Generate the local development fixtures in data/.

    python backend/scripts/make_dev_data.py

Writes:
  data/gtfs_dev/         a small GTFS feed for the Goslar / Clausthal-Zellerfeld area
  data/gbfs_dev/         a GBFS v2.3 snapshot (bike + scooter + car-sharing)
  data/on_demand_zones.json

These are DEVELOPMENT FIXTURES with approximate stop locations and invented
timetables, so the stack runs without network access. They are not real data —
point GTFS_PATH / GBFS_FEEDS at the real feeds for evaluation (see README).
"""

import csv
import json
from pathlib import Path

DATA = Path(__file__).resolve().parents[2] / "data"

STOPS = {
    "GS_BF": ("Goslar Bahnhof", 51.9086, 10.4283),
    "GS_MARKT": ("Goslar Marktplatz", 51.9057, 10.4295),
    "GS_RATHAUS": ("Goslar Rathaus", 51.9062, 10.4306),
    "GS_JAKOBI": ("Goslar Jakobikirchplatz", 51.9071, 10.4262),
    "OKER": ("Oker Bahnhof", 51.9013, 10.4870),
    "BH_BF": ("Bad Harzburg Bahnhof", 51.8817, 10.5598),
    "TORF": ("Torfhaus", 51.8008, 10.5408),
    "ALT": ("Altenau Busbahnhof", 51.8017, 10.4458),
    "HK": ("Hahnenklee Kurhaus", 51.8566, 10.3398),
    "CZ_ZOB": ("Clausthal-Zellerfeld ZOB", 51.8098, 10.3376),
    "CZ_UNI": ("Clausthal Universität", 51.8044, 10.3378),
    "LANG": ("Langelsheim Bahnhof", 51.9378, 10.3336),
    "SZ_BAD": ("Salzgitter-Bad", 52.0480, 10.3730),
    "VIEN": ("Vienenburg Bahnhof", 51.9530, 10.5640),
    "BS_HBF": ("Braunschweig Hbf", 52.2525, 10.5397),
}

# route_id: (short_name, long_name, route_type, color, stops, minutes between stops,
#            headway_min, first "HH:MM", last "HH:MM")
LINES = {
    "r830": (
        "830",
        "Goslar – Hahnenklee – Clausthal-Zellerfeld",
        3,
        "E3000F",
        ["GS_BF", "GS_MARKT", "HK", "CZ_ZOB", "CZ_UNI"],
        [4, 18, 14, 4],
        30,
        "05:30",
        "21:00",
    ),
    "r810": (
        "810",
        "Goslar – Oker – Bad Harzburg",
        3,
        "0072BC",
        ["GS_BF", "OKER", "BH_BF"],
        [9, 14],
        30,
        "05:45",
        "21:30",
    ),
    "r840": (
        "840",
        "Bad Harzburg – Torfhaus – Altenau – Clausthal-Zellerfeld",
        3,
        "00A651",
        ["BH_BF", "TORF", "ALT", "CZ_ZOB"],
        [15, 12, 13],
        60,
        "06:00",
        "20:00",
    ),
    "r11": (
        "11",
        "Goslar Stadtverkehr",
        3,
        "F39200",
        ["GS_BF", "GS_MARKT", "GS_RATHAUS", "GS_JAKOBI"],
        [3, 2, 3],
        15,
        "06:00",
        "22:00",
    ),
    "rRE10": (
        "RE 10",
        "Goslar – Vienenburg – Braunschweig",
        2,
        "8C1D82",
        ["GS_BF", "VIEN", "BS_HBF"],
        [12, 35],
        60,
        "05:10",
        "22:10",
    ),
    "rRB43": (
        "RB 43",
        "Goslar – Langelsheim – Salzgitter-Bad",
        2,
        "5F6B6D",
        ["GS_BF", "LANG", "SZ_BAD"],
        [10, 18],
        60,
        "05:20",
        "21:20",
    ),
}


def hhmm_to_min(value: str) -> int:
    h, m = value.split(":")
    return int(h) * 60 + int(m)


def fmt(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}:00"


def write_csv(path: Path, header: list[str], rows: list[list]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows(rows)


def make_gtfs() -> None:
    out = DATA / "gtfs_dev"
    out.mkdir(parents=True, exist_ok=True)

    write_csv(
        out / "agency.txt",
        ["agency_id", "agency_name", "agency_url", "agency_timezone"],
        [["DEV", "FastMile Dev Fixture", "https://example.invalid", "Europe/Berlin"]],
    )
    write_csv(
        out / "stops.txt",
        ["stop_id", "stop_name", "stop_lat", "stop_lon"],
        [[sid, name, lat, lon] for sid, (name, lat, lon) in STOPS.items()],
    )
    write_csv(
        out / "routes.txt",
        [
            "route_id",
            "agency_id",
            "route_short_name",
            "route_long_name",
            "route_type",
            "route_color",
        ],
        [
            [rid, "DEV", short, long, rtype, color]
            for rid, (short, long, rtype, color, *_rest) in LINES.items()
        ],
    )
    write_csv(
        out / "calendar.txt",
        [
            "service_id",
            "monday",
            "tuesday",
            "wednesday",
            "thursday",
            "friday",
            "saturday",
            "sunday",
            "start_date",
            "end_date",
        ],
        [["daily", 1, 1, 1, 1, 1, 1, 1, "20260101", "20271231"]],
    )

    trips, stop_times = [], []
    for rid, (_short, _long, _type, _color, stops, mins, headway, first, last) in LINES.items():
        for direction, (seq, gaps) in enumerate([(stops, mins), (stops[::-1], mins[::-1])]):
            start = hhmm_to_min(first) + direction * 5
            n = 0
            while start <= hhmm_to_min(last):
                trip_id = f"{rid}_{direction}_{n:03d}"
                trips.append([rid, "daily", trip_id, STOPS[seq[-1]][0], direction])
                t = start
                for i, stop_id in enumerate(seq):
                    if i > 0:
                        t += gaps[i - 1]
                    dwell = 1 if 0 < i < len(seq) - 1 else 0
                    stop_times.append([trip_id, fmt(t), fmt(t + dwell), stop_id, i + 1])
                    t += dwell
                start += headway
                n += 1

    write_csv(
        out / "trips.txt",
        ["route_id", "service_id", "trip_id", "trip_headsign", "direction_id"],
        trips,
    )
    write_csv(
        out / "stop_times.txt",
        ["trip_id", "arrival_time", "departure_time", "stop_id", "stop_sequence"],
        stop_times,
    )
    (out / "README.md").write_text(
        "# Development GTFS fixture\n\n"
        "Generated by `backend/scripts/make_dev_data.py`. Stop locations are approximate and the\n"
        "timetables are invented. Use it for local development and tests only; point\n"
        "`GTFS_PATH` at a real feed for evaluation.\n"
    )
    print(f"GTFS: {len(STOPS)} stops, {len(LINES)} routes, {len(trips)} trips -> {out}")


def make_gbfs() -> None:
    out = DATA / "gbfs_dev"
    out.mkdir(parents=True, exist_ok=True)
    ts = 1790800000

    def feed(data: dict) -> dict:
        return {"last_updated": ts, "ttl": 60, "version": "2.3", "data": data}

    names = [
        "system_information",
        "vehicle_types",
        "station_information",
        "station_status",
        "free_bike_status",
    ]
    files = {
        "gbfs": feed({"en": {"feeds": [{"name": n, "url": f"{n}.json"} for n in names]}}),
        "system_information": feed(
            {
                "system_id": "fastmile_dev",
                "language": "en",
                "name": "FastMile Dev Sharing",
                "timezone": "Europe/Berlin",
            }
        ),
        "vehicle_types": feed(
            {
                "vehicle_types": [
                    {
                        "vehicle_type_id": "bike",
                        "form_factor": "bicycle",
                        "propulsion_type": "human",
                    },
                    {
                        "vehicle_type_id": "escooter",
                        "form_factor": "scooter_standing",
                        "propulsion_type": "electric",
                        "max_range_meters": 30000,
                    },
                    {
                        "vehicle_type_id": "car",
                        "form_factor": "car",
                        "propulsion_type": "combustion",
                        "max_range_meters": 400000,
                    },
                ]
            }
        ),
    }

    stations = [
        ("st_gs_bf", "Goslar Bahnhof", 51.9089, 10.4290, {"bike": 6, "car": 1}),
        ("st_gs_markt", "Goslar Marktplatz", 51.9055, 10.4299, {"bike": 3}),
        ("st_bh_bf", "Bad Harzburg Bahnhof", 51.8820, 10.5590, {"bike": 2}),
        ("st_cz_zob", "Clausthal ZOB", 51.8101, 10.3380, {"bike": 4, "car": 1}),
        ("st_cz_uni", "TU Clausthal Campus", 51.8047, 10.3385, {"bike": 5}),
    ]
    files["station_information"] = feed(
        {
            "stations": [
                {"station_id": sid, "name": name, "lat": lat, "lon": lon, "capacity": 12}
                for sid, name, lat, lon, _ in stations
            ]
        }
    )
    files["station_status"] = feed(
        {
            "stations": [
                {
                    "station_id": sid,
                    "is_renting": True,
                    "is_returning": True,
                    "last_reported": ts,
                    "num_bikes_available": sum(avail.values()),
                    "vehicle_types_available": [
                        {"vehicle_type_id": vt, "count": c} for vt, c in avail.items()
                    ],
                }
                for sid, _name, _lat, _lon, avail in stations
            ]
        }
    )
    scooters = [
        ("sc1", 51.9065, 10.4310),
        ("sc2", 51.9040, 10.4250),
        ("sc3", 51.9100, 10.4330),
        ("sc4", 51.8090, 10.3400),
        ("sc5", 51.8060, 10.3350),
    ]
    files["free_bike_status"] = feed(
        {
            "bikes": [
                {
                    "bike_id": bid,
                    "lat": lat,
                    "lon": lon,
                    "is_reserved": False,
                    "is_disabled": False,
                    "vehicle_type_id": "escooter",
                    "current_range_meters": 18000,
                }
                for bid, lat, lon in scooters
            ]
        }
    )

    for name, body in files.items():
        (out / f"{name}.json").write_text(json.dumps(body, indent=2))
    print(f"GBFS: {len(stations)} stations, {len(scooters)} scooters -> {out}")


def make_zones() -> None:
    zones = {
        "_note": "Development fixture: on-demand service areas. Replace with the operator's zones.",
        "zones": [
            {
                "id": "goslar",
                "name": "Goslar Stadt",
                "lat": 51.905,
                "lon": 10.43,
                "radius_km": 5.0,
                "wait_min": 8,
                "speed_kmh": 28,
                "provider": "Dev On-Demand",
            },
            {
                "id": "harz_nord",
                "name": "Harz Nord (pooled)",
                "lat": 51.86,
                "lon": 10.43,
                "radius_km": 13.0,
                "wait_min": 15,
                "speed_kmh": 35,
                "provider": "Dev On-Demand",
            },
        ],
    }
    path = DATA / "on_demand_zones.json"
    path.write_text(json.dumps(zones, indent=2))
    print(f"On-demand: {len(zones['zones'])} zones -> {path}")


if __name__ == "__main__":
    make_gtfs()
    make_gbfs()
    make_zones()
