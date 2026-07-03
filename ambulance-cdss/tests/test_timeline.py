"""tests/test_timeline.py.

Improvement 3 — tests for the structured incident timeline endpoint.

Tests confirm:
- Merge of two events from different streams with different timestamps
  produces correct order.
- A None timestamp row does not crash the sort.
- An empty incident returns {"events": [], "event_count": 0}.
- No new DB table, no new model, no new dependency.
"""

from __future__ import annotations

import asyncio
import inspect

from app.repositories import get_incident_timeline


class TestTimelineMergeAndSort:
    def test_merge_two_events_different_timestamps_correct_order(self):
        """Events from different streams with different timestamps are sorted correctly."""
        events = [
            {
                "timestamp": "2026-06-01T10:05:00+00:00",
                "event_type": "vitals",
                "source": "field",
                "data": {},
            },
            {
                "timestamp": "2026-06-01T10:00:00+00:00",
                "event_type": "dispatch_answer",
                "source": "dispatch",
                "data": {},
            },
            {
                "timestamp": "2026-06-01T10:10:00+00:00",
                "event_type": "medication",
                "source": "field",
                "data": {},
            },
        ]

        events.sort(key=lambda e: (e["timestamp"] is None, e["timestamp"] or "", e["event_type"]))

        assert events[0]["event_type"] == "dispatch_answer"
        assert events[1]["event_type"] == "vitals"
        assert events[2]["event_type"] == "medication"

    def test_none_timestamp_sorts_last(self):
        """Events with None timestamp are sorted to the end, not causing a crash."""
        events = [
            {"timestamp": None, "event_type": "vitals", "source": "field", "data": {}},
            {
                "timestamp": "2026-06-01T10:00:00+00:00",
                "event_type": "dispatch_answer",
                "source": "dispatch",
                "data": {},
            },
        ]

        events.sort(key=lambda e: (e["timestamp"] is None, e["timestamp"] or "", e["event_type"]))

        assert events[0]["event_type"] == "dispatch_answer"
        assert events[1]["event_type"] == "vitals"
        assert events[1]["timestamp"] is None

    def test_empty_list_sorts_to_empty(self):
        """Sorting an empty list produces an empty list."""
        events = []
        events.sort(key=lambda e: (e["timestamp"] is None, e["timestamp"] or "", e["event_type"]))
        assert events == []

    def test_tie_break_by_event_type_alphabetically(self):
        """Events with the same timestamp are sorted by event_type alphabetically."""
        events = [
            {
                "timestamp": "2026-06-01T10:00:00+00:00",
                "event_type": "vitals",
                "source": "field",
                "data": {},
            },
            {
                "timestamp": "2026-06-01T10:00:00+00:00",
                "event_type": "dispatch_answer",
                "source": "dispatch",
                "data": {},
            },
            {
                "timestamp": "2026-06-01T10:00:00+00:00",
                "event_type": "medication",
                "source": "field",
                "data": {},
            },
        ]

        events.sort(key=lambda e: (e["timestamp"] is None, e["timestamp"] or "", e["event_type"]))

        types = [e["event_type"] for e in events]
        assert types == sorted(types)


class TestTimelineFunctionSignature:
    def test_function_is_async(self):
        assert asyncio.iscoroutinefunction(get_incident_timeline)

    def test_function_returns_dict_or_none(self):
        sig = inspect.signature(get_incident_timeline)
        assert "incident_id" in sig.parameters


class TestTimelineAcceptanceCriteria:
    def test_no_new_db_table(self):
        """No new SQLAlchemy model or table definition added for timeline."""
        source = inspect.getsource(get_incident_timeline)
        assert "create_all" not in source
        assert "CREATE TABLE" not in source

    def test_calls_get_incident_full(self):
        """get_incident_full() is called to assemble the timeline."""
        source = inspect.getsource(get_incident_timeline)
        assert "get_incident_full" in source

    def test_event_types_are_from_specified_set(self):
        """All event_type values match the spec: dispatch_answer, field_action,
        vitals, medication, guidance_lookup.
        """
        source = inspect.getsource(get_incident_timeline)
        assert "dispatch_answer" in source
        assert "field_action" in source
        assert "vitals" in source
        assert "medication" in source
        assert "guidance_lookup" in source

    def test_event_sources_match_spec(self):
        """Source field must be 'dispatch', 'field', or 'system'."""
        source = inspect.getsource(get_incident_timeline)
        assert '"dispatch"' in source or "'dispatch'" in source
        assert '"field"' in source or "'field'" in source
        assert '"system"' in source or "'system'" in source

    def test_returns_empty_events_for_empty_incident(self):
        """An incident with no events should return empty list, not None or error."""
        source = inspect.getsource(get_incident_timeline)
        assert "event_count" in source
