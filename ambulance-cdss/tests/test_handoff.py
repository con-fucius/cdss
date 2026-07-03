"""
tests/test_handoff.py

Phase 5 — unit tests for handoff summary rendering.

Tests _render_text, _max_by, _min_by directly against HandoffSummary
instances. No database, no async, no fixtures — all test data is
constructed inline. This matches the discipline of every other phase's
test file in this codebase.
"""

from __future__ import annotations

import pytest
from app.handoff import HandoffSummary, _max_by, _min_by, _render_text


def _minimal_summary(**kwargs) -> HandoffSummary:
    defaults = dict(
        incident_id="test-001",
        status="on_scene",
        chief_complaint="patient unresponsive",
        priority_code="P1_AIRWAY_COMPLETE",
        recommended_unit_type="ALS_AMBULANCE",
        assigned_unit_id="UNIT-7",
        dispatch_protocol_id="cardiac_arrest_unresponsive_v1",
        dispatch_protocol_version="1.0.0",
        field_protocol_id="field_cardiac_arrest_v1",
        field_protocol_version="1.0.0",
        routed_facility_id="FAC-001",
        routed_facility_name="Kenyatta National Hospital",
    )
    defaults.update(kwargs)
    return HandoffSummary(**defaults)


class TestMaxBy:
    def test_returns_row_with_highest_value(self):
        rows = [{"news2_score": 3}, {"news2_score": 7}, {"news2_score": 1}]
        result = _max_by(rows, "news2_score")
        assert result["news2_score"] == 7

    def test_returns_none_for_empty_list(self):
        assert _max_by([], "news2_score") is None

    def test_skips_rows_where_key_is_none(self):
        rows = [{"news2_score": None}, {"news2_score": 4}]
        result = _max_by(rows, "news2_score")
        assert result["news2_score"] == 4

    def test_returns_none_when_all_values_are_none(self):
        rows = [{"news2_score": None}, {"news2_score": None}]
        assert _max_by(rows, "news2_score") is None


class TestMinBy:
    def test_returns_row_with_lowest_value(self):
        rows = [{"gcs_total": 15}, {"gcs_total": 6}, {"gcs_total": 12}]
        result = _min_by(rows, "gcs_total")
        assert result["gcs_total"] == 6

    def test_returns_none_for_empty_list(self):
        assert _min_by([], "gcs_total") is None

    def test_skips_rows_where_key_is_none(self):
        rows = [{"gcs_total": None}, {"gcs_total": 10}]
        result = _min_by(rows, "gcs_total")
        assert result["gcs_total"] == 10


class TestRenderText:
    def test_empty_incident_renders_without_error(self):
        s = _minimal_summary()
        text = _render_text(s)
        assert "AMBULANCE HANDOFF SUMMARY" in text
        assert s.incident_id in text
        assert s.chief_complaint in text

    def test_dispatch_qa_renders_verbatim(self):
        s = _minimal_summary(
            dispatch_qa=[
                {"question_text": "Is the patient breathing?", "answer": "no", "is_backtrack": False}
            ]
        )
        text = _render_text(s)
        assert "Is the patient breathing?" in text
        assert "A: no" in text

    def test_backtrack_marker_present(self):
        s = _minimal_summary(
            dispatch_qa=[
                {"question_text": "Is the patient breathing?", "answer": "yes", "is_backtrack": True}
            ]
        )
        text = _render_text(s)
        assert "[BACKTRACK]" in text

    def test_no_backtrack_marker_when_false(self):
        s = _minimal_summary(
            dispatch_qa=[
                {"question_text": "Any bystanders?", "answer": "yes", "is_backtrack": False}
            ]
        )
        text = _render_text(s)
        assert "[BACKTRACK]" not in text

    def test_guidance_section_absent_when_no_lookups(self):
        s = _minimal_summary()
        text = _render_text(s)
        assert "SUPPLEMENTARY GUIDANCE" not in text

    def test_guidance_section_present_with_informational_only_label(self):
        s = _minimal_summary(
            guidance_lookups_used=[
                {
                    "question_id": "q4_cpr_capability",
                    "result_summary": "Compression-only CPR is acceptable.",
                }
            ]
        )
        text = _render_text(s)
        assert "SUPPLEMENTARY GUIDANCE" in text
        assert "informational only" in text.lower()
        assert "Compression-only CPR is acceptable." in text

    def test_vitals_line_partial_fields(self):
        s = _minimal_summary(
            vitals_timeline=[
                {
                    "recorded_at": "2026-06-01T10:00:00+03:00",
                    "recorded_by": "P1",
                    "respiratory_rate": 24,
                    "spo2": None,
                    "bp_systolic": None,
                    "bp_diastolic": None,
                    "heart_rate": None,
                    "temperature": None,
                    "consciousness": "V",
                    "news2_score": 3,
                    "news2_risk_level": "low-medium",
                    "gcs_total": None,
                }
            ]
        )
        text = _render_text(s)
        assert "RR 24/min" in text
        assert "AVPU V" in text
        assert "NEWS2 3" in text
        # spo2/BP/HR not mentioned since None
        assert "SpO2" not in text

    def test_highest_news2_and_lowest_gcs_lines(self):
        highest = {
            "news2_score": 7,
            "news2_risk_level": "high",
            "recorded_at": "2026-06-01T10:05:00+03:00",
        }
        lowest = {
            "gcs_total": 8,
            "recorded_at": "2026-06-01T10:10:00+03:00",
        }
        s = _minimal_summary(highest_news2=highest, lowest_gcs=lowest)
        text = _render_text(s)
        assert "Highest NEWS2" in text
        assert "7" in text
        assert "Lowest GCS" in text
        assert "8" in text

    def test_medication_administered_vs_not_split(self):
        meds = [
            {
                "given_at": "2026-06-01T10:02:00+03:00",
                "drug_name": "Adrenaline",
                "dose": "1mg",
                "route": "IV",
                "given_by": "P1",
                "administered": True,
            },
            {
                "given_at": "2026-06-01T10:03:00+03:00",
                "drug_name": "Atropine",
                "dose": "0.5mg",
                "route": "IV",
                "given_by": "P1",
                "administered": False,
            },
        ]
        s = _minimal_summary(medications_given=meds)
        text = _render_text(s)
        assert "Administered:" in text
        assert "Adrenaline" in text
        assert "Carried / considered, NOT administered:" in text
        assert "Atropine" in text

    def test_field_actions_note_fallback(self):
        s = _minimal_summary(
            field_actions=[
                {
                    "timestamp": "2026-06-01T10:00:00+03:00",
                    "action_type": "assessment",
                    "recorded_by": "P1",
                    "data": {"note": "Patient found on floor."},
                }
            ]
        )
        text = _render_text(s)
        assert "Patient found on floor." in text

    def test_field_actions_step_title_fallback(self):
        s = _minimal_summary(
            field_actions=[
                {
                    "timestamp": "2026-06-01T10:00:00+03:00",
                    "action_type": "intervention",
                    "recorded_by": "P1",
                    "data": {"step_title": "Start high-quality CPR"},
                }
            ]
        )
        text = _render_text(s)
        assert "Start high-quality CPR" in text

    def test_no_priority_code_renders_not_recorded(self):
        s = _minimal_summary(priority_code=None)
        text = _render_text(s)
        assert "not recorded" in text
