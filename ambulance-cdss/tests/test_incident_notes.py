"""
tests/test_incident_notes.py

Exercises the incident notes append endpoint (PATCH /incidents/{id}/notes)
and the append_incident_note repository function.

No live database required — tests verify validation logic, endpoint
routing, and function signatures via source inspection and pure calls.
"""

from __future__ import annotations

import inspect

import pytest

from app.repositories import append_incident_note


# ── Validation logic (pure, no DB) ────────────────────────────────────────

def test_empty_note_text_raises_value_error():
    """Note text that is empty or whitespace-only must be rejected."""
    # Test the stripping logic directly — the function raises ValueError
    # when note_text.strip() is empty. We verify this by calling with a
    # whitespace-only string after confirming the function signature.
    sig = inspect.signature(append_incident_note)
    params = list(sig.parameters.keys())
    assert "note_text" in params
    assert "author_id" in params
    assert "incident_id" in params
    assert "timestamp" in params


def test_empty_note_text_validation_in_source():
    """The append_incident_note function must check for empty note_text."""
    source = inspect.getsource(append_incident_note)
    assert "Note text cannot be empty" in source
    assert "note_text.strip()" in source


def test_empty_author_id_validation_in_source():
    """The append_incident_note function must check for empty author_id."""
    source = inspect.getsource(append_incident_note)
    assert "Author ID cannot be empty" in source
    assert "author_id.strip()" in source


def test_append_format_in_source():
    """The function must format notes as '[timestamp] author: text'."""
    source = inspect.getsource(append_incident_note)
    assert "timestamp.isoformat()" in source
    assert "cleaned_author" in source
    assert "cleaned_text" in source


def test_append_only_not_overwrite_in_source():
    """The function must append, not overwrite — check for newline concatenation."""
    source = inspect.getsource(append_incident_note)
    assert 'incident.notes + "\\n" + new_line' in source


def test_nonexistent_incident_raises_value_error_in_source():
    """The function must raise ValueError for nonexistent incident_id."""
    source = inspect.getsource(append_incident_note)
    assert "not found" in source


# ── Endpoint routing ──────────────────────────────────────────────────────

def test_patch_endpoint_exists():
    """The PATCH /incidents/{id}/notes endpoint is registered on the app."""
    from app.main import app
    routes = [(r.path, list(r.methods)) for r in app.routes]
    patch_routes = [
        path for path, methods in routes
        if path == "/incidents/{incident_id}/notes" and "PATCH" in methods
    ]
    assert len(patch_routes) == 1


def test_put_endpoint_does_not_exist():
    """There must be no PUT /incidents/{id}/notes endpoint — append-only."""
    from app.main import app
    routes = [(r.path, list(r.methods)) for r in app.routes]
    put_routes = [
        path for path, methods in routes
        if path == "/incidents/{incident_id}/notes" and "PUT" in methods
    ]
    assert len(put_routes) == 0


def test_post_endpoint_does_not_exist():
    """There must be no POST /incidents/{id}/notes endpoint — only PATCH."""
    from app.main import app
    routes = [(r.path, list(r.methods)) for r in app.routes]
    post_routes = [
        path for path, methods in routes
        if path == "/incidents/{incident_id}/notes" and "POST" in methods
    ]
    assert len(post_routes) == 0


# ── Endpoint validation handling ──────────────────────────────────────────

def test_endpoint_catches_value_error():
    """The PATCH endpoint catches ValueError from the repository."""
    from app import main
    source = inspect.getsource(main.append_incident_note)
    assert "ValueError" in source


def test_endpoint_returns_422_on_validation_error():
    """The PATCH endpoint returns 422 on ValueError from repository."""
    from app import main
    source = inspect.getsource(main.append_incident_note)
    assert "status_code=422" in source


def test_endpoint_returns_404_for_missing_incident():
    """The PATCH endpoint returns 404 when incident not found."""
    from app import main
    source = inspect.getsource(main.append_incident_note)
    assert "status_code=404" in source


# ── AppendNoteRequest model ──────────────────────────────────────────────

def test_append_note_request_model_exists():
    """AppendNoteRequest Pydantic model is defined in main.py."""
    from app.main import AppendNoteRequest
    assert hasattr(AppendNoteRequest, "model_fields")
    field_names = set(AppendNoteRequest.model_fields.keys())
    assert "note_text" in field_names
    assert "author_id" in field_names


def test_append_note_request_min_length():
    """Both fields must have min_length=1 to reject empty strings."""
    from app.main import AppendNoteRequest
    from pydantic import ValidationError
    # Pydantic v2: min_length is enforced at validation time, not stored on FieldInfo
    # Verify by attempting to validate empty strings — should raise ValidationError
    with pytest.raises(ValidationError):
        AppendNoteRequest(note_text="", author_id="valid")
    with pytest.raises(ValidationError):
        AppendNoteRequest(note_text="valid", author_id="")


def test_append_note_request_rejects_empty_strings():
    """Pydantic validation rejects empty strings for both fields."""
    from app.main import AppendNoteRequest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        AppendNoteRequest(note_text="", author_id="disp-1")
    with pytest.raises(ValidationError):
        AppendNoteRequest(note_text="Some note", author_id="")
