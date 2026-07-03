"""
Unit tests for facility-mapper BallTreeIndex, ETA computation, and coordinate validation.

Tests the core spatial matching logic without a database or network:
- BallTreeIndex.find_nearest with synthetic facility data
- _compute_eta_minutes formula correctness
- validate_coordinates for Kenya/Uganda/DRC bounds
- Level filtering, service filtering, radius filtering
- Empty and edge cases
"""

from __future__ import annotations

import numpy as np
import pytest
from unittest.mock import patch

from app.data import BallTreeIndex, validate_coordinates
from app.matcher import _compute_eta_minutes


# ── Test coordinate validation ────────────────────────────────────────────


class TestValidateCoordinates:
    def test_nairobi_is_valid(self):
        assert validate_coordinates(-1.2921, 36.8219) is True

    def test_mombasa_is_valid(self):
        assert validate_coordinates(-4.0435, 39.6682) is True

    def test_kampala_is_valid(self):
        assert validate_coordinates(0.3476, 32.5825) is True

    def test_london_is_invalid(self):
        assert validate_coordinates(51.5074, -0.1278) is False

    def test_boundary_south(self):
        assert validate_coordinates(-5.0, 36.0) is True
        assert validate_coordinates(-5.001, 36.0) is False

    def test_boundary_north(self):
        assert validate_coordinates(5.0, 36.0) is True
        assert validate_coordinates(5.001, 36.0) is False

    def test_boundary_west(self):
        assert validate_coordinates(0.0, 29.0) is True
        assert validate_coordinates(0.0, 28.999) is False

    def test_boundary_east(self):
        assert validate_coordinates(0.0, 42.0) is True
        assert validate_coordinates(0.0, 42.001) is False


# ── Test ETA computation ─────────────────────────────────────────────────


class TestComputeETA:
    def test_eta_at_60_kmh(self):
        # 60 km at 60 km/h = 60 minutes
        assert _compute_eta_minutes(60.0) == 60.0

    def test_eta_short_distance(self):
        # 5 km at 60 km/h = 5 minutes
        assert _compute_eta_minutes(5.0) == 5.0

    def test_eta_very_short(self):
        # 0.5 km at 60 km/h = 0.5 minutes
        assert _compute_eta_minutes(0.5) == 0.5

    def test_eta_zero_distance(self):
        assert _compute_eta_minutes(0.0) == 0.0

    def test_eta_custom_speed(self):
        with patch("app.matcher.get_ambulance_speed_kmh", return_value=80.0):
            # 40 km at 80 km/h = 30 minutes
            assert _compute_eta_minutes(40.0) == 30.0

    def test_eta_with_slow_speed(self):
        with patch("app.matcher.get_ambulance_speed_kmh", return_value=30.0):
            # 15 km at 30 km/h = 30 minutes
            assert _compute_eta_minutes(15.0) == 30.0

    def test_eta_zero_speed_fallback(self):
        with patch("app.matcher.get_ambulance_speed_kmh", return_value=0.0):
            # Falls back to 60 km/h
            assert _compute_eta_minutes(60.0) == 60.0


# ── Test BallTreeIndex with synthetic data ────────────────────────────────


class TestBallTreeIndex:
    """Tests BallTreeIndex.find_nearest with a synthetic in-memory index.

    Does NOT test the async build() method (which requires a database).
    Instead, manually populates the index to test the query logic.
    """

    def _make_index(self, facilities):
        """Create a BallTreeIndex and populate it with synthetic facilities."""
        from sklearn.neighbors import BallTree

        index = BallTreeIndex()

        lats = np.array([f["lat"] for f in facilities])
        lons = np.array([f["lon"] for f in facilities])
        coords_rad = np.radians(np.column_stack([lats, lons]))

        index._tree = BallTree(coords_rad, metric="haversine")
        index._facilities = facilities
        index._lats = lats
        index._lons = lons

        return index

    def _make_facility(self, fid, name, lat, lon, level=4, services=None):
        return {
            "facility_id": fid,
            "name": name,
            "county": "Nairobi",
            "level": level,
            "lat": lat,
            "lon": lon,
            "phone": None,
            "services": services or [],
        }

    def test_find_nearest_returns_closest(self):
        facilities = [
            self._make_facility("F1", "Kenyatta", -1.2984, 36.8165),
            self._make_facility("F2", "Nairobi Hospital", -1.2921, 36.8219),
            self._make_facility("F3", "Mombasa Hospital", -4.0435, 39.6682),
        ]
        index = self._make_index(facilities)
        results = index.find_nearest(lat=-1.29, lon=36.82, max_results=2)
        assert len(results) > 0
        closest_id = results[0]["facility_id"]
        assert closest_id in ("F1", "F2")
        assert results[0]["distance_km"] < 5.0

    def test_find_nearest_respects_max_results(self):
        facilities = [
            self._make_facility(f"F{i}", f"Hospital {i}", -1.29 + i * 0.001, 36.82 + i * 0.001)
            for i in range(10)
        ]
        index = self._make_index(facilities)
        results = index.find_nearest(lat=-1.29, lon=36.82, max_results=3)
        assert len(results) <= 3

    def test_find_nearest_level_filter(self):
        facilities = [
            self._make_facility("F1", "Level 2 Clinic", -1.292, 36.822, level=2),
            self._make_facility("F2", "Level 4 Hospital", -1.293, 36.823, level=4),
            self._make_facility("F3", "Level 5 National", -1.294, 36.824, level=5),
        ]
        index = self._make_index(facilities)
        results = index.find_nearest(lat=-1.29, lon=36.82, level_min=4, max_results=10)
        for r in results:
            assert r["level"] >= 4

    def test_find_nearest_services_filter(self):
        facilities = [
            self._make_facility("F1", "General Hospital", -1.292, 36.822, level=4, services=["surgery"]),
            self._make_facility("F2", "Specialist Hospital", -1.293, 36.823, level=5, services=["surgery", "icu", "cardiac"]),
            self._make_facility("F3", "Clinic", -1.294, 36.824, level=3, services=["basic"]),
        ]
        index = self._make_index(facilities)
        results = index.find_nearest(
            lat=-1.29, lon=36.82, required_services=["icu", "surgery"], max_results=10
        )
        for r in results:
            assert "icu" in r["services"]
            assert "surgery" in r["services"]

    def test_find_nearest_radius_filter(self):
        facilities = [
            self._make_facility("F1", "Nearby", -1.292, 36.822, level=4),
            self._make_facility("F2", "Far away", -4.043, 39.668, level=5),
        ]
        index = self._make_index(facilities)
        results = index.find_nearest(lat=-1.29, lon=36.82, radius_km=5.0, max_results=10)
        facility_ids = [r["facility_id"] for r in results]
        assert "F1" in facility_ids
        assert "F2" not in facility_ids

    def test_find_nearest_empty_index(self):
        index = BallTreeIndex()
        results = index.find_nearest(lat=-1.29, lon=36.82)
        assert results == []

    def test_find_nearest_distance_in_km(self):
        facilities = [
            self._make_facility("F1", "Nairobi CBD Hospital", -1.2921, 36.8219, level=4),
        ]
        index = self._make_index(facilities)
        results = index.find_nearest(lat=-1.29, lon=36.82, max_results=1)
        assert len(results) == 1
        assert 0 <= results[0]["distance_km"] < 5.0

    def test_find_nearest_sorted_by_distance(self):
        facilities = [
            self._make_facility("F1", "Far", -2.0, 37.0, level=4),
            self._make_facility("F2", "Near", -1.292, 36.822, level=4),
            self._make_facility("F3", "Mid", -1.5, 36.9, level=4),
        ]
        index = self._make_index(facilities)
        results = index.find_nearest(lat=-1.29, lon=36.82, max_results=10)
        distances = [r["distance_km"] for r in results]
        assert distances == sorted(distances)

    def test_is_ready_and_facility_count(self):
        index = BallTreeIndex()
        assert index.is_ready() is False
        assert index.facility_count == 0

        index = self._make_index([
            self._make_facility("F1", "H1", -1.29, 36.82, level=4),
        ])
        assert index.is_ready() is True
        assert index.facility_count == 1
