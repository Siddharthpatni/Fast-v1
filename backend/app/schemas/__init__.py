"""Pydantic schemas for API request/response validation."""

import json
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

# ── Health ──


class HealthResponse(BaseModel):
    """Health check response."""

    status: str = "ok"
    service: str = "fastmile"
    version: str = "0.1.0"
    jev_enabled: bool = False
    llm: dict[str, Any] | None = None
    network_loaded: bool = False
    data_attribution: str | None = None  # Credit line required by the transit data licence
    coverage: list[float] | None = None  # [south, west, north, east] of FastMile's own timetables
    germany_routing: bool = False  # Trips elsewhere in Germany are planned via Transitous


class NetworkStatus(BaseModel):
    gtfs_source: str | None
    gtfs_error: str | None
    service_date: str | None
    stops: int
    routes: int
    trips_today: int
    connections_today: int
    shared_vehicles: dict[str, int]
    gbfs_sources: list[str]
    gbfs_errors: list[str]
    on_demand_zones: list[str]
    realtime: dict[str, Any] | None
    models: dict[str, Any]
    llm: dict[str, Any] | None
    jev_enabled: bool
    street_routing: dict[str, Any] | None = None
    germany_routing: dict[str, Any] | None = None


# ── Stops ──


class StopOut(BaseModel):
    """A transit stop from the loaded network."""

    stop_id: str
    name: str
    lat: float
    lon: float
    departures_today: int
    distance_km: float | None = None


# ── Disruptions ──


class DisruptionCreate(BaseModel):
    """Payload to submit a new disruption report (raw text)."""

    raw_text: str = Field(..., min_length=5, description="The raw disruption report text")
    source: str = Field(..., description="Where the report came from")
    reported_at: datetime | None = Field(
        None, description="When the report was issued (default: now)"
    )


class DisruptionUpdate(BaseModel):
    status: Literal["active", "resolved"]


class DisruptionOut(BaseModel):
    """A classified disruption event returned by the API."""

    id: int
    raw_text: str
    summary: str | None
    severity: str | None
    effect: str | None = None
    disruption_type: str | None = None
    delay_minutes: int | None = None
    needs_reroute: float | None = None
    extraction_method: str | None = None
    affected_lines: list[str] = []
    affected_routes: list[str] = []  # Matched GTFS route_ids
    affected_stops: list[str] = []  # Matched GTFS stop_ids
    alternatives: list[str] = []  # What the report recommends instead
    alternative_lines: list[str] = []
    start_time: datetime | None
    end_time: datetime | None
    source: str
    confidence: float | None
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}

    @field_validator(
        "affected_lines",
        "affected_routes",
        "affected_stops",
        "alternatives",
        "alternative_lines",
        mode="before",
    )
    @classmethod
    def _parse_json_list(cls, value: Any) -> Any:
        if value is None:
            return []
        if isinstance(value, str):
            return json.loads(value) if value.startswith("[") else [value]
        return value


# ── Routing ──


class RouteRequest(BaseModel):
    """Request to compute a multimodal route."""

    origin_lat: float = Field(..., ge=-90, le=90)
    origin_lon: float = Field(..., ge=-180, le=180)
    destination_lat: float = Field(..., ge=-90, le=90)
    destination_lon: float = Field(..., ge=-180, le=180)
    departure_time: datetime | None = None  # Naive times are local (Europe/Berlin)
    arrive_by: datetime | None = None
    preferred_modes: list[str] | None = (
        None  # e.g. ["bus", "bike", "walk"]; "transit" = any vehicle
    )
    max_results: int = Field(5, ge=1, le=10)
    lang: Literal["en", "de"] = "en"  # Language of messages and issues


class StopCallOut(BaseModel):
    stop_id: str
    name: str
    lat: float
    lon: float
    time: datetime


class IssueOut(BaseModel):
    severity: str
    message: str
    leg_index: int | None = None


class RouteLeg(BaseModel):
    """One leg of a multimodal route."""

    mode: str
    from_name: str
    to_name: str
    from_lat: float
    from_lon: float
    to_lat: float
    to_lon: float
    departure_time: datetime
    arrival_time: datetime
    duration_minutes: float
    distance_km: float
    line_name: str | None = None
    line_color: str | None = None
    route_id: str | None = None
    trip_id: str | None = None
    headsign: str | None = None
    provider: str | None = None
    vehicles_available: int | None = None
    wait_minutes: float = 0.0
    occupancy: float | None = None
    delay_minutes: int | None = None  # Live delay at departure
    platform: str | None = None
    cancelled: bool = False
    on_request: bool = False  # Book by phone before the trip
    source: str | None = None
    instructions: str | None = None
    stops: list[StopCallOut] = []
    path: list[tuple[float, float]] = []


class RouteOption(BaseModel):
    """A complete route option with multiple legs."""

    rank: int
    label: str | None = None
    departure_time: datetime
    arrival_time: datetime
    total_duration_minutes: float
    total_distance_km: float
    transfers: int
    reliability_score: float = Field(
        ..., ge=0, le=1, description="0 = unreliable, 1 = very reliable"
    )
    feasible: bool = True
    cost: float = 0.0
    issues: list[IssueOut] = []
    jev_checked_transfers: int = 0  # Transfers whose risk Jev rated with confidence
    source: str = "fastmile"  # "fastmile" (own router, loaded region) or "transitous" (Germany)
    legs: list[RouteLeg]


class RouteResponse(BaseModel):
    """Response containing ranked route options."""

    routes: list[RouteOption]
    computed_at: datetime
    disruptions_considered: list[int] = []
    message: str | None = None


class ReplanRequest(BaseModel):
    """Replan an active trip from where the traveller is now."""

    route: RouteOption
    at_time: datetime | None = None  # Default: now
    preferred_modes: list[str] | None = None
    lang: Literal["en", "de"] = "en"


class Position(BaseModel):
    name: str
    lat: float
    lon: float
    time: datetime


class RerouteDecision(BaseModel):
    strategy: str | None
    strategy_confidence: float | None = None
    urgency: float | None
    preferred_alternative_mode: str | None
    mode_confidence: float | None = None
    applied: bool = False  # True when the alternatives were re-ordered by this advice
    model: str


class ReplanResponse(BaseModel):
    position: Position
    arrived: bool = False
    original_still_valid: bool
    original_issues: list[IssueOut] = []
    decision: RerouteDecision | None = None  # Jev's quick call, when configured
    routes: list[RouteOption] = []
    computed_at: datetime


class DepartureOut(BaseModel):
    time: datetime  # Expected departure, live delay included
    scheduled_time: datetime
    delay_minutes: int | None = None  # None = no live data
    line_name: str
    line_color: str | None = None
    mode: str
    headsign: str
    platform: str | None = None
    cancelled: bool = False
    on_request: bool = False  # Book by phone
    disruption_id: int | None = None  # Active FastMile disruption on this line
    disruption: str | None = None


class StopRef(BaseModel):
    name: str
    lat: float
    lon: float


class DeparturesResponse(BaseModel):
    stop: StopRef
    source: str  # "fastmile" (own timetable) or "transitous"
    departures: list[DepartureOut]
    computed_at: datetime
