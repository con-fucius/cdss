"""tests/test_entity_extraction.py.

EPIC 9.1 — Tests for NLP entity extraction endpoint:
- POST /triage/extract-entities
- Regex degraded-mode extraction (BP, HR, RR, GCS)
- Chief complaint suggestion via keywords
- Location extraction from landmarks
- Graceful degradation on empty/missing data
- NLPExtractorClient unit tests

Uses the synchronous TestClient from FastAPI/Starlette to avoid
pytest-asyncio version compatibility issues with async fixtures.
"""

from __future__ import annotations

import asyncio
import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.external.nlp_extractor import ExtractedEntities, NLPExtractorClient


class TestExtractEntities:
    """POST /triage/extract-entities"""

    @pytest.fixture(autouse=True)
    def setup_client(self):
        with TestClient(app) as c:
            self.client = c

    def test_empty_transcript_returns_low_confidence(self):
        resp = self.client.post(
            "/triage/extract-entities",
            json={"transcript": "hello how are you"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["degraded_mode"] is True
        assert data["vitals"] == {}
        assert data["chief_complaint_suggestion"] is None
        assert data["location_text"] is None
        assert data["confidence"] <= 0.3

    def test_bp_extraction(self):
        resp = self.client.post(
            "/triage/extract-entities",
            json={"transcript": "The patient's BP is 120 over 80"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["vitals"]["bp_systolic"] == 120
        assert data["vitals"]["bp_diastolic"] == 80
        assert data["confidence"] >= 0.6

    def test_bp_extraction_with_slash(self):
        resp = self.client.post(
            "/triage/extract-entities",
            json={"transcript": "BP is 130/85"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["vitals"]["bp_systolic"] == 130
        assert data["vitals"]["bp_diastolic"] == 85

    def test_heart_rate_extraction(self):
        resp = self.client.post(
            "/triage/extract-entities",
            json={"transcript": "Heart rate is 95 beats per minute"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["vitals"]["heart_rate"] == 95

    def test_heart_rate_extraction_pulse(self):
        resp = self.client.post(
            "/triage/extract-entities",
            json={"transcript": "pulse 110"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["vitals"]["heart_rate"] == 110

    def test_respiratory_rate_extraction(self):
        resp = self.client.post(
            "/triage/extract-entities",
            json={"transcript": "Respiratory rate is 22"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["vitals"]["respiratory_rate"] == 22

    def test_gcs_extraction(self):
        resp = self.client.post(
            "/triage/extract-entities",
            json={"transcript": "GCS is 14"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["vitals"]["gcs_total"] == 14
        assert data["confidence"] >= 0.7

    def test_chest_pain_suggestion(self):
        resp = self.client.post(
            "/triage/extract-entities",
            json={"transcript": "Patient complains of chest pain and tightness"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["chief_complaint_suggestion"] == "chest pain"
        assert data["confidence"] >= 0.7

    def test_cardiac_arrest_suggestion(self):
        resp = self.client.post(
            "/triage/extract-entities",
            json={"transcript": "Patient is not breathing, no pulse detected"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["chief_complaint_suggestion"] == "cardiac arrest"

    def test_choking_suggestion(self):
        resp = self.client.post(
            "/triage/extract-entities",
            json={"transcript": "Patient is choking on food"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["chief_complaint_suggestion"] == "choking"

    def test_location_extraction_kenyatta(self):
        resp = self.client.post(
            "/triage/extract-entities",
            json={"transcript": "Patient is near Kenyatta hospital"},
        )
        assert resp.status_code == 200
        data = resp.json()
        # Full place name is extracted (not just the landmark)
        assert "Kenyatta" in data["location_text"]

    def test_location_extraction_westlands(self):
        resp = self.client.post(
            "/triage/extract-entities",
            json={"transcript": "The incident is in Westlands area"},
        )
        assert resp.status_code == 200
        data = resp.json()
        # Full place name is extracted (not just the landmark)
        assert "Westlands" in data["location_text"]

    def test_multiple_vitals_combined(self):
        resp = self.client.post(
            "/triage/extract-entities",
            json={
                "transcript": "BP is 90 over 60, heart rate 120, GCS 10"
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["vitals"]["bp_systolic"] == 90
        assert data["vitals"]["bp_diastolic"] == 60
        assert data["vitals"]["heart_rate"] == 120
        assert data["vitals"]["gcs_total"] == 10
        assert data["confidence"] >= 0.7

    def test_case_insensitive_bp(self):
        resp = self.client.post(
            "/triage/extract-entities",
            json={"transcript": "BP IS 140 OVER 90"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["vitals"]["bp_systolic"] == 140
        assert data["vitals"]["bp_diastolic"] == 90

    def test_transcript_too_long_rejected(self):
        resp = self.client.post(
            "/triage/extract-entities",
            json={"transcript": "x" * 5001},
        )
        assert resp.status_code == 422

    def test_empty_transcript_rejected(self):
        resp = self.client.post(
            "/triage/extract-entities",
            json={"transcript": ""},
        )
        assert resp.status_code == 422


class TestNLPExtractorClient:
    """app/external/nlp_extractor.py — unit tests."""

    def test_returns_none_when_unconfigured(self):
        """When TRIAGE_RANKER_BASE_URL is empty, client returns None."""
        with patch.dict(os.environ, {"TRIAGE_RANKER_BASE_URL": ""}, clear=False):
            client = NLPExtractorClient()
            result = asyncio.run(client.extract("chest pain"))
            assert result is None

    def test_degraded_mode_flag(self):
        """Default ExtractedEntities has degraded_mode=True."""
        entities = ExtractedEntities()
        assert entities.degraded_mode is True
        assert entities.confidence == 0.0
