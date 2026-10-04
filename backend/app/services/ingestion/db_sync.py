"""Copy GTFS reference data (stops, routes) into PostGIS for spatial queries and the API."""

from geoalchemy2.elements import WKTElement
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Route, Stop
from app.services.ingestion.gtfs import GtfsFeed

BATCH = 2000


async def sync_reference_data(
    session: AsyncSession, feed: GtfsFeed, provider: str = "gtfs"
) -> tuple[int, int]:
    """Upsert stops and routes; returns (stops, routes) written."""
    stops = [
        {
            "stop_id": r.stop_id,
            "name": r.stop_name,
            "location": WKTElement(f"POINT({r.stop_lon} {r.stop_lat})", srid=4326),
            "provider": provider,
        }
        for r in feed.stops.itertuples()
    ]
    for i in range(0, len(stops), BATCH):
        stmt = insert(Stop).values(stops[i : i + BATCH])
        await session.execute(
            stmt.on_conflict_do_update(
                index_elements=["stop_id"],
                set_={"name": stmt.excluded.name, "location": stmt.excluded.location},
            )
        )

    routes = [
        {
            "route_id": r.route_id,
            "name": r.route_short_name or r.route_long_name,
            "mode": r.mode,
            "provider": provider,
            "color": f"#{r.route_color}" if r.route_color else None,
        }
        for r in feed.routes.itertuples()
    ]
    for i in range(0, len(routes), BATCH):
        stmt = insert(Route).values(routes[i : i + BATCH])
        await session.execute(
            stmt.on_conflict_do_update(
                index_elements=["route_id"],
                set_={
                    "name": stmt.excluded.name,
                    "mode": stmt.excluded.mode,
                    "color": stmt.excluded.color,
                },
            )
        )
    await session.commit()
    return len(stops), len(routes)
