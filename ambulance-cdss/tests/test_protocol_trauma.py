"""tests/test_protocol_trauma.py.

Phase 2.7 branch coverage for major_trauma_mva_v1, the third of the three
Phase 2.5 proving protocols. Same discipline as the other protocol test
files: every defined branch walked to a terminal outcome; an undefined
answer asserted to raise, not default.

Loads the protocol directly from JSON, bypassing the registry's
governance gate — see tests/test_protocol_runner.py module docstring for
why that separation is deliberate (branch-logic correctness vs.
sign-off/selectability are independent concerns).
"""

from __future__ import annotations

import json

import pytest
from app.protocols.registry import DISPATCH_PROTOCOLS_DIR
from app.protocols.runner import OutOfScriptAnswerError, get_entry_question, submit_answer
from app.protocols.schema import DispatchProtocol, ProtocolQuestion, TerminalOutcome


def _load_protocol_ignoring_governance(filename: str) -> DispatchProtocol:
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
def trauma_protocol():
    return _load_protocol_ignoring_governance("major_trauma_mva_v1.json")


def _walk(protocol, answers: list[str]):
    question = get_entry_question(protocol)
    result = None
    for answer in answers:
        result = submit_answer(protocol, question.question_id, answer)
        if result.terminal_outcome is not None:
            return result
        question = result.next_question
    return result


class TestTraumaProtocolBranchCoverage:
    def test_unconscious_not_breathing_routes_to_cardiac_arrest(self, trauma_protocol):
        """Phase 3: unconscious trauma patients with absent/abnormal breathing
        now route to a specific P1_CARDIAC_ARREST outcome with CPR instructions
        (no dangling redirect).
        """
        result = _walk(trauma_protocol, ["no", "not_breathing_or_abnormal", "acknowledged"])
        assert result.terminal_outcome.priority_code == "P1_CARDIAC_ARREST"
        assert result.terminal_outcome.recommended_unit_type == "ALS_AMBULANCE"

    def test_unconscious_breathing_normally_proceeds_to_bleeding_check(self, trauma_protocol):
        result = _walk(trauma_protocol, ["no", "breathing_normally", "no", "no"])
        assert result.terminal_outcome.priority_code == "P3_TRAUMA_MINOR"

    def test_conscious_breathing_difficulty(self, trauma_protocol):
        result = _walk(trauma_protocol, ["yes", "no"])
        assert result.terminal_outcome.priority_code == "P1_TRAUMA_AIRWAY_COMPROMISE"
        assert result.terminal_outcome.recommended_unit_type == "ALS_AMBULANCE"

    def test_severe_bleeding_with_bystander_pressure(self, trauma_protocol):
        result = _walk(trauma_protocol, ["yes", "yes", "yes", "yes"])
        assert result.terminal_outcome.priority_code == "P1_TRAUMA_SEVERE_BLEEDING"
        assert "pressure" in " ".join(result.terminal_outcome.pre_arrival_instructions).lower()

    def test_severe_bleeding_no_bystander(self, trauma_protocol):
        result = _walk(trauma_protocol, ["yes", "yes", "yes", "no"])
        assert result.terminal_outcome.priority_code == "P1_TRAUMA_SEVERE_BLEEDING"
        assert (
            "cannot safely reach"
            in " ".join(result.terminal_outcome.pre_arrival_instructions).lower()
        )

    def test_no_bleeding_high_mechanism(self, trauma_protocol):
        result = _walk(trauma_protocol, ["yes", "yes", "no", "yes"])
        assert result.terminal_outcome.priority_code == "P2_TRAUMA_HIGH_MECHANISM"
        assert result.terminal_outcome.recommended_unit_type == "ALS_AMBULANCE"

    def test_no_bleeding_low_mechanism_minor(self, trauma_protocol):
        result = _walk(trauma_protocol, ["yes", "yes", "no", "no"])
        assert result.terminal_outcome.priority_code == "P3_TRAUMA_MINOR"
        assert result.terminal_outcome.recommended_unit_type == "BLS_AMBULANCE"


class TestTraumaOutOfScriptAnswerHardFail:
    def test_invalid_answer_to_entry_question_raises(self, trauma_protocol):
        entry = get_entry_question(trauma_protocol)
        with pytest.raises(OutOfScriptAnswerError) as exc_info:
            submit_answer(trauma_protocol, entry.question_id, "partially")
        assert exc_info.value.submitted_answer == "partially"

    def test_invalid_select_option_on_unconscious_branch_raises(self, trauma_protocol):
        entry = get_entry_question(trauma_protocol)
        step1 = submit_answer(trauma_protocol, entry.question_id, "no")
        with pytest.raises(OutOfScriptAnswerError):
            submit_answer(trauma_protocol, step1.next_question.question_id, "maybe_breathing")
