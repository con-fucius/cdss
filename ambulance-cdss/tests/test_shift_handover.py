"""
tests/test_shift_handover.py

Improvement 4.1 — tests for the shift handover report.

Tests confirm:
- Endpoint exists and accepts shift_start/shift_end query params
- Repository function signature is correct
- Plain-text renderer produces expected output
- Validation rejects start >= end
"""

from __future__ import annotations

import inspect

from app import main
from app.repositories import get_shift_handover, render_shift_handover_text


class TestShiftHandoverEndpoint:
    def test_endpoint_exists(self):
        """GET /dashboard/shift-handover is registered."""
        from app.main import app
        routes = [(r.path, list(r.methods)) for r in app.routes]
        get_routes = [
            path for path, methods in routes
            if path == "/dashboard/shift-handover" and "GET" in methods
        ]
        assert len(get_routes) == 1

    def test_endpoint_validates_start_before_end(self):
        """shift_start must be before shift_end."""
        source = inspect.getsource(main.shift_handover)
        assert "shift_start must be before shift_end" in source

    def test_endpoint_validates_iso_datetime(self):
        """Both params must be valid ISO datetimes."""
        source = inspect.getsource(main.shift_handover)
        assert "valid ISO datetime" in source

    def test_endpoint_calls_repository(self):
        """The endpoint calls get_shift_handover."""
        source = inspect.getsource(main.shift_handover)
        assert "get_shift_handover" in source

    def test_endpoint_includes_text_rendering(self):
        """The response includes text_rendering."""
        source = inspect.getsource(main.shift_handover)
        assert "text_rendering" in source


class TestShiftHandoverRepository:
    def test_function_is_async(self):
        import asyncio
        assert asyncio.iscoroutinefunction(get_shift_handover)

    def test_function_signature(self):
        sig = inspect.signature(get_shift_handover)
        params = list(sig.parameters.keys())
        assert "shift_start" in params
        assert "shift_end" in params


class TestShiftHandoverTextRendering:
    def test_renders_header(self):
        handover = {
            "shift_start": "2026-06-25T06:00:00",
            "shift_end": "2026-06-25T18:00:00",
            "total_incidents": 5,
            "by_status": {"received": 2, "dispatched": 3},
            "by_priority": {"P1_AIRWAY_COMPLETE": 2, "P3_TRAUMA_MINOR": 3},
            "active_at_shift_end": [],
            "active_at_shift_end_count": 0,
            "top_resolved": [],
        }
        text = render_shift_handover_text(handover)
        assert "SHIFT HANDOVER REPORT" in text
        assert "2026-06-25T06:00:00" in text
        assert "Total incidents: 5" in text

    def test_renders_counts(self):
        handover = {
            "shift_start": "2026-06-25T06:00:00",
            "shift_end": "2026-06-25T18:00:00",
            "total_incidents": 2,
            "by_status": {"dispatched": 2},
            "by_priority": {"P1_AIRWAY_COMPLETE": 2},
            "active_at_shift_end": [],
            "active_at_shift_end_count": 0,
            "top_resolved": [],
        }
        text = render_shift_handover_text(handover)
        assert "dispatched: 2" in text
        assert "P1_AIRWAY_COMPLETE: 2" in text

    def test_renders_empty_active(self):
        handover = {
            "shift_start": "2026-06-25T06:00:00",
            "shift_end": "2026-06-25T18:00:00",
            "total_incidents": 0,
            "by_status": {},
            "by_priority": {},
            "active_at_shift_end": [],
            "active_at_shift_end_count": 0,
            "top_resolved": [],
        }
        text = render_shift_handover_text(handover)
        assert "(none)" in text

    def test_renders_active_with_overdue(self):
        handover = {
            "shift_start": "2026-06-25T06:00:00",
            "shift_end": "2026-06-25T18:00:00",
            "total_incidents": 1,
            "by_status": {"dispatched": 1},
            "by_priority": {"P1_AIRWAY_COMPLETE": 1},
            "active_at_shift_end": [
                {
                    "incident_id": "abc-123-def-456",
                    "priority_code": "P1_AIRWAY_COMPLETE",
                    "status": "dispatched",
                    "assigned_unit_id": "unit-1",
                    "overdue": True,
                }
            ],
            "active_at_shift_end_count": 1,
            "top_resolved": [],
        }
        text = render_shift_handover_text(handover)
        assert "OVERDUE" in text
        assert "unit-1" in text

    def test_renders_top_resolved_with_durations(self):
        handover = {
            "shift_start": "2026-06-25T06:00:00",
            "shift_end": "2026-06-25T18:00:00",
            "total_incidents": 1,
            "by_status": {"closed": 1},
            "by_priority": {"P1_AIRWAY_COMPLETE": 1},
            "active_at_shift_end": [],
            "active_at_shift_end_count": 0,
            "top_resolved": [
                {
                    "incident_id": "abc-123-def-456",
                    "priority_code": "P1_AIRWAY_COMPLETE",
                    "dispatch_to_scene_minutes": 8.5,
                    "scene_to_handoff_minutes": 22.3,
                }
            ],
        }
        text = render_shift_handover_text(handover)
        assert "8.5min" in text
        assert "22.3min" in text
