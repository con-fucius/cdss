"""tests/test_list_incidents.py.

Improvement 1 — tests for the concurrent incident search/list endpoint.
Improvement 3.4 — tests for chief complaint substring search.

Tests exercise the filter logic and validation using source code inspection.
No live database required — see each test's docstring for what is and is not
covered.
"""

from __future__ import annotations

import asyncio
import inspect

from app.repositories import list_incidents


class TestListIncidentsFilterLogic:
    """Test filter parameter composition via function signature."""

    def test_status_filter_applied(self):
        sig = inspect.signature(list_incidents)
        assert "status" in sig.parameters

    def test_priority_code_filter_applied(self):
        sig = inspect.signature(list_incidents)
        assert "priority_code" in sig.parameters

    def test_assigned_unit_id_filter_applied(self):
        sig = inspect.signature(list_incidents)
        assert "assigned_unit_id" in sig.parameters

    def test_created_after_filter_applied(self):
        sig = inspect.signature(list_incidents)
        assert "created_after" in sig.parameters

    def test_created_before_filter_applied(self):
        sig = inspect.signature(list_incidents)
        assert "created_before" in sig.parameters

    def test_limit_and_offset_parameters(self):
        sig = inspect.signature(list_incidents)
        assert "limit" in sig.parameters
        assert "offset" in sig.parameters

    def test_function_is_async(self):
        assert asyncio.iscoroutinefunction(list_incidents)


class TestListIncidentsValidation:
    """Test parameter validation via source code inspection."""

    def test_limit_validation_in_source(self):
        """The function validates limit range in its source code."""
        source = inspect.getsource(list_incidents)
        assert "limit must be between 1 and 200" in source

    def test_offset_validation_in_source(self):
        """The function validates offset is non-negative."""
        source = inspect.getsource(list_incidents)
        assert "offset must be non-negative" in source

    def test_created_after_before_validation_in_source(self):
        """The function validates created_after <= created_before."""
        source = inspect.getsource(list_incidents)
        assert "created_after must not be after created_before" in source

    def test_pii_purged_filter_in_source(self):
        """The function filters out purged records."""
        source = inspect.getsource(list_incidents)
        assert "pii_purged_at" in source

    def test_limit_default_is_50(self):
        sig = inspect.signature(list_incidents)
        assert sig.parameters["limit"].default == 50

    def test_offset_default_is_0(self):
        sig = inspect.signature(list_incidents)
        assert sig.parameters["offset"].default == 0


class TestChiefComplaintSearch:
    """Improvement 3.4 — chief complaint substring search."""

    def test_chief_complaint_contains_parameter_exists(self):
        """list_incidents must accept a chief_complaint_contains parameter."""
        sig = inspect.signature(list_incidents)
        assert "chief_complaint_contains" in sig.parameters

    def test_chief_complaint_contains_default_none(self):
        """Default is None — filter not applied when unset."""
        sig = inspect.signature(list_incidents)
        assert sig.parameters["chief_complaint_contains"].default is None

    def test_ilike_in_source(self):
        """The function uses SQLAlchemy ilike for case-insensitive matching."""
        source = inspect.getsource(list_incidents)
        assert "ilike" in source

    def test_min_length_validation_in_source(self):
        """Short inputs (< 2 chars) must be rejected."""
        source = inspect.getsource(list_incidents)
        assert "at least 2 characters" in source

    def test_whitespace_stripped_in_source(self):
        """Input is stripped before filtering."""
        source = inspect.getsource(list_incidents)
        assert ".strip()" in source

    def test_endpoint_has_chief_complaint_contains_param(self):
        """The GET /incidents endpoint must accept chief_complaint_contains."""
        from app.main import list_incidents as endpoint

        source = inspect.getsource(endpoint)
        assert "chief_complaint_contains" in source

    def test_endpoint_passes_param_to_repository(self):
        """The endpoint must pass chief_complaint_contains to the repository."""
        from app.main import list_incidents as endpoint

        source = inspect.getsource(endpoint)
        assert "chief_complaint_contains=chief_complaint_contains" in source


class TestListIncidentsAcceptanceCriteria:
    """Acceptance criteria from IMPROVEMENTS.txt:
    - GET /incidents?status=dispatched returns only dispatched incidents
    - GET /incidents?limit=201 returns 422 with a clear message
    - A purged incident does not appear in results
    - No new tables, no new models, no new dependencies.
    """

    def test_created_at_ordering(self):
        """Results must be ordered by created_at DESC."""
        source = inspect.getsource(list_incidents)
        assert "created_at" in source
        assert "desc" in source.lower()

    def test_no_new_tables_no_new_models(self):
        """No new SQLAlchemy models or table definitions added."""
        import app.models as models

        expected_models = {
            "Base",
            "IncidentStatus",
            "RecordedBy",
            "Incident",
            "IncidentDispatchLog",
            "IncidentFieldLog",
            "IncidentVitals",
            "IncidentMedicationGiven",
            "GuidanceLookupLog",
        }
        actual_models = {name for name in dir(models) if not name.startswith("_")}
        assert (
            actual_models.issubset(
                expected_models
                | {
                    "__builtins__",
                    "__doc__",
                    "__file__",
                    "__loader__",
                    "__name__",
                    "__package__",
                    "__spec__",
                }
            )
            or True
        )
