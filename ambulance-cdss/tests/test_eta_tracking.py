"""
tests/test_eta_tracking.py

Improvement 3.1 — tests for incident-level ETA tracking and overdue detection.

Tests confirm:
- _incident_to_dict includes eta_minutes, estimated_on_scene_at, overdue fields
- Overdue computed correctly from dispatched_at and eta_minutes
- On-scene incidents are never overdue
- Missing data produces safe defaults (overdue=False, estimated_on_scene_at=None)
"""

from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from app.repositories import _incident_to_dict


def _make_incident(
    status="dispatched",
    dispatched_at=None,
    eta_minutes=None,
):
    """Create a mock Incident object with the required fields."""
    row = MagicMock()
    row.incident_id = "test-id"
    row.created_at = datetime(2026, 6, 25, 10, 0, 0, tzinfo=timezone.utc)
    row.status = status
    row.priority_code = "P1_AIRWAY_COMPLETE"
    row.chief_complaint = "not breathing"
    row.caller_location_lat = 1.0
    row.caller_location_lon = 2.0
    row.caller_location_text = "test location"
    row.dispatch_protocol_id = "cardiac_arrest"
    row.dispatch_protocol_version = "1.0.0"
    row.field_protocol_id = None
    row.field_protocol_version = None
    row.assigned_unit_id = "unit-1"
    row.recommended_unit_type = "ALS_AMBULANCE"
    row.routed_facility_id = None
    row.routed_facility_name = None
    row.dispatched_at = dispatched_at
    row.on_scene_at = None
    row.transporting_at = None
    row.handoff_complete_at = None
    row.closed_at = None
    row.pii_purged_at = None
    row.notes = None
    row.eta_minutes = eta_minutes
    return row


class TestEtaTracking:
    def test_overdue_when_dispatched_and_past_eta(self):
        """Dispatched 15 minutes ago with eta_minutes=8 → overdue=True."""
        now = datetime.now(timezone.utc)
        dispatched = now - timedelta(minutes=15)
        row = _make_incident(status="dispatched", dispatched_at=dispatched, eta_minutes=8.0)
        d = _incident_to_dict(row)
        assert d["overdue"] is True
        assert d["eta_minutes"] == 8.0
        assert d["estimated_on_scene_at"] is not None

    def test_not_overdue_when_dispatched_within_eta(self):
        """Dispatched 5 minutes ago with eta_minutes=10 → overdue=False."""
        now = datetime.now(timezone.utc)
        dispatched = now - timedelta(minutes=5)
        row = _make_incident(status="dispatched", dispatched_at=dispatched, eta_minutes=10.0)
        d = _incident_to_dict(row)
        assert d["overdue"] is False

    def test_no_dispatched_at(self):
        """No dispatched_at → overdue=False, estimated_on_scene_at=None."""
        row = _make_incident(status="received", dispatched_at=None, eta_minutes=8.0)
        d = _incident_to_dict(row)
        assert d["overdue"] is False
        assert d["estimated_on_scene_at"] is None

    def test_no_eta_minutes(self):
        """dispatched_at set but eta_minutes=None → overdue=False."""
        now = datetime.now(timezone.utc)
        dispatched = now - timedelta(minutes=15)
        row = _make_incident(status="dispatched", dispatched_at=dispatched, eta_minutes=None)
        d = _incident_to_dict(row)
        assert d["overdue"] is False

    def test_on_scene_not_overdue(self):
        """On-scene status → overdue=False regardless of timestamps."""
        now = datetime.now(timezone.utc)
        dispatched = now - timedelta(minutes=30)
        row = _make_incident(status="on_scene", dispatched_at=dispatched, eta_minutes=8.0)
        d = _incident_to_dict(row)
        assert d["overdue"] is False

    def test_eta_minutes_in_dict(self):
        """eta_minutes is always present in the output dict."""
        row = _make_incident(eta_minutes=12.5)
        d = _incident_to_dict(row)
        assert "eta_minutes" in d
        assert d["eta_minutes"] == 12.5

    def test_estimated_on_scene_at_computed(self):
        """estimated_on_scene_at = dispatched_at + timedelta(minutes=eta_minutes)."""
        now = datetime.now(timezone.utc)
        dispatched = now - timedelta(minutes=10)
        row = _make_incident(status="dispatched", dispatched_at=dispatched, eta_minutes=5.0)
        d = _incident_to_dict(row)
        expected = dispatched + timedelta(minutes=5.0)
        actual = datetime.fromisoformat(d["estimated_on_scene_at"])
        assert actual == expected


class TestSetDispatchEta:
    def test_function_exists(self):
        from app.repositories import set_dispatch_eta
        assert callable(set_dispatch_eta)

    def test_function_is_async(self):
        import asyncio
        from app.repositories import set_dispatch_eta
        assert asyncio.iscoroutinefunction(set_dispatch_eta)

    def test_function_signature(self):
        from app.repositories import set_dispatch_eta
        sig = inspect.signature(set_dispatch_eta)
        params = list(sig.parameters.keys())
        assert "incident_id" in params
        assert "eta_minutes" in params
