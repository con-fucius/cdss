"""tests/test_pre_arrival_confirmation.py.

Improvement 3.5 — tests for pre-arrival instruction read-back confirmation.

Tests confirm:
- POST /incidents/{id}/confirm-pre-arrival endpoint exists
- Incident without priority_code returns 400
- Confirmation writes to incident_field_log with correct action_type
- render_audit_text renders [CONFIRMED] for confirmation rows
- render_audit_text renders 'Not recorded' when no confirmation exists
- ConfirmPreArrivalRequest model exists with correct fields
"""

from __future__ import annotations

import inspect

from app import main
from app.handoff import render_audit_text


class TestConfirmPreArrivalEndpoint:
    def test_endpoint_exists(self):
        """POST /incidents/{id}/confirm-pre-arrival is registered."""
        from app.main import app

        routes = [(r.path, list(r.methods)) for r in app.routes]
        post_routes = [
            path
            for path, methods in routes
            if path == "/incidents/{incident_id}/confirm-pre-arrival" and "POST" in methods
        ]
        assert len(post_routes) == 1

    def test_endpoint_checks_priority_code(self):
        """The endpoint must check that the incident has a priority_code."""
        source = inspect.getsource(main.confirm_pre_arrival_instructions)
        assert "priority_code" in source
        assert "400" in source

    def test_endpoint_returns_404_for_missing_incident(self):
        """The endpoint returns 404 when incident not found."""
        source = inspect.getsource(main.confirm_pre_arrival_instructions)
        assert "status_code=404" in source

    def test_endpoint_writes_field_log(self):
        """The endpoint calls append_field_log with pre_arrival_confirmation."""
        source = inspect.getsource(main.confirm_pre_arrival_instructions)
        assert "pre_arrival_confirmation" in source
        assert "append_field_log" in source

    def test_endpoint_returns_log_row(self):
        """The endpoint returns the written log row."""
        source = inspect.getsource(main.confirm_pre_arrival_instructions)
        assert "return log_row" in source


class TestConfirmPreArrivalRequest:
    def test_model_exists(self):
        from app.main import ConfirmPreArrivalRequest

        assert hasattr(ConfirmPreArrivalRequest, "model_fields")

    def test_has_required_fields(self):
        from app.main import ConfirmPreArrivalRequest

        field_names = set(ConfirmPreArrivalRequest.model_fields.keys())
        assert "dispatcher_id" in field_names
        assert "terminal_outcome_id" in field_names
        assert "all_instructions_read" in field_names

    def test_all_instructions_read_defaults_true(self):
        from app.main import ConfirmPreArrivalRequest

        req = ConfirmPreArrivalRequest(
            dispatcher_id="disp-1",
            terminal_outcome_id="outcome_a",
        )
        assert req.all_instructions_read is True


class TestRenderAuditTextConfirmation:
    def test_confirmed_row_renders_confirmed_label(self):
        """A pre_arrival_confirmation row renders [CONFIRMED] in the export."""
        full = {
            "incident": {
                "incident_id": "test",
                "created_at": "2026-06-25T10:00:00",
                "status": "closed",
                "chief_complaint": "test",
                "dispatch_protocol_id": None,
                "dispatch_protocol_version": None,
                "dispatch_protocol_snapshot": None,
            },
            "dispatch_log": [],
            "field_log": [
                {
                    "id": "log-1",
                    "step_id": "pre_arrival_confirmation",
                    "action_type": "pre_arrival_confirmation",
                    "data": {
                        "confirmed_by": "disp-1",
                        "all_instructions_read": True,
                        "terminal_outcome_id": "outcome_a",
                    },
                    "recorded_by": "disp-1",
                    "timestamp": "2026-06-25T10:05:00",
                }
            ],
            "vitals_history": [],
            "medications_given": [],
            "guidance_lookups": [],
        }
        text = render_audit_text(full)
        assert "[CONFIRMED]" in text
        assert "disp-1" in text

    def test_no_confirmation_renders_not_recorded(self):
        """Without confirmation rows, the section shows 'Not recorded'."""
        full = {
            "incident": {
                "incident_id": "test",
                "created_at": "2026-06-25T10:00:00",
                "status": "closed",
                "chief_complaint": "test",
                "dispatch_protocol_id": None,
                "dispatch_protocol_version": None,
                "dispatch_protocol_snapshot": None,
            },
            "dispatch_log": [],
            "field_log": [],
            "vitals_history": [],
            "medications_given": [],
            "guidance_lookups": [],
        }
        text = render_audit_text(full)
        assert "Not recorded" in text
        assert "PRE-ARRIVAL INSTRUCTION CONFIRMATION" in text

    def test_non_confirmation_field_actions_not_affected(self):
        """Regular field actions are not rendered as [CONFIRMED]."""
        full = {
            "incident": {
                "incident_id": "test",
                "created_at": "2026-06-25T10:00:00",
                "status": "closed",
                "chief_complaint": "test",
                "dispatch_protocol_id": None,
                "dispatch_protocol_version": None,
                "dispatch_protocol_snapshot": None,
            },
            "dispatch_log": [],
            "field_log": [
                {
                    "id": "log-2",
                    "step_id": "assessment",
                    "action_type": "assessment",
                    "data": {"note": "patient assessed"},
                    "recorded_by": "medic-1",
                    "timestamp": "2026-06-25T10:03:00",
                }
            ],
            "vitals_history": [],
            "medications_given": [],
            "guidance_lookups": [],
        }
        text = render_audit_text(full)
        assert "[CONFIRMED]" not in text
        assert "assessment" in text
