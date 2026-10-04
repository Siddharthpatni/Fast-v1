"""Geospatial utility helpers."""

import math

import numpy as np
from numpy.typing import NDArray

EARTH_RADIUS_KM = 6371.0


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate the great-circle distance (km) between two points using the Haversine formula.

    Args:
        lat1: Latitude of point 1 (degrees).
        lon1: Longitude of point 1 (degrees).
        lat2: Latitude of point 2 (degrees).
        lon2: Longitude of point 2 (degrees).

    Returns:
        Distance in kilometers.
    """
    lat1_r, lon1_r = math.radians(lat1), math.radians(lon1)
    lat2_r, lon2_r = math.radians(lat2), math.radians(lon2)

    dlat = lat2_r - lat1_r
    dlon = lon2_r - lon1_r

    a = math.sin(dlat / 2) ** 2 + math.cos(lat1_r) * math.cos(lat2_r) * math.sin(dlon / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return EARTH_RADIUS_KM * c


def haversine_np(
    lat: float, lon: float, lats: NDArray[np.float64], lons: NDArray[np.float64]
) -> NDArray[np.float64]:
    """Vectorized haversine distance (km) from one point to many."""
    lat_r, lon_r = np.radians(lat), np.radians(lon)
    lats_r, lons_r = np.radians(lats), np.radians(lons)
    a = (
        np.sin((lats_r - lat_r) / 2) ** 2
        + np.cos(lat_r) * np.cos(lats_r) * np.sin((lons_r - lon_r) / 2) ** 2
    )
    return 2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))
