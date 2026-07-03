"""
tests/test_protocol_choking.py

Phase 2.7 branch coverage for choking_airway_obstruction_v1, the second
of the three Phase 2.5 proving protocols. Same discipline as
test_protocol_runner.py: every defined branch walked to a terminal
outcome; an undefined answer asserted to raise, not default.

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
def choking_protocol():
    return _load_protocol_ignoring_governance("choking_airway_obstruction_v1.json")


def _walk(protocol, answers: list[str]):
    question = get_entry_question(protocol)
    result = None
    for answer in answers:
        result = submit_answer(protocol, question.question_id, answer)
        if result.terminal_outcome is not None:
            return result
        question = result.next_question
    return result


class TestChokingProtocolBranchCoverage:
    def test_unconscious_redirects_to_airway_compromise(self, choking_protocol):
        """Phase 3: outcome_route_to_other_protocol retired. Unconscious
        choking patients now route directly to P1_AIRWAY_COMPROMISE with
        specific resuscitation instructions (no dangling redirect)."""
        result = _walk(choking_protocol, ["no", "acknowledged"])
        assert result.terminal_outcome.priority_code == "P1_AIRWAY_COMPROMISE"
        assert result.terminal_outcome.recommended_unit_type == "ALS_AMBULANCE"
        assert len(result.terminal_outcome.pre_arrival_instructions) > 0

    def test_effective_cough_mild_obstruction(self, choking_protocol):
        result = _walk(choking_protocol, ["yes", "effective_cough_or_speech"])
        assert result.terminal_outcome.priority_code == "P2_AIRWAY_PARTIAL"
        assert result.terminal_outcome.recommended_unit_type == "BLS_AMBULANCE"

    def test_weak_cough_no_bystander(self, choking_protocol):
        result = _walk(choking_protocol, ["yes", "weak_or_silent_cough", "no"])
        assert result.terminal_outcome.priority_code == "P1_AIRWAY_COMPLETE"
        assert result.terminal_outcome.recommended_unit_type == "ALS_AMBULANCE"

    def test_no_sound_bystander_capable_infant(self, choking_protocol):
        result = _walk(choking_protocol, ["yes", "no_sound_at_all", "yes", "infant"])
        assert result.terminal_outcome.priority_code == "P1_AIRWAY_COMPLETE"
        assert "infant" in " ".join(result.terminal_outcome.pre_arrival_instructions).lower()

    def test_no_sound_bystander_capable_child_or_adult(self, choking_protocol):
        result = _walk(
            choking_protocol, ["yes", "no_sound_at_all", "yes", "child_or_adult"]
        )
        assert result.terminal_outcome.priority_code == "P1_AIRWAY_COMPLETE"
        assert "abdominal thrusts" in " ".join(
            result.terminal_outcome.pre_arrival_instructions
        ).lower()


class TestChokingOutOfScriptAnswerHardFail:
    def test_invalid_answer_to_entry_question_raises(self, choking_protocol):
        entry = get_entry_question(choking_protocol)
        with pytest.raises(OutOfScriptAnswerError) as exc_info:
            submit_answer(choking_protocol, entry.question_id, "kind_of")
        assert exc_info.value.submitted_answer == "kind_of"

    def test_invalid_select_option_raises(self, choking_protocol):
        entry = get_entry_question(choking_protocol)
        step1 = submit_answer(choking_protocol, entry.question_id, "yes")
        with pytest.raises(OutOfScriptAnswerError):
            submit_answer(
                choking_protocol, step1.next_question.question_id, "sort_of_coughing"
            )
