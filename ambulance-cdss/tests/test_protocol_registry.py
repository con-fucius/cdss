"""
tests/test_protocol_registry.py

Exercises the governance enforcement in app/protocols/registry.py.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.protocols.registry import ProtocolRegistry, ProtocolRejectedError, _parse_protocol


GOVERNANCE_COMPLETE = {
    "protocol_id": "test_protocol",
    "version": "1.0.0",
    "locked": True,
    "approved_by": "Dr. Test Director",
    "approved_date": "2026-06-01",
    "chief_complaint_trigger": ["test trigger"],
    "entry_question_id": "q1",
    "questions": {
        "q1": {
            "text": "Q1?",
            "answer_type": "yes_no",
            "branch_map": {"yes": "outcome_a", "no": "outcome_a"},
        }
    },
    "terminal_outcomes": {
        "outcome_a": {
            "priority_code": "P3",
            "recommended_unit_type": "BLS_AMBULANCE",
            "pre_arrival_instructions": [],
        }
    },
}


def test_governance_complete_protocol_loads(tmp_path: Path):
    path = tmp_path / "ok.json"
    path.write_text(json.dumps(GOVERNANCE_COMPLETE))
    protocol = _parse_protocol(GOVERNANCE_COMPLETE, path)
    assert protocol.is_governance_complete()


@pytest.mark.parametrize(
    "missing_field",
    ["locked", "approved_by", "approved_date", "version"],
)
def test_missing_governance_field_rejected(tmp_path: Path, missing_field: str):
    raw = dict(GOVERNANCE_COMPLETE)
    if missing_field == "locked":
        raw["locked"] = False
    else:
        raw[missing_field] = ""
    path = tmp_path / "bad.json"
    with pytest.raises(ProtocolRejectedError):
        _parse_protocol(raw, path)


def test_dangling_branch_target_rejected(tmp_path: Path):
    raw = dict(GOVERNANCE_COMPLETE)
    raw["questions"] = {
        "q1": {
            "text": "Q1?",
            "answer_type": "yes_no",
            "branch_map": {"yes": "nonexistent_target", "no": "outcome_a"},
        }
    }
    path = tmp_path / "dangling.json"
    with pytest.raises(ProtocolRejectedError, match="branch integrity"):
        _parse_protocol(raw, path)


def test_placeholder_approved_by_rejected(tmp_path: Path):
    """
    A non-empty but literally placeholder approved_by must be rejected,
    not treated as governance-complete — see
    DispatchProtocol.is_governance_complete docstring. Plain truthiness
    alone is not a sufficient check; "PLACEHOLDER ..." is non-empty.
    """
    raw = dict(GOVERNANCE_COMPLETE)
    raw["approved_by"] = "PLACEHOLDER — medical director name pending Phase 0.2 decision"
    path = tmp_path / "placeholder_by.json"
    with pytest.raises(ProtocolRejectedError, match="governance fields incomplete"):
        _parse_protocol(raw, path)


def test_placeholder_approved_date_rejected(tmp_path: Path):
    raw = dict(GOVERNANCE_COMPLETE)
    raw["approved_date"] = "PLACEHOLDER — pending real sign-off, do not treat as production-approved"
    path = tmp_path / "placeholder_date.json"
    with pytest.raises(ProtocolRejectedError, match="governance fields incomplete"):
        _parse_protocol(raw, path)


def test_placeholder_check_is_case_insensitive(tmp_path: Path):
    raw = dict(GOVERNANCE_COMPLETE)
    raw["approved_by"] = "placeholder, lowercase variant"
    path = tmp_path / "placeholder_lower.json"
    with pytest.raises(ProtocolRejectedError, match="governance fields incomplete"):
        _parse_protocol(raw, path)


def test_registry_loads_real_dispatch_directory():
    """
    The three shipped dispatch protocol JSON files
    (cardiac_arrest_unresponsive_v1, choking_airway_obstruction_v1,
    major_trauma_mva_v1) all currently carry literal PLACEHOLDER text in
    approved_by/approved_date pending the real named doctor + medical
    director sign-off (Phase 0.1/0.2, resolved as in-house authorship,
    names to be supplied separately — see docs/PHASE_STATUS.md). They are
    therefore correctly REJECTED at load time, not active, until that
    text is replaced with real approvals. This test asserts the current,
    correct, safe state: zero active protocols, three rejections, each
    rejection naming a real file. Once real approvals are substituted in
    those JSON files, this test must be updated to assert the opposite.
    """
    registry = ProtocolRegistry()
    registry.load_all()
    active_ids = [p["protocol_id"] for p in registry.list_active()]
    rejected_files = [r["file"] for r in registry.list_rejected()]

    assert "cardiac_arrest_unresponsive_v1" not in active_ids
    assert "cardiac_arrest_unresponsive_v1.json" in rejected_files
    assert "choking_airway_obstruction_v1.json" in rejected_files
    assert "major_trauma_mva_v1.json" in rejected_files
    for rejection in registry.list_rejected():
        assert "governance fields incomplete" in rejection["reason"]


# ── find_by_chief_complaint ───────────────────────────────────────────────────

def _make_registry_with_protocols(protocols: list, tmp_path: Path) -> ProtocolRegistry:
    """Utility: write protocol files to a temp dir and load them."""
    import json
    from app.protocols.registry import ProtocolRegistry
    for proto in protocols:
        path = tmp_path / f"{proto['protocol_id']}.json"
        path.write_text(json.dumps(proto))
    reg = ProtocolRegistry(protocols_dir=tmp_path)
    reg.load_all()
    return reg


def _protocol_fixture(protocol_id: str, triggers: list) -> dict:
    base = dict(GOVERNANCE_COMPLETE)
    base["protocol_id"] = protocol_id
    base["chief_complaint_trigger"] = triggers
    return base


def test_find_by_chief_complaint_exact_word_match(tmp_path: Path):
    reg = _make_registry_with_protocols(
        [_protocol_fixture("cardiac", ["not breathing", "cardiac arrest"])], tmp_path
    )
    result = reg.find_by_chief_complaint("patient is not breathing")
    assert result is not None
    assert result.protocol_id == "cardiac"


def test_find_by_chief_complaint_no_match(tmp_path: Path):
    reg = _make_registry_with_protocols(
        [_protocol_fixture("cardiac", ["cardiac arrest"])], tmp_path
    )
    result = reg.find_by_chief_complaint("patient has a headache")
    assert result is None


def test_find_by_chief_complaint_word_boundary_prevents_substring_false_positive(tmp_path: Path):
    """
    'choking' must not match 'not choking, just coughing'... actually it
    should — 'choking' appears as a whole word. But 'chok' must not match
    'choking'. This confirms the \\b boundary works on both sides.
    """
    reg = _make_registry_with_protocols(
        [_protocol_fixture("choke", ["chok"])], tmp_path  # 'chok' is not a word
    )
    result = reg.find_by_chief_complaint("patient choking on food")
    # 'chok' does not appear as a whole word (\b boundary prevents substring match)
    assert result is None


def test_find_by_chief_complaint_longest_trigger_wins(tmp_path: Path):
    """
    'not breathing' (12 chars) should beat 'breathing' (9 chars)
    when both match the same complaint.
    """
    reg = _make_registry_with_protocols(
        [
            _protocol_fixture("respiratory", ["breathing"]),
            _protocol_fixture("cardiac", ["not breathing", "cardiac arrest"]),
        ],
        tmp_path,
    )
    result = reg.find_by_chief_complaint("patient is not breathing")
    assert result is not None
    assert result.protocol_id == "cardiac"  # 'not breathing' is longer than 'breathing'


# ── Protocol reachability validation ──────────────────────────────────────

def test_unreachable_question_rejected(tmp_path: Path):
    """
    A question that no branch_map points to (and is not the entry question)
    is dead code — the author wrote guidance nobody will ever see.
    """
    raw = dict(GOVERNANCE_COMPLETE)
    raw["questions"] = {
        "q1": {
            "text": "Q1?",
            "answer_type": "yes_no",
            "branch_map": {"yes": "outcome_a", "no": "outcome_a"},
        },
        "q2_orphan": {
            "text": "Orphan question never reachable",
            "answer_type": "yes_no",
            "branch_map": {"yes": "outcome_a", "no": "outcome_a"},
        },
    }
    raw["entry_question_id"] = "q1"
    path = tmp_path / "unreachable_q.json"
    with pytest.raises(ProtocolRejectedError, match="unreachable questions"):
        _parse_protocol(raw, path)


def test_unreachable_outcome_rejected(tmp_path: Path):
    """
    A terminal outcome that no branch_map points to is dead code — a
    priority code that can never fire.
    """
    raw = dict(GOVERNANCE_COMPLETE)
    raw["questions"] = {
        "q1": {
            "text": "Q1?",
            "answer_type": "yes_no",
            "branch_map": {"yes": "outcome_a", "no": "outcome_a"},
        },
    }
    raw["terminal_outcomes"] = {
        "outcome_a": {
            "priority_code": "P3",
            "recommended_unit_type": "BLS_AMBULANCE",
            "pre_arrival_instructions": [],
        },
        "outcome_dead": {
            "priority_code": "P1",
            "recommended_unit_type": "ALS_AMBULANCE",
            "pre_arrival_instructions": [],
        },
    }
    raw["entry_question_id"] = "q1"
    path = tmp_path / "unreachable_outcome.json"
    with pytest.raises(ProtocolRejectedError, match="unreachable terminal outcomes"):
        _parse_protocol(raw, path)


def test_valid_fixture_passes_reachability(tmp_path: Path):
    """
    The GOVERNANCE_COMPLETE fixture: entry q1 -> outcome_a. Both are
    reachable. Reachability check must pass.
    """
    path = tmp_path / "ok.json"
    path.write_text(json.dumps(GOVERNANCE_COMPLETE))
    protocol = _parse_protocol(GOVERNANCE_COMPLETE, path)
    assert protocol.entry_question_id == "q1"
    assert "outcome_a" in protocol.terminal_outcomes


def test_reachable_chain_of_questions(tmp_path: Path):
    """
    q1 -> q2 -> outcome_a. All questions are reachable via the chain.
    Reachability check must pass.
    """
    raw = dict(GOVERNANCE_COMPLETE)
    raw["questions"] = {
        "q1": {
            "text": "Q1?",
            "answer_type": "yes_no",
            "branch_map": {"yes": "q2", "no": "q2"},
        },
        "q2": {
            "text": "Q2?",
            "answer_type": "yes_no",
            "branch_map": {"yes": "outcome_a", "no": "outcome_a"},
        },
    }
    raw["entry_question_id"] = "q1"
    path = tmp_path / "chain.json"
    protocol = _parse_protocol(raw, path)
    assert "q1" in protocol.questions
    assert "q2" in protocol.questions


def test_reachability_governance_rejects_before_reachability_checked(tmp_path: Path):
    """
    The three shipped dispatch protocol files all have PLACEHOLDER
    governance text, so they are rejected at the governance check before
    reachability is ever evaluated. This confirms the check order is
    correct: governance first, then reachability.
    """
    from app.protocols.registry import ProtocolRegistry
    registry = ProtocolRegistry()
    registry.load_all()
    for rejection in registry.list_rejected():
        assert "governance fields incomplete" in rejection["reason"]


def test_reachability_multiple_outcomes_one_unreachable(tmp_path: Path):
    """
    q1 -> outcome_a. outcome_b exists but is unreachable.
    Only outcome_b should be listed as unreachable.
    """
    raw = dict(GOVERNANCE_COMPLETE)
    raw["questions"] = {
        "q1": {
            "text": "Q1?",
            "answer_type": "yes_no",
            "branch_map": {"yes": "outcome_a", "no": "outcome_a"},
        },
    }
    raw["terminal_outcomes"] = {
        "outcome_a": {
            "priority_code": "P3",
            "recommended_unit_type": "BLS_AMBULANCE",
            "pre_arrival_instructions": [],
        },
        "outcome_b": {
            "priority_code": "P1",
            "recommended_unit_type": "ALS_AMBULANCE",
            "pre_arrival_instructions": [],
        },
    }
    raw["entry_question_id"] = "q1"
    path = tmp_path / "partial_unreachable.json"
    with pytest.raises(ProtocolRejectedError, match="outcome_b"):
        _parse_protocol(raw, path)


def test_reachability_error_lists_all_unreachable(tmp_path: Path):
    """
    Multiple unreachable items are all listed in the rejection error.
    """
    raw = dict(GOVERNANCE_COMPLETE)
    raw["questions"] = {
        "q1": {
            "text": "Q1?",
            "answer_type": "yes_no",
            "branch_map": {"yes": "outcome_a", "no": "outcome_a"},
        },
        "q_orphan1": {
            "text": "Orphan 1",
            "answer_type": "yes_no",
            "branch_map": {},
        },
        "q_orphan2": {
            "text": "Orphan 2",
            "answer_type": "yes_no",
            "branch_map": {},
        },
    }
    raw["terminal_outcomes"] = {
        "outcome_a": {
            "priority_code": "P3",
            "recommended_unit_type": "BLS_AMBULANCE",
            "pre_arrival_instructions": [],
        },
        "outcome_orphan": {
            "priority_code": "P1",
            "recommended_unit_type": "ALS_AMBULANCE",
            "pre_arrival_instructions": [],
        },
    }
    raw["entry_question_id"] = "q1"
    path = tmp_path / "multi_unreachable.json"
    with pytest.raises(ProtocolRejectedError, match="unreachable") as exc_info:
        _parse_protocol(raw, path)
    msg = str(exc_info.value)
    assert "q_orphan1" in msg
    assert "q_orphan2" in msg
    assert "outcome_orphan" in msg
