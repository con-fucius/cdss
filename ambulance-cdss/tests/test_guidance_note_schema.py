"""tests/test_guidance_note_schema.py.

Confirms the Mode 2 guidance_note field is loaded from protocol JSON and
that all three allow_guidance_lookup=true questions across the proving
protocol set have authored content — see docs/GOVERNANCE.md: Mode 2 must
never be a silent no-op for a gated question.

Loads protocols directly from JSON, bypassing the registry's governance
gate — see tests/test_protocol_runner.py module docstring for why that
separation is deliberate. Whether a gated question has an authored
guidance note is a content-completeness concern independent of whether
the protocol has real medical director sign-off yet (Phase 0.2, names
pending — see docs/PHASE_STATUS.md); both are checked, but by different
test files, against different gates.
"""

from __future__ import annotations

import json

from app.protocols.registry import DISPATCH_PROTOCOLS_DIR
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


_PROVING_PROTOCOL_FILES = [
    "cardiac_arrest_unresponsive_v1.json",
    "choking_airway_obstruction_v1.json",
    "major_trauma_mva_v1.json",
]


def test_all_guidance_gated_questions_have_authored_notes():
    gated_without_note = []
    for filename in _PROVING_PROTOCOL_FILES:
        protocol = _load_protocol_ignoring_governance(filename)
        for question in protocol.questions.values():
            if question.allow_guidance_lookup and not question.guidance_note:
                gated_without_note.append((protocol.protocol_id, question.question_id))

    assert gated_without_note == [], (
        f"Questions gated for guidance lookup with no authored note: {gated_without_note}"
    )


def test_non_gated_question_has_no_guidance_note_required():
    cardiac = _load_protocol_ignoring_governance("cardiac_arrest_unresponsive_v1.json")
    entry = cardiac.questions[cardiac.entry_question_id]
    assert entry.allow_guidance_lookup is False
    # Absence of a note on a non-gated question is fine — only gated
    # questions are required to have one.
