"""Cut nationwide GTFS feeds down to a region and merge them into one feed.

    python backend/scripts/build_region_feed.py \
        --bbox 51.30,8.80,53.20,11.70 --out data/gtfs_region \
        data/raw/gtfs_nv.zip data/raw/gtfs_rv.zip data/raw/gtfs_fv.zip

(`make real-data` downloads the inputs and runs this.)

Download the inputs first (free, from gtfs.de):
    https://download.gtfs.de/germany/nv_free/latest.zip   local buses and trams
    https://download.gtfs.de/germany/rv_free/latest.zip   regional rail
    https://download.gtfs.de/germany/fv_free/latest.zip   long-distance rail

Keeps every trip with at least two stops inside the box, with only its in-box
stops. IDs are kept as published so the matching GTFS-Realtime feed
(https://realtime.gtfs.de/realtime-free.pb) still lines up; if two input feeds
reuse an ID for different things, the later feed's ID gets a prefix and a
warning is printed (realtime updates for those trips then won't match).
"""

import argparse
import io
import zipfile
from pathlib import Path

import pandas as pd

CHUNK_ROWS = 2_000_000
ID_TABLES = {  # table → ID columns that must be unique across feeds
    "agency": ["agency_id"],
    "stops": ["stop_id"],
    "routes": ["route_id"],
    "trips": ["trip_id"],
    "calendar": ["service_id"],
}


def read(zf: zipfile.ZipFile, name: str, **kwargs) -> pd.DataFrame | None:
    members = {Path(m).name: m for m in zf.namelist()}
    if f"{name}.txt" not in members:
        return None
    with zf.open(members[f"{name}.txt"]) as f:
        return pd.read_csv(f, dtype=str, keep_default_na=False, encoding="utf-8-sig", **kwargs)


def cut_feed(path: Path, bbox: tuple[float, float, float, float]) -> dict[str, pd.DataFrame]:
    south, west, north, east = bbox
    with zipfile.ZipFile(path) as zf:
        stops = read(zf, "stops")
        lat = pd.to_numeric(stops["stop_lat"], errors="coerce")
        lon = pd.to_numeric(stops["stop_lon"], errors="coerce")
        inside = stops[(lat >= south) & (lat <= north) & (lon >= west) & (lon <= east)]
        keep_stops = set(inside["stop_id"])
        print(f"  {path.name}: {len(keep_stops)} of {len(stops)} stops in the box")

        # stop_times is the big one (tens of millions of rows): stream it
        members = {Path(m).name: m for m in zf.namelist()}
        parts = []
        with zf.open(members["stop_times.txt"]) as f:
            reader = pd.read_csv(
                io.TextIOWrapper(f, encoding="utf-8-sig"),
                dtype=str,
                keep_default_na=False,
                chunksize=CHUNK_ROWS,
            )
            for i, chunk in enumerate(reader):
                parts.append(chunk[chunk["stop_id"].isin(keep_stops)])
                print(f"    stop_times chunk {i + 1}", end="\r", flush=True)
        stop_times = pd.concat(parts, ignore_index=True)
        print()
        counts = stop_times["trip_id"].value_counts()
        keep_trips = set(counts[counts >= 2].index)
        stop_times = stop_times[stop_times["trip_id"].isin(keep_trips)]

        trips = read(zf, "trips")
        trips = trips[trips["trip_id"].isin(keep_trips)]
        routes = read(zf, "routes")
        routes = routes[routes["route_id"].isin(set(trips["route_id"]))]
        services = set(trips["service_id"])
        tables = {
            "stops": stops[stops["stop_id"].isin(set(stop_times["stop_id"]))],
            "stop_times": stop_times,
            "trips": trips,
            "routes": routes,
        }
        agency = read(zf, "agency")
        if agency is not None:
            used = set(routes["agency_id"]) if "agency_id" in routes else set()
            tables["agency"] = agency[agency["agency_id"].isin(used)] if used else agency
        for name in ("calendar", "calendar_dates"):
            df = read(zf, name)
            if df is not None:
                tables[name] = df[df["service_id"].isin(services)]
        for name in ("feed_info", "attributions"):
            df = read(zf, name)
            if df is not None:
                tables[name] = df
    print(f"    kept {len(trips)} trips on {len(routes)} routes, {len(stop_times)} stop times")
    return tables


def prefix_collisions(
    merged: dict[str, pd.DataFrame], new: dict[str, pd.DataFrame], tag: str
) -> None:
    """Prefix IDs in `new` that already exist in `merged`, everywhere they are referenced."""
    renames: dict[str, dict[str, str]] = {}
    # The same station published in both feeds (same ID, name and position) is one stop:
    # keep the first copy instead of renaming it
    if "stops" in new and "stops" in merged:
        key = ["stop_id", "stop_name", "stop_lat", "stop_lon"]
        same = new["stops"].merge(merged["stops"][key], on=key)["stop_id"]
        if len(same):
            print(f"  merged {len(same)} stops published in both feeds")
            new["stops"] = new["stops"][~new["stops"]["stop_id"].isin(set(same))]
    for table, cols in ID_TABLES.items():
        if table not in new or table not in merged:
            continue
        for col in cols:
            clash = set(new[table][col]) & set(merged[table][col])
            if clash:
                print(f"  warning: {len(clash)} {col} values clash; prefixing with '{tag}:'")
                renames[col] = {v: f"{tag}:{v}" for v in clash}
    for col, mapping in renames.items():
        for df in new.values():
            if col in df.columns:
                df[col] = df[col].replace(mapping)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("feeds", nargs="+", type=Path)
    parser.add_argument("--bbox", default="51.70,10.15,52.35,10.75", help="south,west,north,east")
    parser.add_argument("--out", type=Path, default=Path("data/gtfs_region"))
    args = parser.parse_args()
    bbox = tuple(float(x) for x in args.bbox.split(","))

    merged: dict[str, pd.DataFrame] = {}
    for i, path in enumerate(args.feeds):
        print(f"Cutting {path} to {bbox}")
        tables = cut_feed(path, bbox)  # type: ignore[arg-type]
        if merged:
            prefix_collisions(merged, tables, f"f{i}")
        for name, df in tables.items():
            merged[name] = (
                pd.concat([merged[name], df], ignore_index=True) if name in merged else df
            )

    args.out.mkdir(parents=True, exist_ok=True)
    for name, df in merged.items():
        if name in ("feed_info", "attributions"):
            df = df.drop_duplicates()
        df.to_csv(args.out / f"{name}.txt", index=False)
    print(
        f"Wrote {args.out}: {len(merged['stops'])} stops, {len(merged['routes'])} routes, "
        f"{len(merged['trips'])} trips, {len(merged['stop_times'])} stop times"
    )


if __name__ == "__main__":
    main()
