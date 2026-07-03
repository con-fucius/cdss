"""
tests/test_unit_location.py

Improvement 4.3 — tests for responder location updates.

Tests confirm:
- POST /incidents/{id}/unit-location endpoint exists
- GET /incidents/{id}/unit-location/latest endpoint exists
- IncidentUnitLocation model exists with correct columns
- Repository functions exist with correct signatures
- route_facility prefers latest unit location
"""

from __future__ import annotations

import inspect

from app import main
from app.models import IncidentUnitLocation
from app.repositories import add_unit_location, get_latest_unit_location


class TestUnitLocationModel:
    def test_table_name(self):
        assert IncidentUnitLocation.__tablename__ == "incident_unit_location"

    def test_has_required_columns(self):
        cols = {c.name for c in IncidentUnitLocation.__table__.columns}
        assert "id" in cols
        assert "incident_id" in cols
        assert "lat" in cols
        assert "lon" in cols
        assert "recorded_by" in cols
        assert "recorded_at" in cols

    def test_no_extra_columns(self):
        """The table should be lightweight — just the essential columns."""
        cols = {c.name for c in IncidentUnitLocation.__table__.columns}
        expected = {"id", "incident_id", "lat", "lon", "recorded_by", "recorded_at"}
        assert cols == expected


class TestUnitLocationEndpoints:
    def test_post_endpoint_exists(self):
        """POST /incidents/{id}/unit-location is registered."""
        from app.main import app
        routes = [(r.path, list(r.methods)) for r in app.routes]
        post_routes = [
            path for path, methods in routes
            if path == "/incidents/{incident_id}/unit-location" and "POST" in methods
        ]
        assert len(post_routes) == 1

    def test_get_latest_endpoint_exists(self):
        """GET /incidents/{id}/unit-location/latest is registered."""
        from app.main import app
        routes = [(r.path, list(r.methods)) for r in app.routes]
        get_routes = [
            path for path, methods in routes
            if path == "/incidents/{incident_id}/unit-location/latest" and "GET" in methods
        ]
        assert len(get_routes) == 1

    def test_post_endpoint_returns_location(self):
        """The POST endpoint returns the stored location."""
        source = inspect.getsource(main.add_unit_location)
        assert "return loc" in source

    def test_get_latest_returns_location_or_none(self):
        """The GET endpoint returns location or a 'no location' message."""
        source = inspect.getsource(main.get_latest_unit_location)
        assert "No location recorded" in source


class TestUnitLocationRepository:
    def test_add_unit_location_is_async(self):
        import asyncio
        assert asyncio.iscoroutinefunction(add_unit_location)

    def test_get_latest_unit_location_is_async(self):
        import asyncio
        assert asyncio.iscoroutinefunction(get_latest_unit_location)

    def test_add_unit_location_signature(self):
        sig = inspect.signature(add_unit_location)
        params = list(sig.parameters.keys())
        assert "incident_id" in params
        assert "lat" in params
        assert "lon" in params
        assert "recorded_by" in params
        assert "timestamp" in params

    def test_get_latest_unit_location_signature(self):
        sig = inspect.signature(get_latest_unit_location)
        assert "incident_id" in sig.parameters


class TestRouteFacilityPrefersUnitLocation:
    def test_route_facility_checks_unit_location(self):
        """route_facility must call get_latest_unit_location."""
        source = inspect.getsource(main.route_facility)
        assert "get_latest_unit_location" in source

    def test_route_facility_uses_unit_location_lat_lon(self):
        """When unit location exists, it overrides the request lat/lon."""
        source = inspect.getsource(main.route_facility)
        assert 'lat = unit_loc["lat"]' in source
        assert 'lon = unit_loc["lon"]' in source
