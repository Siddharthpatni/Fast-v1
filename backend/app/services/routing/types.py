"""Plan data structures shared by the planner and the optimisation check."""

from dataclasses import dataclass, field
from datetime import datetime

from app.services.routing.modes import TRANSIT_MODES


@dataclass
class StopCall:
    stop_id: str
    name: str
    lat: float
    lon: float
    time: datetime


@dataclass
class Issue:
    severity: str  # "info", "warning", "error" (error = plan infeasible)
    message: str
    leg_index: int | None = None


@dataclass
class TransferCheck:
    """One connection the optimisation check scored, kept so Jev can review it."""

    leg_index: int  # The leg being boarded
    slack_min: float  # Scheduled time between arriving and departing
    reported_delay_min: float  # Reported delay of the arriving line
    probability: float  # Heuristic P(connection made), already in the plan's reliability
    jev_probability: float | None = None


@dataclass
class Leg:
    mode: str
    from_name: str
    to_name: str
    from_lat: float
    from_lon: float
    to_lat: float
    to_lon: float
    departure: datetime
    arrival: datetime
    distance_km: float
    line_name: str | None = None
    line_color: str | None = None
    route_id: str | None = None
    trip_id: str | None = None
    headsign: str | None = None
    provider: str | None = None
    vehicles_available: int | None = None  # Shared vehicles at the pickup spot
    pickup_in_future: bool = False  # Shared vehicle picked up mid-journey (availability may change)
    wait_min: float = 0.0  # On-demand pickup wait included in the leg
    occupancy: float | None = None  # Predicted, transit legs
    delay_min: int | None = None  # Live delay at departure, when the source reports one
    platform: str | None = None  # Departure track or platform
    cancelled: bool = False
    on_request: bool = False  # Runs only when booked by phone (call-ahead taxi / Rufbus)
    source: str | None = None  # "transitous" for legs planned outside the loaded feed
    stops: list[StopCall] = field(default_factory=list)
    path: list[tuple[float, float]] = field(default_factory=list)

    @property
    def is_transit(self) -> bool:
        return self.mode in TRANSIT_MODES

    @property
    def duration_min(self) -> float:
        return (self.arrival - self.departure).total_seconds() / 60


@dataclass
class Plan:
    legs: list[Leg]
    feasible: bool = True
    reliability: float = 1.0
    issues: list[Issue] = field(default_factory=list)
    cost: float = 0.0
    label: str | None = None
    transfer_checks: list[TransferCheck] = field(default_factory=list)
    source: str = "fastmile"  # Who planned it: FastMile's own router or "transitous"
    follows_report: bool = False  # Rides a line a disruption report recommends (set by the check)

    @property
    def departure(self) -> datetime:
        return self.legs[0].departure

    @property
    def arrival(self) -> datetime:
        return self.legs[-1].arrival

    @property
    def duration_min(self) -> float:
        return (self.arrival - self.departure).total_seconds() / 60

    @property
    def distance_km(self) -> float:
        return sum(leg.distance_km for leg in self.legs)

    @property
    def transfers(self) -> int:
        return max(0, sum(leg.is_transit for leg in self.legs) - 1)

    @property
    def signature(self) -> tuple[str, ...]:
        return tuple(leg.trip_id or leg.mode for leg in self.legs if leg.mode != "walk")
