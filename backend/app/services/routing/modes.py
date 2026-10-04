"""Mode profiles for first/last-mile and door-to-door legs."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ModeProfile:
    name: str
    speed_kmh: float
    detour: float  # Street-network distance ≈ straight-line distance × detour
    max_km: float  # Longest leg we would offer with this mode
    setup_min: float  # Unlock / park / find the vehicle
    shared: bool  # Needs a vehicle from GBFS
    cost_min: float  # Generalized-cost penalty (price, effort) in minutes

    def minutes(self, km: float) -> float:
        return km * self.detour / self.speed_kmh * 60 + self.setup_min


WALK = ModeProfile("walk", 4.8, 1.3, 1.5, 0, False, 0)
MODES: dict[str, ModeProfile] = {
    "walk": WALK,
    "bike": ModeProfile("bike", 15, 1.3, 8, 2, True, 2),
    "scooter": ModeProfile("scooter", 18, 1.3, 6, 1, True, 3),
    "car_share": ModeProfile("car_share", 35, 1.4, 40, 5, True, 8),
    "on_demand": ModeProfile("on_demand", 30, 1.35, 30, 0, False, 5),  # speed/wait from zone
}

ACCESS_MODES = ["walk", "bike", "scooter", "car_share", "on_demand"]
EGRESS_MODES = ["walk", "bike", "scooter", "on_demand"]
TRANSIT_MODES = {"bus", "tram", "train", "subway", "ferry", "gondola", "funicular"}

MAX_WALK_TO_VEHICLE_KM = 0.5  # How far we send someone to pick up a shared vehicle
MAX_DIRECT_WALK_KM = 3.0
MIN_TRANSFER_SECONDS = 120
TRANSFER_WALK_RADIUS_KM = 0.4
