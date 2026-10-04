"""Process-wide runtime state: loaded feeds, networks, models and clients.

Loaded once at startup (see main.lifespan) and refreshed in the background for
live data (GBFS every GBFS_REFRESH_SECONDS, GTFS-RT every GTFS_RT_REFRESH_SECONDS when configured).
"""

import asyncio
import logging
from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.config import settings
from app.database import async_session
from app.services.alerts import SyncResult, sync_alerts
from app.services.geocoding import Geocoder
from app.services.ingestion import (
    GbfsSnapshot,
    GtfsFeed,
    OnDemandZone,
    RealtimeState,
    fetch_realtime,
    load_gbfs,
    load_gtfs,
    load_zones,
)
from app.services.llm import FallbackLLMClient, LLMClient, make_llm_client
from app.services.ml import CapacityModel, OnDemandWaitModel
from app.services.routing import PlanningContext, TransitNetwork
from app.services.routing.streets import StreetRouter, make_street_router
from app.services.routing.transitous import TransitousClient

logger = logging.getLogger(__name__)

LOOP_TICK_SECONDS = 30
COVERAGE_MARGIN_DEG = 0.02  # About 2 km


class AppState:
    def __init__(self) -> None:
        self.tz = ZoneInfo(settings.timezone)
        self.feed: GtfsFeed | None = None
        self.feed_error: str | None = None
        self.gbfs = GbfsSnapshot()
        self.realtime: RealtimeState | None = None
        self.alert_sync: SyncResult | None = None
        self.alert_sync_error: str | None = None
        self.zones: list[OnDemandZone] = []
        self.capacity = CapacityModel()
        self.wait_model = OnDemandWaitModel()
        self.llm: LLMClient | FallbackLLMClient | None = None
        self.streets: StreetRouter | None = None
        self.geocoder: Geocoder | None = None
        self.transitous: TransitousClient | None = None
        # (south, west, north, east) of the loaded feed: FastMile's own router plans inside it
        self.coverage: tuple[float, float, float, float] | None = None
        self._networks: dict[tuple[date, float], TransitNetwork] = {}

    # ── Loading ──

    def load_static(self) -> None:
        """Load the GTFS feed, on-demand zones and trained models (blocking; run in a thread)."""
        try:
            self.feed = load_gtfs(settings.gtfs_path)
            self.feed_error = None
            logger.info("Loaded GTFS feed %s (%d stops)", settings.gtfs_path, len(self.feed.stops))
            lat, lon = self.feed.stops["stop_lat"], self.feed.stops["stop_lon"]
            m = COVERAGE_MARGIN_DEG  # Doors near the edge stops still count as inside
            self.coverage = (
                round(float(lat.min()) - m, 4),
                round(float(lon.min()) - m, 4),
                round(float(lat.max()) + m, 4),
                round(float(lon.max()) + m, 4),
            )
        except (FileNotFoundError, ValueError) as exc:
            self.feed, self.feed_error, self.coverage = None, str(exc), None
            logger.error("GTFS feed not loaded: %s", exc)
        self.zones = load_zones(settings.on_demand_zones_path)
        self.capacity = CapacityModel.load_or_default(settings.capacity_model_path)  # type: ignore[assignment]
        self.wait_model = OnDemandWaitModel.load_or_default(settings.wait_model_path)  # type: ignore[assignment]
        self._networks.clear()

    def init_clients(self) -> None:
        self.streets = make_street_router()
        self.geocoder = Geocoder()
        self.transitous = TransitousClient() if settings.transitous_url else None
        try:
            self.llm = make_llm_client()
        except ValueError as exc:
            logger.error("LLM disabled: %s", exc)
            self.llm = None

    async def refresh_gbfs(self) -> None:
        sources = [s.strip() for s in settings.gbfs_feeds.split(",") if s.strip()]
        if sources:
            self.gbfs = await load_gbfs(sources)

    async def refresh_realtime(self) -> None:
        """Fetch delays/cancellations, then turn regional service alerts into disruptions."""
        if not settings.gtfs_rt_url:
            return
        realtime = await fetch_realtime(settings.gtfs_rt_url)
        alerts, realtime.alerts = realtime.alerts, []  # Don't keep ~100k alert objects around
        self.realtime = realtime
        if not alerts or self.feed is None:
            return
        net = await asyncio.to_thread(self.network_for, datetime.now(self.tz).date())
        if net is None:
            return
        try:
            async with async_session() as session:
                self.alert_sync = await sync_alerts(
                    session, alerts, net, self.llm, self.tz, self.coverage
                )
            self.alert_sync_error = None
            if self.alert_sync.created or self.alert_sync.resolved:
                logger.info("Service alerts: %s", self.alert_sync)
        except Exception as exc:  # noqa: BLE001 — the database may be down; routing goes on
            self.alert_sync_error = str(exc)
            logger.warning("Service alert sync failed: %s", exc)

    async def run_refresh_loop(self) -> None:
        """Keep live feeds fresh until cancelled. Realtime is fetched right away."""
        since_rt = settings.gtfs_rt_refresh_seconds
        since_gbfs = 0
        while True:
            if since_rt >= settings.gtfs_rt_refresh_seconds:
                try:
                    await self.refresh_realtime()
                except Exception:  # noqa: BLE001 — never let a feed kill the loop
                    logger.exception("Realtime refresh failed")
                since_rt = 0
            await asyncio.sleep(LOOP_TICK_SECONDS)
            since_rt += LOOP_TICK_SECONDS
            since_gbfs += LOOP_TICK_SECONDS
            try:
                if since_gbfs >= settings.gbfs_refresh_seconds:
                    await self.refresh_gbfs()
                    since_gbfs = 0
            except Exception:  # noqa: BLE001 — never let a feed kill the loop
                logger.exception("Live feed refresh failed")

    async def close(self) -> None:
        if self.llm is not None:
            await self.llm.close()
        if self.streets is not None:
            self.streets.close()
        if self.geocoder is not None:
            await self.geocoder.close()
        if self.transitous is not None:
            await self.transitous.close()

    # ── Access ──

    def covers(self, lat: float, lon: float) -> bool:
        """Whether a point is inside the loaded feed's region."""
        if self.coverage is None:
            return False
        south, west, north, east = self.coverage
        return south <= lat <= north and west <= lon <= east

    def network_for(self, day: date) -> TransitNetwork | None:
        """The network for a service day, rebuilt when new realtime data arrives."""
        if self.feed is None:
            return None
        rt = self.realtime if self.realtime and not self.realtime.error else None
        key = (day, rt.fetched_at if rt else 0.0)
        if key not in self._networks:
            if len(self._networks) >= 2:  # A region network is ~1 GB; keep two at most
                self._networks.pop(next(iter(self._networks)))
            self._networks[key] = TransitNetwork(self.feed, day, rt)
        return self._networks[key]

    def planning_context(self, network: TransitNetwork) -> PlanningContext:
        return PlanningContext(
            network=network,
            streets=self.streets,
            gbfs=self.gbfs,
            zones=self.zones,
            wait_model=self.wait_model,
            tz=self.tz,
        )


state = AppState()
