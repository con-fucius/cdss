"""tests/test_scoring.py.

Per the established pattern (matches the chronic-disease CDSS's scoring
test discipline): normal case, boundary score, missing required input
(raises), for each scorer. No LLM imports, no network calls, no async —
asserted directly by inspecting the module.
"""

from __future__ import annotations

import inspect

import pytest
from app.scoring import scorers
from app.scoring.scorers import (
    ScoringError,
    compute_gcs_total,
    compute_news2,
    interpret_gcs,
)


class TestNEWS2:
    def test_all_normal_inputs_score_zero(self):
        result = compute_news2(
            {
                "respiratory_rate": 16,
                "spo2": 98,
                "bp_systolic": 120,
                "heart_rate": 70,
                "consciousness": "A",
                "temperature": 37.0,
            }
        )
        assert result.score == 0
        assert result.risk_level == "low"
        assert result.escalation_required is False

    def test_high_risk_boundary_case(self):
        """RR=25 (3), SpO2=90 (3), BP=90 (3), HR=120 (2) -> total 11, high risk."""
        result = compute_news2(
            {
                "respiratory_rate": 25,
                "spo2": 90,
                "bp_systolic": 90,
                "heart_rate": 120,
                "consciousness": "A",
                "temperature": 37.0,
            }
        )
        assert result.score >= 7
        assert result.risk_level == "high"
        assert result.escalation_required is True

    def test_single_param_score_3_triggers_escalation_even_if_total_low(self):
        """A single 3-scoring parameter must escalate per NEWS2 spec,
        regardless of total score magnitude.
        """
        result = compute_news2(
            {
                "respiratory_rate": 16,  # 0
                "spo2": 98,  # 0
                "bp_systolic": 120,  # 0
                "heart_rate": 70,  # 0
                "consciousness": "V",  # 3 — any non-alert scores 3
                "temperature": 37.0,  # 0
            }
        )
        assert result.component_scores["consciousness"] == 3
        assert result.escalation_required is True

    def test_missing_required_field_raises_scoring_error(self):
        with pytest.raises(ScoringError) as exc_info:
            compute_news2(
                {
                    "respiratory_rate": 16,
                    "spo2": 98,
                    # bp_systolic missing
                    "heart_rate": 70,
                    "consciousness": "A",
                    "temperature": 37.0,
                }
            )
        assert "bp_systolic" in exc_info.value.missing_fields

    def test_spo2_scale_2_used_when_specified(self):
        result = compute_news2(
            {
                "respiratory_rate": 16,
                "spo2": 90,
                "spo2_scale": 2,
                "supplemental_o2": True,
                "bp_systolic": 120,
                "heart_rate": 70,
                "consciousness": "A",
                "temperature": 37.0,
            }
        )
        # Scale 2, spo2=90 is within target range 88-92 -> score 0 for spo2
        assert result.component_scores["spo2"] == 0


class TestGCS:
    def test_full_normal_gcs(self):
        total = compute_gcs_total(eye=4, verbal=5, motor=6)
        assert total == 15
        result = interpret_gcs(total)
        assert result.risk_level == "low"
        assert result.escalation_required is False

    def test_severe_gcs(self):
        total = compute_gcs_total(eye=1, verbal=1, motor=1)
        assert total == 3
        result = interpret_gcs(total)
        assert result.risk_level == "high"
        assert result.escalation_required is True

    def test_out_of_range_eye_raises(self):
        with pytest.raises(ScoringError):
            compute_gcs_total(eye=5, verbal=5, motor=6)

    def test_out_of_range_motor_raises(self):
        with pytest.raises(ScoringError):
            compute_gcs_total(eye=4, verbal=5, motor=0)


def test_no_async_no_llm_no_network_imports_in_scorers_module():
    """Hard constraint per app/scoring/scorers.py module docstring and the
    Ambulance CDSS implementation plan: ClinicalScorer-equivalent functions
    must be synchronous, pure Python, with no LLM provider imports.
    """
    source = inspect.getsource(scorers)
    assert "async def" not in source
    assert "httpx" not in source
    assert "import asyncio" not in source
