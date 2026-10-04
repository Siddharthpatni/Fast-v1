"""Data ingestion: GTFS static, GTFS-Realtime, GBFS and on-demand service areas."""

from app.services.ingestion.gbfs import GbfsSnapshot, VehicleSpot, load_gbfs
from app.services.ingestion.gtfs import GtfsFeed, load_gtfs
from app.services.ingestion.gtfs_rt import RealtimeState, ServiceAlert, fetch_realtime
from app.services.ingestion.on_demand import OnDemandZone, load_zones, zone_serving

__all__ = [
    "GbfsSnapshot",
    "GtfsFeed",
    "OnDemandZone",
    "RealtimeState",
    "ServiceAlert",
    "VehicleSpot",
    "fetch_realtime",
    "load_gbfs",
    "load_gtfs",
    "load_zones",
    "zone_serving",
]
