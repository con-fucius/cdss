"""tests/test_field_protocol.py.

Phase 4 — field protocol registry loading and the field runner's checklist
state mechanics. Deliberately tests a DIFFERENT contract than
tests/test_protocol_runner.py (Mode 1): no hard-fail on out-of-script
input here, because there is no script — see app/protocols/field_runner.py
module docstring. What IS tested: structural load-time validation
(duplicate step_id, missing fields, invalid action_type all rejected),
correct step ordering, and that state reconstruction from field_log
entries is accurate and independent of any client-held state.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from app.protocols.field_registry import (
    FieldProtocolRegistry,
    FieldProtocolRejectedError,
    _parse_field_protocol,
)
from app.protocols.field_runner import FieldRunState, rebuild_from_field_log
from app.protocols.schema import FieldProtocol, FieldProtocolStep


@pytest.fixture()
def field_registry_instance():
    registry = FieldProtocolRegistry()
    registry.load_all()
    return registry


@pytest.fixture()
def cardiac_field_protocol(field_registry_instance):
    protocol = field_registry_instance.get("field_cardiac_arrest_v1")
    assert protocol is not None
    return protocol


class TestFieldRegistryLoading:
    def test_field_cardiac_arrest_loads_with_no_rejections(self, field_registry_instance):
        assert field_registry_instance.list_rejected() == []
        assert field_registry_instance.get("field_cardiac_arrest_v1") is not None

    def test_loaded_protocol_has_ordered_steps(self, cardiac_field_protocol):
        step_ids = [s.step_id for s in cardiac_field_protocol.steps]
        assert step_ids == [
            "f1_scene_safety",
            "f2_confirm_arrest",
            "f3_start_cpr",
            "f4_rhythm_check",
            "f5_airway",
            "f6_iv_access",
            "f7_vitals_during_arrest",
            "f8_disposition",
        ]

    def test_find_by_presentation_matches_loosely(self, field_registry_instance):
        found = field_registry_instance.find_by_presentation(
            "patient found in cardiac arrest, unresponsive"
        )
        assert found is not None
        assert found.protocol_id == "field_cardiac_arrest_v1"


class TestFieldProtocolStructuralValidation:
    def test_duplicate_step_id_rejected(self):
        raw = {
            "protocol_id": "x",
            "version": "1.0.0",
            "disease_or_presentation": "test",
            "steps": [
                {"step_id": "s1", "title": "A", "action_type": "assessment"},
                {"step_id": "s1", "title": "B", "action_type": "assessment"},
            ],
        }
        with pytest.raises(FieldProtocolRejectedError, match="duplicate step_id"):
            _parse_field_protocol(raw, Path("test.json"))

    def test_missing_step_id_rejected(self):
        raw = {
            "protocol_id": "x",
            "version": "1.0.0",
            "disease_or_presentation": "test",
            "steps": [{"title": "A", "action_type": "assessment"}],
        }
        with pytest.raises(FieldProtocolRejectedError, match="missing step_id"):
            _parse_field_protocol(raw, Path("test.json"))

    def test_invalid_action_type_rejected(self):
        raw = {
            "protocol_id": "x",
            "version": "1.0.0",
            "disease_or_presentation": "test",
            "steps": [{"step_id": "s1", "title": "A", "action_type": "not_a_real_type"}],
        }
        with pytest.raises(FieldProtocolRejectedError, match="invalid action_type"):
            _parse_field_protocol(raw, Path("test.json"))

    def test_no_steps_rejected(self):
        raw = {
            "protocol_id": "x",
            "version": "1.0.0",
            "disease_or_presentation": "test",
            "steps": [],
        }
        with pytest.raises(FieldProtocolRejectedError, match="no steps defined"):
            _parse_field_protocol(raw, Path("test.json"))


@pytest.fixture()
def small_protocol():
    return FieldProtocol(
        protocol_id="test_protocol",
        version="1.0.0",
        disease_or_presentation="test",
        steps=[
            FieldProtocolStep(step_id="s1", title="Step 1", action_type="assessment"),
            FieldProtocolStep(step_id="s2", title="Step 2", action_type="intervention"),
            FieldProtocolStep(step_id="s3", title="Step 3", action_type="disposition"),
        ],
    )


class TestFieldRunState:
    def test_all_steps_start_pending(self, small_protocol):
        state = FieldRunState(protocol=small_protocol)
        assert state.next_pending_step().step_id == "s1"
        assert state.is_complete() is False

    def test_mark_step_advances_next_pending(self, small_protocol):
        state = FieldRunState(protocol=small_protocol)
        state.mark_step("s1", "done")
        assert state.next_pending_step().step_id == "s2"

    def test_out_of_order_marking_is_permitted_not_a_hard_fail(self, small_protocol):
        """Deliberately confirms the OPPOSITE of Mode 1's hard-fail rule —
        see module docstring. Marking s3 before s1/s2 must not raise.
        """
        state = FieldRunState(protocol=small_protocol)
        state.mark_step("s3", "done")  # out of order — must not raise
        assert state.steps["s3"].status == "done"
        assert state.next_pending_step().step_id == "s1"

    def test_skipped_and_not_applicable_count_toward_completion(self, small_protocol):
        state = FieldRunState(protocol=small_protocol)
        state.mark_step("s1", "done")
        state.mark_step("s2", "skipped")
        state.mark_step("s3", "not_applicable")
        assert state.is_complete() is True

    def test_invalid_status_raises(self, small_protocol):
        state = FieldRunState(protocol=small_protocol)
        with pytest.raises(ValueError, match="Invalid step status"):
            state.mark_step("s1", "bogus_status")

    def test_unknown_step_id_raises(self, small_protocol):
        state = FieldRunState(protocol=small_protocol)
        with pytest.raises(KeyError):
            state.mark_step("does_not_exist", "done")

    def test_summary_preserves_protocol_order(self, small_protocol):
        state = FieldRunState(protocol=small_protocol)
        state.mark_step("s2", "done")
        summary = state.summary()
        assert [s["step_id"] for s in summary] == ["s1", "s2", "s3"]
        assert summary[1]["status"] == "done"
        assert summary[0]["status"] == "pending"


class TestRebuildFromFieldLog:
    def test_reconstructs_done_steps_from_log_entries(self, small_protocol):
        field_log = [
            {"step_id": "s1", "action_type": "assessment", "data": {}},
            {"step_id": "s2", "action_type": "intervention", "data": {}},
        ]
        state = rebuild_from_field_log(small_protocol, field_log)
        assert state.steps["s1"].status == "done"
        assert state.steps["s2"].status == "done"
        assert state.steps["s3"].status == "pending"
        assert state.next_pending_step().step_id == "s3"

    def test_empty_log_means_all_pending(self, small_protocol):
        state = rebuild_from_field_log(small_protocol, [])
        assert state.is_complete() is False
        assert all(s.status == "pending" for s in state.steps.values())

    def test_log_entries_for_unknown_step_ids_are_ignored_not_errors(self, small_protocol):
        """A field_log row referencing a step_id outside the current protocol
        (e.g. patient was switched to a different field protocol mid-call)
        must not crash state reconstruction.
        """
        field_log = [
            {"step_id": "some_other_protocols_step", "action_type": "assessment", "data": {}}
        ]
        state = rebuild_from_field_log(small_protocol, field_log)
        assert all(s.status == "pending" for s in state.steps.values())

    def test_reconstruction_is_independent_of_log_order(self, small_protocol):
        field_log_a = [
            {"step_id": "s2", "action_type": "intervention", "data": {}},
            {"step_id": "s1", "action_type": "assessment", "data": {}},
        ]
        field_log_b = list(reversed(field_log_a))
        state_a = rebuild_from_field_log(small_protocol, field_log_a)
        state_b = rebuild_from_field_log(small_protocol, field_log_b)
        assert state_a.summary() == state_b.summary()
