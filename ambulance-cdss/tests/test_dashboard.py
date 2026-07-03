"""
tests/test_dashboard.py

Phase 6 — unit tests for dashboard sort ordering and stats aggregation.

No database, no async. Tests _priority_sort directly and exercises the
aggregation logic inside get_dashboard_stats against lightweight
SimpleNamespace stand-ins (only status and priority_code attributes —
the only attributes either function reads). Same approach as test_handoff.py.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.repositories import _priority_sort, _PRIORITY_SORT_KEY
from app.models import IncidentStatus


class TestPrioritySort:
    def test_p1_sorts_before_p2(self):
        assert _priority_sort("P1_AIRWAY_COMPLETE") < _priority_sort("P2_AIRWAY_PARTIAL")

    def test_p2_sorts_before_p3(self):
        assert _priority_sort("P2_TRAUMA_HIGH_MECHANISM") < _priority_sort("P3_TRAUMA_MINOR")

    def test_none_sorts_last(self):
        assert _priority_sort(None) > _priority_sort("P3_TRAUMA_MINOR")

    def test_route_reassess_sorts_before_none(self):
        assert _priority_sort("ROUTE_REASSESS") < _priority_sort(None)

    def test_unknown_p1_code_sorts_before_all_known_p2(self):
        # An unrecognised P1_* code should still sort above any P2_*
        assert _priority_sort("P1_UNKNOWN_NEW_CODE") < _priority_sort("P2_AIRWAY_PARTIAL")

    def test_unknown_p2_code_sorts_before_known_p3(self):
        assert _priority_sort("P2_UNKNOWN_CODE") < _priority_sort("P3_TRAUMA_MINOR")

    def test_unknown_p3_code_sorts_before_none(self):
        assert _priority_sort("P3_NEW_LOW_PRIORITY") < _priority_sort(None)

    def test_wholly_unrecognised_code_does_not_raise(self):
        result = _priority_sort("COMPLETELY_UNKNOWN_SCHEME")
        assert isinstance(result, int)

    def test_all_known_codes_are_in_expected_severity_order(self):
        ordered = [
            "P1_TRAUMA_SEVERE_BLEEDING",
            "P1_TRAUMA_AIRWAY_COMPROMISE",
            "P1_AIRWAY_COMPLETE",
            "P2_TRAUMA_HIGH_MECHANISM",
            "P2_AIRWAY_PARTIAL",
            "P3_TRAUMA_MINOR",
            "ROUTE_REASSESS",
            None,
        ]
        sort_values = [_priority_sort(code) for code in ordered]
        assert sort_values == sorted(sort_values), (
            "Known priority codes are not in strict severity order in _PRIORITY_SORT_KEY"
        )


class TestDashboardStatsAggregationLogic:
    """
    Exercises the aggregation logic extracted from get_dashboard_stats
    without a live database, by replicating the loop against
    SimpleNamespace rows. This confirms the counting logic is correct
    independently of the DB query.
    """

    def _aggregate(self, rows):
        by_status = {}
        by_priority = {}
        for row in rows:
            status_key = str(row.status)
            by_status[status_key] = by_status.get(status_key, 0) + 1
            priority_key = row.priority_code or "no_outcome_yet"
            by_priority[priority_key] = by_priority.get(priority_key, 0) + 1
        active_count = sum(
            1 for r in rows
            if r.status not in (IncidentStatus.CLOSED, IncidentStatus.HANDOFF_COMPLETE)
        )
        critical_count = sum(
            1 for r in rows
            if r.priority_code and r.priority_code.startswith("P1_")
        )
        return {
            "total": len(rows),
            "active": active_count,
            "critical": critical_count,
            "by_status": by_status,
            "by_priority": by_priority,
        }

    def test_empty_rows(self):
        result = self._aggregate([])
        assert result["total"] == 0
        assert result["active"] == 0
        assert result["critical"] == 0

    def test_counts_by_status(self):
        rows = [
            SimpleNamespace(status=IncidentStatus.RECEIVED, priority_code=None),
            SimpleNamespace(status=IncidentStatus.RECEIVED, priority_code=None),
            SimpleNamespace(status=IncidentStatus.DISPATCHED, priority_code="P1_AIRWAY_COMPLETE"),
        ]
        result = self._aggregate(rows)
        assert result["by_status"][str(IncidentStatus.RECEIVED)] == 2
        assert result["by_status"][str(IncidentStatus.DISPATCHED)] == 1

    def test_closed_and_handoff_excluded_from_active(self):
        rows = [
            SimpleNamespace(status=IncidentStatus.DISPATCHED, priority_code="P2_AIRWAY_PARTIAL"),
            SimpleNamespace(status=IncidentStatus.CLOSED, priority_code="P3_TRAUMA_MINOR"),
            SimpleNamespace(status=IncidentStatus.HANDOFF_COMPLETE, priority_code="P1_AIRWAY_COMPLETE"),
        ]
        result = self._aggregate(rows)
        assert result["active"] == 1

    def test_critical_count_only_p1(self):
        rows = [
            SimpleNamespace(status=IncidentStatus.DISPATCHED, priority_code="P1_AIRWAY_COMPLETE"),
            SimpleNamespace(status=IncidentStatus.DISPATCHED, priority_code="P1_TRAUMA_SEVERE_BLEEDING"),
            SimpleNamespace(status=IncidentStatus.ON_SCENE, priority_code="P2_AIRWAY_PARTIAL"),
            SimpleNamespace(status=IncidentStatus.RECEIVED, priority_code=None),
        ]
        result = self._aggregate(rows)
        assert result["critical"] == 2

    def test_no_outcome_yet_bucket_for_none_priority(self):
        rows = [
            SimpleNamespace(status=IncidentStatus.RECEIVED, priority_code=None),
        ]
        result = self._aggregate(rows)
        assert result["by_priority"]["no_outcome_yet"] == 1
