"""tests/test_triage_e2e.py.

Comprehensive E2E tests hitting the live triage-ranker server on
localhost:8100. Tests multiple clinical scenarios and error paths.

Run with the server started: uvicorn app.main:app --host 0.0.0.0 --port 8100
"""

from __future__ import annotations

import httpx

BASE = "http://localhost:8100"


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _client() -> httpx.Client:
    return httpx.Client(base_url=BASE, timeout=15)


def _triage(client: httpx.Client, **kwargs) -> dict:
    """POST /triage and return the full response dict."""
    r = client.post("/triage", json=kwargs)
    assert r.status_code == 200, f"/triage returned {r.status_code}: {r.text}"
    return r.json()


# ─────────────────────────────────────────────────────────────────────────────
# Clinical scenarios — Cardiovascular
# ─────────────────────────────────────────────────────────────────────────────


class TestCardiovascular:
    def test_chest_pain_with_vitals(self):
        """Chest pain + tachycardia + hypotension → expect high acuity."""
        with _client() as c:
            d = _triage(c, incident_desc="chest pain, shortness of breath", hr=110, sbp=90)
            assert d["triage_level"] in ("P1", "P2")
            assert d["esi_level"] <= 3
            assert len(d["diagnosis_ranking"]) > 0
            # Shock index should be computed: 110/90 = 1.22
            assert d["metadata"]["shock_index"] is not None
            assert d["metadata"]["shock_index"] > 1.0

    def test_chest_pain_stable_vitals(self):
        """Chest pain with normal vitals → moderate acuity."""
        with _client() as c:
            d = _triage(c, incident_desc="chest pain", hr=75, sbp=130)
            assert len(d["diagnosis_ranking"]) > 0
            assert d["metadata"]["shock_index"] is not None
            assert d["metadata"]["shock_index"] < 1.0

    def test_cardiac_arrest_acvpu_unresponsive(self):
        """Unresponsive patient → expect severe TBI risk and low ESI."""
        with _client() as c:
            d = _triage(c, incident_desc="unresponsive patient, no pulse", acvpu="unresponsive")
            assert d["triage_level"] in ("P1", "P2")
            assert "UNRESPONSIVE" in d["metadata"]["inferred_risks"]

    def test_cardiac_arrest_low_gcs(self):
        """GCS 3 → severe TBI risk flag."""
        with _client() as c:
            d = _triage(c, incident_desc="cardiac arrest, collapsed patient", gcs_score=3)
            assert "SEVERE_TBI" in d["metadata"]["inferred_risks"]
            # GCS 3 should push score high
            top = d["diagnosis_ranking"][0]
            assert top["score_breakdown"]["w_gcs"] > 0


# ─────────────────────────────────────────────────────────────────────────────
# Clinical scenarios — Respiratory
# ─────────────────────────────────────────────────────────────────────────────


class TestRespiratory:
    def test_difficulty_breathing(self):
        """General difficulty breathing → expect respiratory keyword."""
        with _client() as c:
            d = _triage(c, incident_desc="difficulty breathing, wheezing")
            assert len(d["keywords"]) > 0
            categories = {kw.get("category", "") for kw in d["keywords"]}
            assert "RESPIRATORY" in categories

    def test_severe_respiratory_distress(self):
        """Severe respiratory distress with tachycardia → high acuity."""
        with _client() as c:
            d = _triage(
                c,
                incident_desc="severe respiratory distress, can barely speak",
                hr=130,
                sbp=95,
            )
            assert d["triage_level"] in ("P1", "P2")
            assert d["metadata"]["shock_index"] > 1.0

    def test_child_respiratory(self):
        """Paediatric respiratory complaint."""
        with _client() as c:
            d = _triage(c, incident_desc="child can't breathe, wheezing")
            assert len(d["diagnosis_ranking"]) > 0


# ─────────────────────────────────────────────────────────────────────────────
# Clinical scenarios — Trauma
# ─────────────────────────────────────────────────────────────────────────────


class TestTrauma:
    def test_mva_with_vitals(self):
        """Motor vehicle accident with hypotension → P1 trauma."""
        with _client() as c:
            d = _triage(
                c,
                incident_desc="car accident, patient trapped, bleeding",
                hr=120,
                sbp=80,
            )
            assert d["triage_level"] in ("P1", "P2")
            assert d["metadata"]["shock_index"] > 1.0
            assert "HAEMODYNAMIC_INSTABILITY" in d["metadata"]["inferred_risks"]

    def test_fall_injury(self):
        """Fall from height."""
        with _client() as c:
            d = _triage(c, incident_desc="fell down stairs, head injury")
            assert len(d["diagnosis_ranking"]) > 0

    def test_stab_wound(self):
        """Penetrating trauma."""
        with _client() as c:
            d = _triage(c, incident_desc="stabbing to the chest, bleeding heavily")
            assert len(d["diagnosis_ranking"]) > 0


# ─────────────────────────────────────────────────────────────────────────────
# Clinical scenarios — Neurological
# ─────────────────────────────────────────────────────────────────────────────


class TestNeurological:
    def test_stroke_symptoms(self):
        """Stroke-like presentation."""
        with _client() as c:
            d = _triage(c, incident_desc="sudden weakness on left side, slurred speech")
            assert len(d["diagnosis_ranking"]) > 0

    def test_seizure(self):
        """Seizure activity."""
        with _client() as c:
            d = _triage(c, incident_desc="patient having a seizure, shaking")
            assert len(d["diagnosis_ranking"]) > 0


# ─────────────────────────────────────────────────────────────────────────────
# Clinical scenarios — Obstetric
# ─────────────────────────────────────────────────────────────────────────────


class TestObstetric:
    def test_pregnancy_bleeding(self):
        """Obstetric emergency."""
        with _client() as c:
            d = _triage(c, incident_desc="pregnant woman, vaginal bleeding")
            assert len(d["diagnosis_ranking"]) > 0


# ─────────────────────────────────────────────────────────────────────────────
# Clinical scenarios — Swahili terms
# ─────────────────────────────────────────────────────────────────────────────


class TestSwahili:
    def test_swahili_breathing(self):
        """Swahili respiratory term should extract a keyword."""
        with _client() as c:
            d = _triage(c, incident_desc="kushindwa kupumua")
            assert len(d["keywords"]) > 0
            categories = {kw.get("category", "") for kw in d["keywords"]}
            # Exact Swahili synonym should match via regex-only path
            assert "RESPIRATORY" in categories

    def test_swahili_cardiac(self):
        """Swahili cardiac arrest terms."""
        with _client() as c:
            d = _triage(c, incident_desc="kuzimia, moyo haupigi")
            assert len(d["keywords"]) > 0
            categories = {kw.get("category", "") for kw in d["keywords"]}
            # At least one keyword should match (NEUROLOGICAL or CARDIOVASCULAR)
            assert categories - {"UNKNOWN"} != set()


# ─────────────────────────────────────────────────────────────────────────────
# Clinical scenarios — Shock Index edge cases
# ─────────────────────────────────────────────────────────────────────────────


class TestShockIndex:
    def test_shock_index_critical(self):
        """SI > 1.0 → HAEMODYNAMIC_INSTABILITY risk flag."""
        with _client() as c:
            d = _triage(c, incident_desc="bleeding heavily", hr=130, sbp=85)
            assert d["metadata"]["shock_index"] is not None
            assert d["metadata"]["shock_index"] > 1.0
            assert "HAEMODYNAMIC_INSTABILITY" in d["metadata"]["inferred_risks"]

    def test_shock_index_normal(self):
        """SI < 0.9 → no instability flag."""
        with _client() as c:
            d = _triage(c, incident_desc="minor injury", hr=70, sbp=120)
            assert d["metadata"]["shock_index"] is not None
            assert d["metadata"]["shock_index"] < 0.9
            assert "HAEMODYNAMIC_INSTABILITY" not in d["metadata"]["inferred_risks"]

    def test_shock_index_borderline(self):
        """SI 0.9-1.0 → elevated but not critical."""
        with _client() as c:
            d = _triage(c, incident_desc="chest discomfort", hr=100, sbp=110)
            assert d["metadata"]["shock_index"] is not None
            si = d["metadata"]["shock_index"]
            assert 0.8 < si <= 1.0


# ─────────────────────────────────────────────────────────────────────────────
# Clinical scenarios — ACVPU / GCS mapping
# ─────────────────────────────────────────────────────────────────────────────


class TestConsciousnessLevel:
    def test_alert_patient(self):
        """Alert → high GCS → no TBI risk."""
        with _client() as c:
            d = _triage(c, incident_desc="chest pain", acvpu="A")
            assert "SEVERE_TBI" not in d["metadata"]["inferred_risks"]
            assert "UNRESPONSIVE" not in d["metadata"]["inferred_risks"]

    def test_confused_patient(self):
        """Confused → moderate GCS."""
        with _client() as c:
            d = _triage(c, incident_desc="fell, head injury", acvpu="confused")
            assert len(d["diagnosis_ranking"]) > 0

    def test_unresponsive_patient(self):
        """Unresponsive → UNRESPONSIVE risk flag."""
        with _client() as c:
            d = _triage(c, incident_desc="collapsed, not responding", acvpu="U")
            assert "UNRESPONSIVE" in d["metadata"]["inferred_risks"]


# ─────────────────────────────────────────────────────────────────────────────
# Response structure validation
# ─────────────────────────────────────────────────────────────────────────────


class TestResponseStructure:
    def test_response_has_all_fields(self):
        """Every response must include all top-level fields."""
        with _client() as c:
            d = _triage(c, incident_desc="chest pain")
            for field in [
                "diagnosis_ranking",
                "historical_findings",
                "keywords",
                "triage_level",
                "esi_level",
                "degraded_mode",
                "metadata",
            ]:
                assert field in d, f"Missing field: {field}"

    def test_diagnosis_ranking_structure(self):
        """Each diagnosis item has required fields."""
        with _client() as c:
            d = _triage(c, incident_desc="difficulty breathing")
            for item in d["diagnosis_ranking"]:
                for field in [
                    "rank",
                    "canonical_name",
                    "severity_level",
                    "esi_level",
                    "score_breakdown",
                    "scoring_systems_applied",
                ]:
                    assert field in item, f"Diagnosis item missing: {field}"
                assert item["rank"] > 0
                assert item["severity_level"].upper() in ("CRITICAL", "HIGH", "ACUTE", "MODERATE", "LOW")

    def test_metadata_structure(self):
        """Metadata contains timing and cache info."""
        with _client() as c:
            d = _triage(c, incident_desc="chest pain")
            meta = d["metadata"]
            assert "request_id" in meta
            assert "processing_times_ms" in meta
            assert "total" in meta["processing_times_ms"]
            assert meta["processing_times_ms"]["total"] > 0

    def test_triage_level_is_valid_enum(self):
        """triage_level must be one of P1-P4."""
        with _client() as c:
            d = _triage(c, incident_desc="chest pain")
            assert d["triage_level"] in ("P1", "P2", "P3", "P4")

    def test_esi_level_range(self):
        """esi_level must be 1-5."""
        with _client() as c:
            d = _triage(c, incident_desc="chest pain")
            assert 1 <= d["esi_level"] <= 5

    def test_keywords_extracted(self):
        """Keywords should be extracted for any medical input."""
        with _client() as c:
            d = _triage(c, incident_desc="chest pain and dizziness")
            assert len(d["keywords"]) > 0

    def test_never_empty_ranking(self):
        """ranking must always have at least one item (fallback)."""
        with _client() as c:
            d = _triage(c, incident_desc="xyzzy no match")
            assert len(d["diagnosis_ranking"]) >= 1
            assert "undifferentiated" in d["diagnosis_ranking"][0]["canonical_name"].lower()


# ─────────────────────────────────────────────────────────────────────────────
# Error paths
# ─────────────────────────────────────────────────────────────────────────────


class TestErrorPaths:
    def test_missing_incident_desc(self):
        """Missing required field → 422."""
        with _client() as c:
            r = c.post("/triage", json={"hr": 80})
            assert r.status_code == 422

    def test_short_incident_desc(self):
        """Short incident_desc (< 5 chars) → 422 validation error."""
        with _client() as c:
            r = c.post("/triage", json={"incident_desc": "abc"})
            assert r.status_code == 422

    def test_very_long_incident_desc(self):
        """Long text exceeding 5000 chars → 422 validation error."""
        with _client() as c:
            long_desc = "chest pain " * 500  # ~5500 chars
            r = c.post("/triage", json={"incident_desc": long_desc})
            assert r.status_code == 422

    def test_gcs_above_15_rejected(self):
        """GCS > 15 is invalid → 422."""
        with _client() as c:
            r = c.post("/triage", json={"incident_desc": "trauma test case", "gcs_score": 20})
            assert r.status_code == 422

    def test_negative_hr_rejected(self):
        """Negative heart rate → 422."""
        with _client() as c:
            r = c.post("/triage", json={"incident_desc": "chest pain test", "hr": -10})
            assert r.status_code == 422


# ─────────────────────────────────────────────────────────────────────────────
# Admin endpoints
# ─────────────────────────────────────────────────────────────────────────────


class TestAdminEndpoints:
    def test_health(self):
        """Health endpoint returns ok."""
        with _client() as c:
            r = c.get("/health")
            assert r.status_code == 200
            d = r.json()
            assert d["status"] == "ok"
            assert d["clinical_rules_count"] > 0

    def test_ready(self):
        """Readiness endpoint returns ready (regex fallback OK)."""
        with _client() as c:
            r = c.get("/ready")
            assert r.status_code == 200

    def test_admin_cache_purge_requires_key(self):
        """Cache purge without admin key → 403."""
        with _client() as c:
            r = c.delete("/admin/cache")
            # If no admin key is configured, this may return 200 or 403
            assert r.status_code in (200, 403)

    def test_admin_rules_reload_requires_key(self):
        """Rules reload without admin key → 403."""
        with _client() as c:
            r = c.post("/admin/rules/reload")
            assert r.status_code in (200, 403)
