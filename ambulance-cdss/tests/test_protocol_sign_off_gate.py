"""tests/test_protocol_sign_off_gate.py.

EPIC 9.1 — Asserts that protocols with placeholder governance values
("Dev Setup", "TBD", etc.) are rejected at load time.
"""

from __future__ import annotations

import json

import pytest

from app.protocols.registry import DISPATCH_PROTOCOLS_DIR
from app.protocols.schema import DispatchProtocol


def _load_raw(filename: str) -> dict:
    return json.loads((DISPATCH_PROTOCOLS_DIR / filename).read_text(encoding="utf-8"))


class TestGovernanceGate:
    """Verify blocked governance values reject protocols."""

    def test_blocked_values_include_dev_setup(self):
        blocked = DispatchProtocol._BLOCKED_GOVERNANCE_VALUES
        assert "dev setup" in blocked
        assert "tbd" in blocked
        assert "placeholder" in blocked

    def test_dev_setup_rejects_protocol(self):
        proto = DispatchProtocol(
            protocol_id="test", version="1.0.0",
            chief_complaint_trigger=["test"], questions={}, terminal_outcomes={},
            entry_question_id="q1", locked=True,
            approved_by="Dev Setup", approved_date="2026-07-03",
        )
        assert proto.is_governance_complete() is False

    def test_real_approver_accepts_protocol(self):
        proto = DispatchProtocol(
            protocol_id="test", version="1.0.0",
            chief_complaint_trigger=["test"], questions={}, terminal_outcomes={},
            entry_question_id="q1", locked=True,
            approved_by="Dr. Jane Mwangi", approved_date="2026-07-03",
        )
        assert proto.is_governance_complete() is True

    def test_empty_approved_by_rejects(self):
        proto = DispatchProtocol(
            protocol_id="test", version="1.0.0",
            chief_complaint_trigger=["test"], questions={}, terminal_outcomes={},
            entry_question_id="q1", locked=True,
            approved_by="", approved_date="2026-07-03",
        )
        assert proto.is_governance_complete() is False

    def test_unlocked_protocol_rejects(self):
        proto = DispatchProtocol(
            protocol_id="test", version="1.0.0",
            chief_complaint_trigger=["test"], questions={}, terminal_outcomes={},
            entry_question_id="q1", locked=False,
            approved_by="Dr. Jane Mwangi", approved_date="2026-07-03",
        )
        assert proto.is_governance_complete() is False

    @pytest.mark.parametrize("filename", [
        "cardiac_arrest_unresponsive_v1.json",
    ])
    def test_existing_protocols_have_placeholder_governance(self, filename):
        """Existing protocol files still have placeholder governance —
        confirms the gate is correctly blocking them."""
        raw = _load_raw(filename)
        approved_by = raw.get("approved_by", "")
        # These should be blocked until real sign-off
        blocked = DispatchProtocol._BLOCKED_GOVERNANCE_VALUES
        assert approved_by.strip().lower() in blocked or approved_by.strip() == "", (
            f"Protocol {filename} has approved_by={approved_by!r} which is "
            "not in the blocked list — if this is a real sign-off, update the test."
        )
