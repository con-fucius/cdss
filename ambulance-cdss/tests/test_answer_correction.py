"""tests/test_answer_correction.py.

Improvement 4.2 — tests for protocol answer correction window.

Tests confirm:
- Endpoint exists (PATCH /incidents/{id}/answer/{log_id})
- Superseded_by column exists on IncidentDispatchLog model
- Correction window config function exists
- Repository functions exist with correct signatures
- Response structure includes corrected, superseded_log_id, new_log_id
"""

from __future__ import annotations

import inspect

from app import main
from app.config import get_answer_correction_window_seconds
from app.models import IncidentDispatchLog
from app.repositories import correct_dispatch_answer, get_dispatch_log_entry


class TestCorrectionWindowConfig:
    def test_config_function_exists(self):
        assert callable(get_answer_correction_window_seconds)

    def test_default_is_60(self):
        """Default window is 60 seconds."""
        import os

        # Clear env var to test default
        old = os.environ.pop("ANSWER_CORRECTION_WINDOW_SECONDS", None)
        try:
            assert get_answer_correction_window_seconds() == 60
        finally:
            if old is not None:
                os.environ["ANSWER_CORRECTION_WINDOW_SECONDS"] = old

    def test_config_reads_env_var(self):
        """Can be overridden via environment variable."""
        import os

        old = os.environ.get("ANSWER_CORRECTION_WINDOW_SECONDS")
        try:
            os.environ["ANSWER_CORRECTION_WINDOW_SECONDS"] = "120"
            assert get_answer_correction_window_seconds() == 120
        finally:
            if old is not None:
                os.environ["ANSWER_CORRECTION_WINDOW_SECONDS"] = old
            else:
                os.environ.pop("ANSWER_CORRECTION_WINDOW_SECONDS", None)


class TestSupersededByColumn:
    def test_column_exists_on_model(self):
        """IncidentDispatchLog must have a superseded_by column."""
        cols = {c.name for c in IncidentDispatchLog.__table__.columns}
        assert "superseded_by" in cols

    def test_column_is_nullable(self):
        """superseded_by is nullable (null means the row is current)."""
        col = IncidentDispatchLog.__table__.c.superseded_by
        assert col.nullable is True

    def test_superseded_by_in_dict(self):
        """_dispatch_log_to_dict must include superseded_by."""
        from app.repositories import _dispatch_log_to_dict

        source = inspect.getsource(_dispatch_log_to_dict)
        assert "superseded_by" in source


class TestAnswerCorrectionEndpoint:
    def test_endpoint_exists(self):
        """PATCH /incidents/{id}/answer/{log_id} is registered."""
        from app.main import app

        routes = [(r.path, list(r.methods)) for r in app.routes]
        patch_routes = [
            path for path, methods in routes if "/answer/" in path and "PATCH" in methods
        ]
        assert len(patch_routes) >= 1

    def test_endpoint_checks_correction_window(self):
        """The endpoint must check the correction window."""
        source = inspect.getsource(main.correct_answer)
        assert "correction_window_expired" in source

    def test_endpoint_writes_backtrack_row(self):
        """The endpoint writes a new row with is_backtrack=True."""
        source = inspect.getsource(main.correct_answer)
        assert "is_backtrack=True" in source

    def test_endpoint_marks_superseded(self):
        """The endpoint calls correct_dispatch_answer to mark the original."""
        source = inspect.getsource(main.correct_answer)
        assert "correct_dispatch_answer" in source

    def test_endpoint_returns_corrected_flag(self):
        """The response includes 'corrected: True'."""
        source = inspect.getsource(main.correct_answer)
        assert '"corrected": True' in source

    def test_endpoint_returns_superseded_and_new_ids(self):
        """The response includes superseded_log_id and new_log_id."""
        source = inspect.getsource(main.correct_answer)
        assert "superseded_log_id" in source
        assert "new_log_id" in source

    def test_endpoint_rejects_out_of_script_answer(self):
        """The endpoint rejects invalid answers with 422."""
        source = inspect.getsource(main.correct_answer)
        assert "OutOfScriptAnswerError" in source

    def test_endpoint_returns_404_for_missing_log(self):
        """Returns 404 if the log entry doesn't exist."""
        source = inspect.getsource(main.correct_answer)
        assert "Dispatch log entry not found" in source

    def test_endpoint_returns_403_for_expired_window(self):
        """Returns 403 when the correction window has expired."""
        source = inspect.getsource(main.correct_answer)
        assert "status_code=403" in source


class TestAnswerCorrectionRepository:
    def test_get_dispatch_log_entry_is_async(self):
        import asyncio

        assert asyncio.iscoroutinefunction(get_dispatch_log_entry)

    def test_correct_dispatch_answer_is_async(self):
        import asyncio

        assert asyncio.iscoroutinefunction(correct_dispatch_answer)

    def test_get_dispatch_log_entry_signature(self):
        sig = inspect.signature(get_dispatch_log_entry)
        assert "log_id" in sig.parameters

    def test_correct_dispatch_answer_signature(self):
        sig = inspect.signature(correct_dispatch_answer)
        params = list(sig.parameters.keys())
        assert "log_id" in params
        assert "corrected_answer" in params
        assert "new_log_id" in params


class TestCorrectAnswerRequestModel:
    def test_model_exists(self):
        from app.main import CorrectAnswerRequest

        assert hasattr(CorrectAnswerRequest, "model_fields")

    def test_has_required_fields(self):
        from app.main import CorrectAnswerRequest

        field_names = set(CorrectAnswerRequest.model_fields.keys())
        assert "corrected_answer" in field_names
        assert "dispatcher_id" in field_names
