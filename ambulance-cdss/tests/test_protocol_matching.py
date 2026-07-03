"""
tests/test_protocol_matching.py

Exercises the weighted chief-complaint protocol selection with confidence
scoring in app/protocols/registry.py (ProtocolMatchResult,
match_by_chief_complaint).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.protocols.registry import ProtocolRegistry


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


def _protocol_fixture(protocol_id: str, triggers: list) -> dict:
    base = dict(GOVERNANCE_COMPLETE)
    base["protocol_id"] = protocol_id
    base["chief_complaint_trigger"] = triggers
    return base


def _make_registry_with_protocols(protocols: list, tmp_path: Path) -> ProtocolRegistry:
    for proto in protocols:
        path = tmp_path / f"{proto['protocol_id']}.json"
        path.write_text(json.dumps(proto))
    reg = ProtocolRegistry(protocols_dir=tmp_path)
    reg.load_all()
    return reg


# ── Confidence scoring ────────────────────────────────────────────────────

def test_all_triggers_match_confidence_1(tmp_path: Path):
    """All triggers of a single-protocol registry match → confidence 1.0."""
    reg = _make_registry_with_protocols(
        [_protocol_fixture("cardiac", ["cardiac arrest"])], tmp_path
    )
    result = reg.match_by_chief_complaint("cardiac arrest")
    assert result is not None
    assert result.protocol.protocol_id == "cardiac"
    assert result.confidence == 1.0
    assert result.matched_triggers == ["cardiac arrest"]
    assert result.alternatives == []


def test_one_of_two_triggers_match_confidence_05(tmp_path: Path):
    """Protocol with two triggers, only one matches → confidence 0.5."""
    reg = _make_registry_with_protocols(
        [_protocol_fixture("cardiac", ["cardiac arrest", "not breathing"])],
        tmp_path,
    )
    result = reg.match_by_chief_complaint("cardiac arrest")
    assert result is not None
    assert result.confidence == 0.5
    assert result.matched_triggers == ["cardiac arrest"]
    assert len(result.alternatives) == 0


def test_two_of_three_triggers_match_confidence_two_thirds(tmp_path: Path):
    """Protocol with three triggers, two match → confidence 2/3."""
    reg = _make_registry_with_protocols(
        [_protocol_fixture("multi", ["cardiac arrest", "not breathing", "unresponsive"])],
        tmp_path,
    )
    result = reg.match_by_chief_complaint("cardiac arrest and not breathing")
    assert result is not None
    assert result.confidence == pytest.approx(2.0 / 3.0)
    assert len(result.matched_triggers) == 2
    assert "cardiac arrest" in result.matched_triggers
    assert "not breathing" in result.matched_triggers


# ── Two protocols both match ──────────────────────────────────────────────

def test_two_protocols_match_loser_in_alternatives(tmp_path: Path):
    """
    Two protocols match the same complaint. The one with the longer
    matching trigger wins; the other appears in alternatives.
    """
    reg = _make_registry_with_protocols(
        [
            _protocol_fixture("respiratory", ["breathing"]),
            _protocol_fixture("cardiac", ["not breathing", "cardiac arrest"]),
        ],
        tmp_path,
    )
    result = reg.match_by_chief_complaint("patient is not breathing")
    assert result is not None
    # 'not breathing' (12 chars) beats 'breathing' (9 chars)
    assert result.protocol.protocol_id == "cardiac"
    assert len(result.alternatives) == 1
    assert result.alternatives[0].protocol.protocol_id == "respiratory"


def test_two_protocols_same_longest_trigger_alphabetical_winner(tmp_path: Path):
    """
    Two protocols with identical longest trigger length: the one that
    sorts alphabetically first wins.
    """
    reg = _make_registry_with_protocols(
        [
            _protocol_fixture("z_protocol", ["chest pain"]),
            _protocol_fixture("a_protocol", ["chest pain"]),
        ],
        tmp_path,
    )
    result = reg.match_by_chief_complaint("chest pain")
    assert result is not None
    assert result.protocol.protocol_id == "a_protocol"
    assert len(result.alternatives) == 1
    assert result.alternatives[0].protocol.protocol_id == "z_protocol"


# ── No match ──────────────────────────────────────────────────────────────

def test_no_match_returns_none(tmp_path: Path):
    """No protocol has any trigger matching the complaint → None."""
    reg = _make_registry_with_protocols(
        [_protocol_fixture("cardiac", ["cardiac arrest"])], tmp_path
    )
    result = reg.match_by_chief_complaint("patient has a headache")
    assert result is None


# ── requires_manual_verification ──────────────────────────────────────────

def test_requires_manual_verification_true_when_confidence_below_1(tmp_path: Path):
    """
    Confidence < 1.0 means not all triggers fired → ambiguous enough
    to require manual verification.
    """
    reg = _make_registry_with_protocols(
        [_protocol_fixture("cardiac", ["cardiac arrest", "not breathing"])],
        tmp_path,
    )
    result = reg.match_by_chief_complaint("cardiac arrest")
    assert result is not None
    assert result.confidence < 1.0
    # The caller (create_incident endpoint) sets requires_manual_verification
    # based on this. We verify the confidence here.
    requires_manual_verification = result.confidence < 1.0 or len(result.alternatives) > 0
    assert requires_manual_verification is True


def test_requires_manual_verification_true_when_alternatives_nonempty(tmp_path: Path):
    """
    Even if confidence is 1.0, having alternatives means the match was
    ambiguous between two protocols → requires manual verification.
    """
    # Both protocols have a single trigger that matches
    reg = _make_registry_with_protocols(
        [
            _protocol_fixture("a_proto", ["chest pain"]),
            _protocol_fixture("b_proto", ["chest pain"]),
        ],
        tmp_path,
    )
    result = reg.match_by_chief_complaint("chest pain")
    assert result is not None
    assert result.confidence == 1.0
    assert len(result.alternatives) == 1
    requires_manual_verification = result.confidence < 1.0 or len(result.alternatives) > 0
    assert requires_manual_verification is True


def test_no_alternatives_confidence_1_no_manual_verification(tmp_path: Path):
    """
    Single protocol, all triggers match, no alternatives →
    requires_manual_verification should be False.
    """
    reg = _make_registry_with_protocols(
        [_protocol_fixture("cardiac", ["cardiac arrest"])], tmp_path
    )
    result = reg.match_by_chief_complaint("cardiac arrest")
    assert result is not None
    assert result.confidence == 1.0
    assert len(result.alternatives) == 0
    requires_manual_verification = result.confidence < 1.0 or len(result.alternatives) > 0
    assert requires_manual_verification is False


# ── Alternative sorting ───────────────────────────────────────────────────

def test_alternatives_sorted_by_confidence_desc(tmp_path: Path):
    """
    When multiple alternatives exist, they are sorted by confidence
    descending (most confident alternative first).
    """
    reg = _make_registry_with_protocols(
        [
            _protocol_fixture("winner", ["not breathing normally", "cardiac arrest"]),
            _protocol_fixture("alt_high", ["cardiac arrest", "chest pain", "shortness of breath"]),
            _protocol_fixture("alt_low", ["breathing"]),
        ],
        tmp_path,
    )
    result = reg.match_by_chief_complaint("patient is not breathing normally with cardiac arrest")
    assert result is not None
    assert result.protocol.protocol_id == "winner"
    assert len(result.alternatives) == 2
    # winner matched 2 of 2 triggers (confidence 1.0), longest trigger "not breathing normally" (22 chars)
    # alt_high matched 1 of 3 triggers (confidence 1/3), longest trigger "cardiac arrest" (13 chars)
    # alt_low matched 1 of 1 triggers (confidence 1.0), longest trigger "breathing" (9 chars)
    # Winner is "winner" because longest trigger is 22 > 13 > 9
    # Alternatives sorted by confidence desc: alt_low (1.0) then alt_high (1/3)
    assert result.confidence == 1.0
    assert result.alternatives[0].protocol.protocol_id == "alt_low"
    assert result.alternatives[0].confidence == 1.0
    assert result.alternatives[1].protocol.protocol_id == "alt_high"
    assert result.alternatives[1].confidence == pytest.approx(1.0 / 3.0)


# ── Confidence bounds ─────────────────────────────────────────────────────

def test_confidence_always_between_0_and_1(tmp_path: Path):
    """Confidence must always be in [0.0, 1.0]."""
    reg = _make_registry_with_protocols(
        [_protocol_fixture("cardiac", ["cardiac arrest", "not breathing", "unresponsive"])],
        tmp_path,
    )
    result = reg.match_by_chief_complaint("cardiac arrest")
    assert result is not None
    assert 0.0 <= result.confidence <= 1.0


# ── find_by_chief_complaint still works ───────────────────────────────────

def test_find_by_chief_complaint_still_works(tmp_path: Path):
    """
    The original find_by_chief_complaint method must still return
    the same DispatchProtocol object as before.
    """
    reg = _make_registry_with_protocols(
        [_protocol_fixture("cardiac", ["cardiac arrest"])], tmp_path
    )
    result = reg.find_by_chief_complaint("cardiac arrest")
    assert result is not None
    assert result.protocol_id == "cardiac"
