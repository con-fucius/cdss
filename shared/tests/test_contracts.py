"""Unit tests for shared Pydantic v2 contract schemas.

Tests validation, serialization, enum values, and edge cases for:
- TriageRequest / TriageResponse / DiagnosisRankItem / ExtractedKeyword
- FacilitySearchRequest / FacilitySearchResponse / FacilityResult
- CapturePayload / PatientInfo / IncidentInfo

No network calls, no database — pure Pydantic validation tests.
"""

from __future__ import annotations

import pytest
from contracts.facility import (
    FacilityResult,
    FacilitySearchRequest,
    FacilitySearchResponse,
)
from contracts.incident_capture import (
    CaptureMetadata,
    CapturePayload,
    IncidentInfo,
    PatientInfo,
)
from contracts.triage import (
    ClinicalCategory,
    DiagnosisRankItem,
    ExtractedKeyword,
    SeverityLevel,
    TriageLevel,
    TriageMetadata,
    TriageRequest,
    TriageResponse,
)
from pydantic import ValidationError

# ── TriageRequest ─────────────────────────────────────────────────────────


class TestTriageRequest:
    def test_valid_minimal_request(self):
        req = TriageRequest(incident_desc="patient collapsed, not breathing")
        assert req.incident_desc == "patient collapsed, not breathing"
        assert req.gcs_score is None
        assert req.acvpu is None
        assert req.include_umls_lookup is True

    def test_valid_full_request(self):
        req = TriageRequest(
            incident_desc="severe chest pain, diaphoretic",
            gcs_score=13,
            acvpu="confused",
            sbp=90,
            hr=120,
            include_umls_lookup=True,
        )
        assert req.gcs_score == 13
        assert req.sbp == 90
        assert req.hr == 120

    def test_incident_desc_too_short_rejected(self):
        with pytest.raises(ValidationError, match="at least 5 characters"):
            TriageRequest(incident_desc="abc")

    def test_incident_desc_too_long_rejected(self):
        with pytest.raises(ValidationError, match="at most 5000 characters"):
            TriageRequest(incident_desc="x" * 5001)

    def test_gcs_score_below_3_rejected(self):
        with pytest.raises(ValidationError):
            TriageRequest(incident_desc="test description", gcs_score=2)

    def test_gcs_score_above_15_rejected(self):
        with pytest.raises(ValidationError):
            TriageRequest(incident_desc="test description", gcs_score=16)

    def test_gcs_score_boundaries_valid(self):
        r3 = TriageRequest(incident_desc="test description", gcs_score=3)
        assert r3.gcs_score == 3
        r15 = TriageRequest(incident_desc="test description", gcs_score=15)
        assert r15.gcs_score == 15

    def test_sbp_below_30_rejected(self):
        with pytest.raises(ValidationError):
            TriageRequest(incident_desc="test description", sbp=29)

    def test_hr_below_20_rejected(self):
        with pytest.raises(ValidationError):
            TriageRequest(incident_desc="test description", hr=19)


# ── Enums ─────────────────────────────────────────────────────────────────


class TestEnums:
    def test_triage_level_values(self):
        assert TriageLevel.P1.value == "P1"
        assert TriageLevel.P4.value == "P4"

    def test_severity_level_values(self):
        assert SeverityLevel.CRITICAL.value == "critical"
        assert SeverityLevel.LOW.value == "low"

    def test_clinical_category_values(self):
        assert ClinicalCategory.RESPIRATORY.value == "RESPIRATORY"
        assert ClinicalCategory.UNKNOWN.value == "UNKNOWN"

    def test_all_categories_present(self):
        expected = {
            "RESPIRATORY",
            "CARDIOVASCULAR",
            "NEUROLOGICAL",
            "TRAUMA",
            "OBSTETRIC",
            "PAEDIATRIC",
            "UNKNOWN",
        }
        actual = {c.value for c in ClinicalCategory}
        assert actual == expected


# ── ExtractedKeyword ──────────────────────────────────────────────────────


class TestExtractedKeyword:
    def test_minimal_keyword(self):
        kw = ExtractedKeyword(text="chest pain", category=ClinicalCategory.CARDIOVASCULAR)
        assert kw.is_negated is False
        assert kw.severity_modifiers == []
        assert kw.source == "rules"

    def test_negated_keyword(self):
        kw = ExtractedKeyword(
            text="chest pain", category=ClinicalCategory.CARDIOVASCULAR, is_negated=True
        )
        assert kw.is_negated is True

    def test_keyword_with_modifiers(self):
        kw = ExtractedKeyword(
            text="severe bleeding",
            category=ClinicalCategory.CARDIOVASCULAR,
            severity_modifiers=["SEVERITY_SEVERE", "ACTIVE"],
        )
        assert len(kw.severity_modifiers) == 2

    def test_keyword_with_codes(self):
        kw = ExtractedKeyword(
            text="cardiac arrest",
            category=ClinicalCategory.CARDIOVASCULAR,
            icd10_prefix="I46",
            snomed_hint="419422000",
        )
        assert kw.icd10_prefix == "I46"
        assert kw.snomed_hint == "419422000"


# ── DiagnosisRankItem ─────────────────────────────────────────────────────


class TestDiagnosisRankItem:
    def test_minimal_rank_item(self):
        item = DiagnosisRankItem(
            rank=1,
            canonical_name="Cardiac Arrest",
            severity_level=SeverityLevel.CRITICAL,
            esi_level=1,
        )
        assert item.rank == 1
        assert item.umls_cui is None
        assert item.score_breakdown == {}
        assert item.scoring_systems_applied == []

    def test_esi_level_boundaries(self):
        item1 = DiagnosisRankItem(
            rank=1, canonical_name="Test", severity_level=SeverityLevel.CRITICAL, esi_level=1
        )
        assert item1.esi_level == 1
        item5 = DiagnosisRankItem(
            rank=1, canonical_name="Test", severity_level=SeverityLevel.LOW, esi_level=5
        )
        assert item5.esi_level == 5

    def test_esi_level_out_of_range_rejected(self):
        with pytest.raises(ValidationError):
            DiagnosisRankItem(
                rank=1, canonical_name="Test", severity_level=SeverityLevel.LOW, esi_level=6
            )


# ── TriageMetadata ────────────────────────────────────────────────────────


class TestTriageMetadata:
    def test_minimal_metadata(self):
        meta = TriageMetadata(request_id="req-001")
        assert meta.request_id == "req-001"
        assert meta.processing_times_ms == {}
        assert meta.shock_index is None

    def test_metadata_with_shock_index(self):
        meta = TriageMetadata(request_id="req-002", shock_index=1.33)
        assert meta.shock_index == 1.33


# ── TriageResponse ────────────────────────────────────────────────────────


class TestTriageResponse:
    def test_minimal_response(self):
        meta = TriageMetadata(request_id="r1")
        resp = TriageResponse(
            diagnosis_ranking=[],
            keywords=[],
            triage_level=TriageLevel.P2,
            esi_level=2,
            metadata=meta,
        )
        assert resp.triage_level == TriageLevel.P2
        assert resp.degraded_mode is False

    def test_full_response_serialization(self):
        meta = TriageMetadata(request_id="r1", shock_index=1.2)
        kw = ExtractedKeyword(text="chest pain", category=ClinicalCategory.CARDIOVASCULAR)
        item = DiagnosisRankItem(
            rank=1, canonical_name="Chest Pain", severity_level=SeverityLevel.HIGH, esi_level=2
        )
        resp = TriageResponse(
            diagnosis_ranking=[item],
            keywords=[kw],
            triage_level=TriageLevel.P1,
            esi_level=1,
            degraded_mode=True,
            metadata=meta,
        )
        data = resp.model_dump()
        assert data["triage_level"] == "P1"
        assert data["degraded_mode"] is True
        assert len(data["diagnosis_ranking"]) == 1
        assert data["metadata"]["shock_index"] == 1.2


# ── FacilitySearchRequest ─────────────────────────────────────────────────


class TestFacilitySearchRequest:
    def test_minimal_request(self):
        req = FacilitySearchRequest(lat=-1.2921, lon=36.8219)
        assert req.radius_km == 50.0
        assert req.level_min == 1
        assert req.max_results == 3

    def test_full_request(self):
        req = FacilitySearchRequest(
            lat=-1.2921,
            lon=36.8219,
            radius_km=25.0,
            level_min=4,
            required_services=["icu", "surgery"],
            max_results=5,
        )
        assert req.required_services == ["icu", "surgery"]
        assert req.max_results == 5

    def test_radius_too_large_rejected(self):
        with pytest.raises(ValidationError):
            FacilitySearchRequest(lat=0, lon=0, radius_km=201)

    def test_radius_too_small_rejected(self):
        with pytest.raises(ValidationError):
            FacilitySearchRequest(lat=0, lon=0, radius_km=0.5)

    def test_max_results_boundary(self):
        req1 = FacilitySearchRequest(lat=0, lon=0, max_results=1)
        assert req1.max_results == 1
        req10 = FacilitySearchRequest(lat=0, lon=0, max_results=10)
        assert req10.max_results == 10

    def test_max_results_out_of_range_rejected(self):
        with pytest.raises(ValidationError):
            FacilitySearchRequest(lat=0, lon=0, max_results=11)


# ── FacilityResult ────────────────────────────────────────────────────────


class TestFacilityResult:
    def test_minimal_facility(self):
        f = FacilityResult(
            facility_id="F001",
            name="Test Hospital",
            level=4,
            lat=-1.29,
            lon=36.82,
            distance_km=5.2,
            eta_minutes=5.2,
        )
        assert f.phone is None
        assert f.services == []
        assert f.capacity_status is None

    def test_facility_serialization(self):
        f = FacilityResult(
            facility_id="F001",
            name="Nairobi Hospital",
            level=5,
            lat=-1.29,
            lon=36.82,
            distance_km=2.5,
            eta_minutes=2.5,
            services=["icu", "surgery", "cardiac"],
            phone="+254202845000",
            capacity_status="available",
        )
        data = f.model_dump()
        assert data["services"] == ["icu", "surgery", "cardiac"]
        assert data["capacity_status"] == "available"


# ── FacilitySearchResponse ────────────────────────────────────────────────


class TestFacilitySearchResponse:
    def test_empty_response(self):
        resp = FacilitySearchResponse(facilities=[], total_found=0)
        assert resp.data_as_of is None
        assert resp.geocoded_location is None

    def test_response_with_facilities(self):
        f = FacilityResult(
            facility_id="F001",
            name="Test",
            level=4,
            lat=0,
            lon=0,
            distance_km=1.0,
            eta_minutes=1.0,
        )
        resp = FacilitySearchResponse(
            facilities=[f],
            total_found=1,
            data_as_of="2024-02-01",
            geocoded_location="-1.2921,36.8219",
        )
        assert resp.total_found == 1
        assert resp.geocoded_location == "-1.2921,36.8219"


# ── CapturePayload ────────────────────────────────────────────────────────


class TestCapturePayload:
    def test_minimal_payload(self):
        payload = CapturePayload(
            dispatchId="disp-001",
            patientInfo=PatientInfo(),
            incidentInfo=IncidentInfo(description="chest pain"),
        )
        assert payload.dispatchId == "disp-001"
        assert payload.patientInfo.consciousness is None
        assert payload.patientInfo.activelyBleeding is False
        assert payload.metadata.source == "web_listener"

    def test_full_payload(self):
        payload = CapturePayload(
            dispatchId="disp-002",
            patientInfo=PatientInfo(
                ageGroup="adult",
                approxAge="34",
                sex="male",
                consciousness="unconscious",
                breathing="not breathing",
                activelyBleeding=True,
                medicalHistory="hypertension",
            ),
            incidentInfo=IncidentInfo(
                type="Trauma",
                description="car accident, multiple injuries",
                location={"address": "Nairobi", "landmark": "near stadium"},
                priority="critical",
            ),
            metadata=CaptureMetadata(
                source="web_listener",
                capture_version="1.0",
                raw_form={"field1": "value1"},
            ),
        )
        assert payload.patientInfo.activelyBleeding is True
        assert payload.incidentInfo.priority == "critical"
        assert payload.metadata.capture_version == "1.0"

    def test_dispatch_id_required(self):
        with pytest.raises(ValidationError, match="at least 1 character"):
            CapturePayload(
                dispatchId="",
                patientInfo=PatientInfo(),
                incidentInfo=IncidentInfo(description="test"),
            )

    def test_incident_description_required(self):
        with pytest.raises(ValidationError):
            CapturePayload(
                dispatchId="d1",
                patientInfo=PatientInfo(),
                incidentInfo=IncidentInfo(description=""),
            )

    def test_consciousness_mapping_documented(self):
        """Document the consciousness-to-GCS mapping in tests."""
        mappings = {
            "unconscious": 3,
            "responds to pain": 7,
            "responds to voice": 9,
            "confused": 13,
            "alert": 15,
        }
        # These mappings are used in ambulance-cdss's from-capture endpoint
        for consciousness, _expected_gcs in mappings.items():
            p = PatientInfo(consciousness=consciousness)
            assert p.consciousness == consciousness
