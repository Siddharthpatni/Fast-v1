"""Test utilities for geo calculations."""

from app.utils.geo import haversine_distance


def test_haversine_known_distance():
    """Test haversine with Goslar ↔ Clausthal-Zellerfeld (~16km)."""
    dist = haversine_distance(51.9077, 10.4282, 51.8090, 10.3380)
    assert 10 < dist < 15  # roughly 11-12 km


def test_haversine_same_point():
    """Same point should return 0."""
    dist = haversine_distance(51.9077, 10.4282, 51.9077, 10.4282)
    assert dist == 0.0
