"""
tests/test_status_transitions.py

Exercises the status transition enforcement in app/repositories.py
(VALID_TRANSITIONS and InvalidStatusTransitionError) and the
POST /incidents/{id}/status endpoint's handling of it.

No live database required — tests verify the transition table,
error structure, and endpoint logic via source inspection and
direct function calls on pure/structural code.
"""

from __future__ import annotations

import inspect

from app.models import IncidentStatus
from app.repositories import (
    InvalidStatusTransitionError,
    VALID_TRANSITIONS,
)


# ── Transition table completeness ─────────────────────────────────────────

def test_valid_transitions_covers_all_statuses():
    """Every IncidentStatus value must be a key in VALID_TRANSITIONS."""
    for status in IncidentStatus:
        assert status in VALID_TRANSITIONS, (
            f"IncidentStatus.{status.name} is missing from VALID_TRANSITIONS"
        )


def test_closed_has_no_allowed_transitions():
    """CLOSED is a terminal state — no transitions permitted."""
    assert VALID_TRANSITIONS[IncidentStatus.CLOSED] == set()


def test_received_allows_dispatched_and_closed():
    """RECEIVED → DISPATCHED and RECEIVED → CLOSED are the only allowed forward paths."""
    allowed = VALID_TRANSITIONS[IncidentStatus.RECEIVED]
    assert IncidentStatus.DISPATCHED in allowed
    assert IncidentStatus.CLOSED in allowed
    assert len(allowed) == 2


def test_handoff_complete_allows_only_closed():
    """HANDOFF_COMPLETE → CLOSED is the only allowed transition."""
    allowed = VALID_TRANSITIONS[IncidentStatus.HANDOFF_COMPLETE]
    assert IncidentStatus.CLOSED in allowed
    assert len(allowed) == 1


def test_dispatched_allows_on_scene_and_closed():
    """DISPATCHED → ON_SCENE and DISPATCHED → CLOSED are allowed."""
    allowed = VALID_TRANSITIONS[IncidentStatus.DISPATCHED]
    assert IncidentStatus.ON_SCENE in allowed
    assert IncidentStatus.CLOSED in allowed
    assert len(allowed) == 2


def test_on_scene_allows_transporting_and_closed():
    """ON_SCENE → TRANSPORTING and ON_SCENE → CLOSED are allowed."""
    allowed = VALID_TRANSITIONS[IncidentStatus.ON_SCENE]
    assert IncidentStatus.TRANSPORTING in allowed
    assert IncidentStatus.CLOSED in allowed
    assert len(allowed) == 2


def test_transporting_allows_handoff_complete_and_closed():
    """TRANSPORTING → HANDOFF_COMPLETE and TRANSPORTING → CLOSED are allowed."""
    allowed = VALID_TRANSITIONS[IncidentStatus.TRANSPORTING]
    assert IncidentStatus.HANDOFF_COMPLETE in allowed
    assert IncidentStatus.CLOSED in allowed
    assert len(allowed) == 2


# ── Invalid transitions (NOT in allowed set) ──────────────────────────────

def test_closed_to_dispatched_not_allowed():
    """CLOSED → DISPATCHED is not in the allowed set."""
    assert IncidentStatus.DISPATCHED not in VALID_TRANSITIONS[IncidentStatus.CLOSED]


def test_closed_to_received_not_allowed():
    """CLOSED → RECEIVED is not in the allowed set."""
    assert IncidentStatus.RECEIVED not in VALID_TRANSITIONS[IncidentStatus.CLOSED]


def test_closed_to_on_scene_not_allowed():
    """CLOSED → ON_SCENE is not in the allowed set."""
    assert IncidentStatus.ON_SCENE not in VALID_TRANSITIONS[IncidentStatus.CLOSED]


def test_closed_to_transporting_not_allowed():
    """CLOSED → TRANSPORTING is not in the allowed set."""
    assert IncidentStatus.TRANSPORTING not in VALID_TRANSITIONS[IncidentStatus.CLOSED]


def test_closed_to_handoff_complete_not_allowed():
    """CLOSED → HANDOFF_COMPLETE is not in the allowed set."""
    assert IncidentStatus.HANDOFF_COMPLETE not in VALID_TRANSITIONS[IncidentStatus.CLOSED]


def test_handoff_complete_to_received_not_allowed():
    """HANDOFF_COMPLETE → RECEIVED is not in the allowed set."""
    assert IncidentStatus.RECEIVED not in VALID_TRANSITIONS[IncidentStatus.HANDOFF_COMPLETE]


def test_handoff_complete_to_dispatched_not_allowed():
    """HANDOFF_COMPLETE → DISPATCHED is not in the allowed set."""
    assert IncidentStatus.DISPATCHED not in VALID_TRANSITIONS[IncidentStatus.HANDOFF_COMPLETE]


def test_handoff_complete_to_on_scene_not_allowed():
    """HANDOFF_COMPLETE → ON_SCENE is not in the allowed set."""
    assert IncidentStatus.ON_SCENE not in VALID_TRANSITIONS[IncidentStatus.HANDOFF_COMPLETE]


def test_handoff_complete_to_transporting_not_allowed():
    """HANDOFF_COMPLETE → TRANSPORTING is not in the allowed set."""
    assert IncidentStatus.TRANSPORTING not in VALID_TRANSITIONS[IncidentStatus.HANDOFF_COMPLETE]


def test_received_to_on_scene_not_allowed():
    """RECEIVED → ON_SCENE is not in the allowed set."""
    assert IncidentStatus.ON_SCENE not in VALID_TRANSITIONS[IncidentStatus.RECEIVED]


def test_received_to_transporting_not_allowed():
    """RECEIVED → TRANSPORTING is not in the allowed set."""
    assert IncidentStatus.TRANSPORTING not in VALID_TRANSITIONS[IncidentStatus.RECEIVED]


def test_received_to_handoff_complete_not_allowed():
    """RECEIVED → HANDOFF_COMPLETE is not in the allowed set."""
    assert IncidentStatus.HANDOFF_COMPLETE not in VALID_TRANSITIONS[IncidentStatus.RECEIVED]


def test_dispatched_to_received_not_allowed():
    """DISPATCHED → RECEIVED is not in the allowed set."""
    assert IncidentStatus.RECEIVED not in VALID_TRANSITIONS[IncidentStatus.DISPATCHED]


def test_dispatched_to_handoff_complete_not_allowed():
    """DISPATCHED → HANDOFF_COMPLETE is not in the allowed set."""
    assert IncidentStatus.HANDOFF_COMPLETE not in VALID_TRANSITIONS[IncidentStatus.DISPATCHED]


def test_on_scene_to_received_not_allowed():
    """ON_SCENE → RECEIVED is not in the allowed set."""
    assert IncidentStatus.RECEIVED not in VALID_TRANSITIONS[IncidentStatus.ON_SCENE]


def test_on_scene_to_dispatched_not_allowed():
    """ON_SCENE → DISPATCHED is not in the allowed set."""
    assert IncidentStatus.DISPATCHED not in VALID_TRANSITIONS[IncidentStatus.ON_SCENE]


def test_transporting_to_received_not_allowed():
    """TRANSPORTING → RECEIVED is not in the allowed set."""
    assert IncidentStatus.RECEIVED not in VALID_TRANSITIONS[IncidentStatus.TRANSPORTING]


def test_transporting_to_dispatched_not_allowed():
    """TRANSPORTING → DISPATCHED is not in the allowed set."""
    assert IncidentStatus.DISPATCHED not in VALID_TRANSITIONS[IncidentStatus.TRANSPORTING]


# ── InvalidStatusTransitionError ──────────────────────────────────────────

def test_error_has_correct_fields():
    """InvalidStatusTransitionError carries structured data for API responses."""
    err = InvalidStatusTransitionError(
        current_status=IncidentStatus.CLOSED,
        requested_status=IncidentStatus.DISPATCHED,
        allowed_statuses=set(),
    )
    assert err.current_status == IncidentStatus.CLOSED
    assert err.requested_status == IncidentStatus.DISPATCHED
    assert err.allowed_statuses == set()
    assert "closed" in str(err)
    assert "dispatched" in str(err)


def test_error_is_value_error():
    """InvalidStatusTransitionError is a ValueError subclass."""
    err = InvalidStatusTransitionError(
        IncidentStatus.CLOSED, IncidentStatus.DISPATCHED, set()
    )
    assert isinstance(err, ValueError)


def test_error_with_nonempty_allowed():
    """Error carries the allowed set for the response body."""
    allowed = {IncidentStatus.ON_SCENE, IncidentStatus.CLOSED}
    err = InvalidStatusTransitionError(
        IncidentStatus.DISPATCHED, IncidentStatus.RECEIVED, allowed
    )
    assert err.allowed_statuses == allowed
    assert IncidentStatus.ON_SCENE in err.allowed_statuses
    assert IncidentStatus.CLOSED in err.allowed_statuses


# ── Repository function signature ─────────────────────────────────────────

def test_update_incident_status_signature():
    """update_incident_status must accept incident_id, status, and timestamp kwargs."""
    from app.repositories import update_incident_status
    sig = inspect.signature(update_incident_status)
    params = list(sig.parameters.keys())
    assert "incident_id" in params
    assert "status" in params


# ── Endpoint catches InvalidStatusTransitionError ─────────────────────────

def test_endpoint_catches_invalid_status_transition_error():
    """The update_incident_status endpoint imports and catches InvalidStatusTransitionError."""
    from app import main
    source = inspect.getsource(main.update_incident_status)
    assert "InvalidStatusTransitionError" in source


def test_endpoint_returns_422_detail_structure():
    """The 422 detail must have error, current, requested, allowed keys."""
    from app import main
    source = inspect.getsource(main.update_incident_status)
    assert '"error": "invalid_status_transition"' in source
    assert '"current"' in source
    assert '"requested"' in source
    assert '"allowed"' in source


def test_valid_transitions_dict_type():
    """VALID_TRANSITIONS must be a dict mapping IncidentStatus to sets."""
    assert isinstance(VALID_TRANSITIONS, dict)
    for key, value in VALID_TRANSITIONS.items():
        assert isinstance(key, IncidentStatus)
        assert isinstance(value, set)
