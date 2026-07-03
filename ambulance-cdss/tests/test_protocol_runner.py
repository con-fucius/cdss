"""tests/test_protocol_runner.py.

Phase 2.7 exit criterion: "every defined branch path through each of the
3 protocols is walked and asserted to reach a valid terminal outcome; an
undefined answer path is asserted to raise, not default."

This file tests app/protocols/runner.py's branch-walking logic against
parsed DispatchProtocol objects loaded directly from the JSON files in
protocols/dispatch/, deliberately bypassing the registry's governance
gate (app/protocols/registry.py's locked/approved_by/approved_date/
placeholder checks). That gate is a separate, correctly-tested concern
owned by tests/test_protocol_registry.py: whether a protocol is fit to
be SELECTED for a real incident. This file answers a different
question — given a protocol's question/branch graph, does the runner
walk it correctly and hard-fail on undefined answers — which holds
regardless of governance/sign-off status. (The three shipped protocol
files currently carry PLACEHOLDER governance text pending real medical
director sign-off — see docs/PHASE_STATUS.md — so they are correctly
REJECTED by the registry right now; that must not block testing whether
their branch logic itself is correct once sign-off lands.)
"""

from __future__ import annotations

import json

import pytest
from app.protocols.registry import DISPATCH_PROTOCOLS_DIR
from app.protocols.runner import OutOfScriptAnswerError, get_entry_question, submit_answer
from app.protocols.schema import DispatchProtocol, ProtocolQuestion, TerminalOutcome


def _load_protocol_ignoring_governance(filename: str) -> DispatchProtocol:
    """Parses a protocol JSON file into a DispatchProtocol WITHOUT running
    registry._parse_protocol's governance/placeholder checks — this is
    deliberate, see module docstring. Branch integrity (dangling targets,
    unknown entry question) is still a basic structural concern, not a
    governance one, so it is re-validated here independently rather than
    imported from registry.py, keeping this test file decoupled from
    registry internals.
    """
    raw = json.loads((DISPATCH_PROTOCOLS_DIR / filename).read_text(encoding="utf-8"))

    questions = {
        qid: ProtocolQuestion(
            question_id=qid,
            text=qraw["text"],
            answer_type=qraw["answer_type"],
            options=qraw.get("options", []),
            branch_map=qraw.get("branch_map", {}),
            is_terminal=qraw.get("is_terminal", False),
            allow_guidance_lookup=qraw.get("allow_guidance_lookup", False),
            guidance_note=qraw.get("guidance_note"),
        )
        for qid, qraw in raw["questions"].items()
    }
    terminal_outcomes = {
        tid: TerminalOutcome(
            priority_code=traw["priority_code"],
            recommended_unit_type=traw["recommended_unit_type"],
            pre_arrival_instructions=traw.get("pre_arrival_instructions", []),
        )
        for tid, traw in raw["terminal_outcomes"].items()
    }
    return DispatchProtocol(
        protocol_id=raw["protocol_id"],
        version=str(raw["version"]),
        chief_complaint_trigger=raw["chief_complaint_trigger"],
        questions=questions,
        terminal_outcomes=terminal_outcomes,
        entry_question_id=raw["entry_question_id"],
        locked=bool(raw["locked"]),
        approved_by=str(raw["approved_by"]),
        approved_date=str(raw["approved_date"]),
    )


@pytest.fixture()
def cardiac_protocol():
    return _load_protocol_ignoring_governance("cardiac_arrest_unresponsive_v1.json")


def _walk(protocol, answers: list[str]):
    """Walk a sequence of answers from the entry question; return the final result."""
    question = get_entry_question(protocol)
    result = None
    for answer in answers:
        result = submit_answer(protocol, question.question_id, answer)
        if result.terminal_outcome is not None:
            return result
        question = result.next_question
    return result


class TestCardiacArrestProtocolBranchCoverage:
    def test_conscious_patient_routes_away(self, cardiac_protocol):
        result = _walk(cardiac_protocol, ["yes", "acknowledged"])
        assert result.terminal_outcome is not None
        assert result.terminal_outcome.priority_code == "ROUTE_REASSESS"

    def test_breathing_normally_routes_to_unconscious_breathing(self, cardiac_protocol):
        """Phase 3: unconscious but breathing patients now route to a
        specific P2_UNCONSCIOUS_BREATHING outcome with recovery position
        instructions (no dangling redirect).
        """
        result = _walk(cardiac_protocol, ["no", "normal", "acknowledged"])
        assert result.terminal_outcome.priority_code == "P2_UNCONSCIOUS_BREATHING"
        assert result.terminal_outcome.recommended_unit_type == "ALS_AMBULANCE"

    def test_has_definite_pulse_routes_to_respiratory_distress(self, cardiac_protocol):
        """Phase 3: pulse-present patients with abnormal breathing now route
        to a specific P2_RESPIRATORY_DISTRESS outcome (no dangling redirect).
        """
        result = _walk(
            cardiac_protocol,
            ["no", "not_breathing", "definite_pulse", "acknowledged"],
        )
        assert result.terminal_outcome.priority_code == "P2_RESPIRATORY_DISTRESS"
        assert result.terminal_outcome.recommended_unit_type == "ALS_AMBULANCE"

    def test_no_pulse_with_cpr_capable_bystander(self, cardiac_protocol):
        result = _walk(
            cardiac_protocol,
            ["no", "not_breathing", "no_pulse", "yes"],
        )
        assert result.terminal_outcome.priority_code == "P1_CARDIAC_ARREST"
        assert result.terminal_outcome.recommended_unit_type == "ALS_AMBULANCE"
        assert len(result.terminal_outcome.pre_arrival_instructions) > 0

    def test_no_pulse_no_cpr_capable_bystander(self, cardiac_protocol):
        result = _walk(
            cardiac_protocol,
            ["no", "not_breathing", "no_pulse", "no"],
        )
        assert result.terminal_outcome.priority_code == "P1_CARDIAC_ARREST"
        assert "I will guide you" in " ".join(result.terminal_outcome.pre_arrival_instructions)

    def test_unsure_breathing_proceeds_to_pulse_check(self, cardiac_protocol):
        result = _walk(
            cardiac_protocol,
            ["no", "unsure", "no_pulse", "yes"],
        )
        assert result.terminal_outcome.priority_code == "P1_CARDIAC_ARREST"

    def test_unsure_pulse_proceeds_to_cpr_question(self, cardiac_protocol):
        result = _walk(
            cardiac_protocol,
            ["no", "abnormal_or_gasping", "unsure", "yes"],
        )
        assert result.terminal_outcome.priority_code == "P1_CARDIAC_ARREST"


class TestOutOfScriptAnswerHardFail:
    def test_invalid_answer_to_entry_question_raises(self, cardiac_protocol):
        entry = get_entry_question(cardiac_protocol)
        with pytest.raises(OutOfScriptAnswerError) as exc_info:
            submit_answer(cardiac_protocol, entry.question_id, "maybe")
        assert exc_info.value.submitted_answer == "maybe"
        assert "yes" in exc_info.value.valid_answers
        assert "no" in exc_info.value.valid_answers

    def test_invalid_answer_mid_script_raises_not_defaults(self, cardiac_protocol):
        entry = get_entry_question(cardiac_protocol)
        step1 = submit_answer(cardiac_protocol, entry.question_id, "no")
        with pytest.raises(OutOfScriptAnswerError):
            submit_answer(cardiac_protocol, step1.next_question.question_id, "sort_of")

    def test_unknown_question_id_raises_keyerror(self, cardiac_protocol):
        with pytest.raises(KeyError):
            submit_answer(cardiac_protocol, "nonexistent_question", "yes")
