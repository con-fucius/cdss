"""
tests/test_audit_export.py

Improvement 5 — tests for medico-legal audit export.

Tests confirm:
- Every row from dispatch_log appears in the export text, identified by its id.
- A backtrack row is marked [BACKTRACK].
- The administered: false medication flag appears in the export.
- The SHA256 hash in the footer changes when any field in the source data changes.
- The function is synchronous (no async def) and takes a plain dict — it must
  not make DB calls itself.
"""

from __future__ import annotations

import inspect

from app.handoff import render_audit_text


def _minimal_full(**kwargs) -> dict:
    """Build a minimal get_incident_full()-shaped dict for testing."""
    defaults = {
        "incident": {
            "incident_id": "test-export-001",
            "created_at": "2026-06-01T10:00:00+00:00",
            "status": "on_scene",
            "chief_complaint": "cardiac arrest",
            "dispatch_protocol_id": "cardiac_arrest_v1",
            "dispatch_protocol_version": "1.0.0",
            "dispatch_protocol_snapshot": {
                "approved_by": "Dr. Smith",
                "approved_date": "2026-01-01",
            },
        },
        "dispatch_log": [],
        "field_log": [],
        "vitals_history": [],
        "medications_given": [],
        "guidance_lookups": [],
    }
    defaults.update(kwargs)
    return defaults


class TestAuditExportContent:
    def test_every_dispatch_log_row_appears_with_id(self):
        """Every row from dispatch_log appears in the export with its id."""
        full = _minimal_full(
            dispatch_log=[
                {
                    "id": "disp-001",
                    "question_id": "q1",
                    "question_text": "Is the patient breathing?",
                    "answer": "no",
                    "protocol_version": "1.0.0",
                    "is_backtrack": False,
                    "timestamp": "2026-06-01T10:00:00+00:00",
                },
                {
                    "id": "disp-002",
                    "question_id": "q2",
                    "question_text": "Any pulse?",
                    "answer": "no",
                    "protocol_version": "1.0.0",
                    "is_backtrack": False,
                    "timestamp": "2026-06-01T10:01:00+00:00",
                },
            ]
        )
        text = render_audit_text(full)
        assert "disp-001" in text
        assert "disp-002" in text
        assert "Is the patient breathing?" in text
        assert "Any pulse?" in text

    def test_backtrack_row_is_marked(self):
        """A backtrack row is marked with [BACKTRACK]."""
        full = _minimal_full(
            dispatch_log=[
                {
                    "id": "disp-bt",
                    "question_id": "q1",
                    "question_text": "Re-check breathing?",
                    "answer": "yes",
                    "protocol_version": "1.0.0",
                    "is_backtrack": True,
                    "timestamp": "2026-06-01T10:00:00+00:00",
                },
            ]
        )
        text = render_audit_text(full)
        assert "[BACKTRACK]" in text

    def test_administered_false_medication_appears(self):
        """The administered: false medication flag appears in the export."""
        full = _minimal_full(
            medications_given=[
                {
                    "id": "med-001",
                    "drug_name": "Atropine",
                    "dose": "0.5mg",
                    "route": "IV",
                    "administered": False,
                    "given_at": "2026-06-01T10:02:00+00:00",
                    "given_by": "P1",
                },
            ]
        )
        text = render_audit_text(full)
        assert "NOT ADMINISTERED" in text
        assert "Atropine" in text
        assert "med-001" in text

    def test_administered_true_medication_appears(self):
        """The administered: true medication flag appears correctly."""
        full = _minimal_full(
            medications_given=[
                {
                    "id": "med-002",
                    "drug_name": "Adrenaline",
                    "dose": "1mg",
                    "route": "IV",
                    "administered": True,
                    "given_at": "2026-06-01T10:03:00+00:00",
                    "given_by": "P1",
                },
            ]
        )
        text = render_audit_text(full)
        assert "ADMINISTERED" in text
        assert "NOT ADMINISTERED" not in text

    def test_vitals_row_appears_with_all_fields(self):
        """A vitals row appears with its id and component values."""
        full = _minimal_full(
            vitals_history=[
                {
                    "id": "vitals-001",
                    "recorded_at": "2026-06-01T10:00:00+00:00",
                    "recorded_by": "P1",
                    "respiratory_rate": 24,
                    "spo2": 92,
                    "bp_systolic": 100,
                    "bp_diastolic": 60,
                    "heart_rate": 110,
                    "consciousness": "V",
                    "temperature": 36.5,
                    "gcs_eye": 3,
                    "gcs_verbal": 4,
                    "gcs_motor": 5,
                    "news2_score": 6,
                    "news2_risk_level": "medium",
                    "gcs_total": 12,
                },
            ]
        )
        text = render_audit_text(full)
        assert "vitals-001" in text
        assert "RR=24" in text
        assert "SpO2=92%" in text
        assert "NEWS2=6(medium)" in text
        assert "GCS_total=12" in text

    def test_field_log_row_appears_with_data(self):
        """A field log row appears with step_id, action_type, and data."""
        full = _minimal_full(
            field_log=[
                {
                    "id": "field-001",
                    "step_id": "f1_scene_safety",
                    "action_type": "assessment",
                    "data": {"note": "Scene is safe"},
                    "recorded_by": "P1",
                    "timestamp": "2026-06-01T10:00:00+00:00",
                },
            ]
        )
        text = render_audit_text(full)
        assert "field-001" in text
        assert "f1_scene_safety" in text
        assert "assessment" in text
        assert "Scene is safe" in text

    def test_guidance_lookup_appears(self):
        """A guidance lookup row appears in the export."""
        full = _minimal_full(
            guidance_lookups=[
                {
                    "id": "gl-001",
                    "question_id": "q4",
                    "query_text": "CPR guidance?",
                    "result_summary": "Compression-only CPR is acceptable.",
                    "dispatcher_id": "disp-1",
                    "timestamp": "2026-06-01T10:05:00+00:00",
                },
            ]
        )
        text = render_audit_text(full)
        assert "gl-001" in text
        assert "Compression-only CPR is acceptable." in text

    def test_header_block_contains_incident_fields(self):
        """The header block contains incident_id, chief_complaint, etc."""
        full = _minimal_full()
        text = render_audit_text(full)
        assert "INCIDENT AUDIT EXPORT" in text
        assert "test-export-001" in text
        assert "cardiac arrest" in text
        assert "cardiac_arrest_v1" in text

    def test_footer_contains_timestamp(self):
        """The footer contains an export generation timestamp."""
        full = _minimal_full()
        text = render_audit_text(full)
        assert "EXPORT GENERATED AT:" in text

    def test_empty_incident_renders_without_error(self):
        """An incident with no events renders without error."""
        full = _minimal_full()
        text = render_audit_text(full)
        assert "no dispatch answers recorded" in text
        assert "none recorded" in text.lower()


class TestAuditExportIntegrityHash:
    def test_sha256_hash_in_footer(self):
        """The footer contains a SHA256 hash."""
        full = _minimal_full()
        text = render_audit_text(full)
        assert "INCIDENT DATA HASH (SHA256):" in text

    def test_hash_is_64_hex_chars(self):
        """The SHA256 hash is a 64-character hex string."""
        full = _minimal_full()
        text = render_audit_text(full)
        for line in text.split("\n"):
            if "INCIDENT DATA HASH (SHA256):" in line:
                hash_value = line.split(":")[-1].strip()
                assert len(hash_value) == 64
                assert all(c in "0123456789abcdef" for c in hash_value)
                break

    def test_hash_changes_when_data_changes(self):
        """The SHA256 hash changes when any field in the source data changes."""
        full_a = _minimal_full()
        full_b = _minimal_full()
        full_b["incident"]["status"] = "closed"

        text_a = render_audit_text(full_a)
        text_b = render_audit_text(full_b)

        # Extract hashes
        hash_a = None
        hash_b = None
        for line in text_a.split("\n"):
            if "INCIDENT DATA HASH (SHA256):" in line:
                hash_a = line.split(":")[-1].strip()
        for line in text_b.split("\n"):
            if "INCIDENT DATA HASH (SHA256):" in line:
                hash_b = line.split(":")[-1].strip()

        assert hash_a is not None
        assert hash_b is not None
        assert hash_a != hash_b

    def test_hash_is_reproducible(self):
        """Calling render_audit_text twice on the same data produces the same hash."""
        full = _minimal_full()
        text_a = render_audit_text(full)
        text_b = render_audit_text(full)

        # Extract hashes (excluding the generation timestamp which differs)
        def extract_hash(text):
            for line in text.split("\n"):
                if "INCIDENT DATA HASH (SHA256):" in line:
                    return line.split(":")[-1].strip()
            return None

        # The hash of the data itself is deterministic; the generated-at
        # timestamp varies but the hash is computed from the input dict, not
        # the rendered text. So hashes must match.
        assert extract_hash(text_a) == extract_hash(text_b)


class TestAuditExportFunctionProperties:
    def test_function_is_synchronous(self):
        """The function must be synchronous (no async def) — it takes a plain dict."""
        assert not inspect.iscoroutinefunction(render_audit_text)

    def test_function_takes_a_plain_dict(self):
        """The function takes a plain dict, not a HandoffSummary or DB session."""
        sig = inspect.signature(render_audit_text)
        params = list(sig.parameters.keys())
        assert len(params) == 1
        # The parameter should be named 'full'
        assert params[0] == "full"

    def test_function_does_not_import_db_or_async(self):
        """The function must not make DB calls or use async."""
        source = inspect.getsource(render_audit_text)
        assert "async def" not in source
        assert "get_session" not in source
        assert "await" not in source
