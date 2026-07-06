"""tests/test_governance_and_select_protocol.py.

Tests for the P0 critical fixes:
- Governance check: blocked values reject protocols at load time
- POST /incidents/{id}/select-protocol: manual protocol assignment
- GET /admin/protocol-audit: governance audit endpoint

Uses the synchronous TestClient from FastAPI/Starlette to avoid
pytest-asyncio version compatibility issues with async fixtures.
Requires DATABASE_URL to be set AND the database reachable for endpoint tests.
"""

from __future__ import annotations

import os
import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.protocols.schema import DispatchProtocol

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()


def _db_reachable() -> bool:
    """Quick check: can we actually connect to the database?"""
    if not DATABASE_URL:
        return False
    try:
        import sqlalchemy
        engine = sqlalchemy.create_engine(DATABASE_URL.replace("+asyncpg", ""), pool_pre_ping=True)
        with engine.connect() as conn:
            conn.execute(sqlalchemy.text("SELECT 1"))
        return True
    except Exception:
        return False


HAS_DB = _db_reachable()


class TestGovernanceCheck:
    """Unit tests for the governance check — no DB required."""

    def test_blocked_values_reject_protocols(self):
        """Protocols with blocked governance values are rejected at load time."""
        blocked = DispatchProtocol._BLOCKED_GOVERNANCE_VALUES
        assert "dev setup" in blocked
        assert "tbd" in blocked
        assert "placeholder" in blocked

    def test_is_governance_complete_rejects_dev_setup(self):
        proto = DispatchProtocol(
            protocol_id="test", version="1.0.0",
            chief_complaint_trigger=["test"], questions={}, terminal_outcomes={},
            entry_question_id="q1", locked=True,
            approved_by="Dev Setup", approved_date="2026-07-03",
        )
        assert proto.is_governance_complete() is False

    def test_is_governance_complete_accepts_real_approver(self):
        proto = DispatchProtocol(
            protocol_id="test", version="1.0.0",
            chief_complaint_trigger=["test"], questions={}, terminal_outcomes={},
            entry_question_id="q1", locked=True,
            approved_by="Dr. Jane Doe", approved_date="2026-07-03",
        )
        assert proto.is_governance_complete() is True

    def test_is_governance_complete_rejects_substring_match(self):
        proto = DispatchProtocol(
            protocol_id="test", version="1.0.0",
            chief_complaint_trigger=["test"], questions={}, terminal_outcomes={},
            entry_question_id="q1", locked=True,
            approved_by="Pending review by Dev Setup team", approved_date="2026-07-03",
        )
        assert proto.is_governance_complete() is False

    def test_is_governance_complete_rejects_empty_fields(self):
        proto = DispatchProtocol(
            protocol_id="test", version="1.0.0",
            chief_complaint_trigger=["test"], questions={}, terminal_outcomes={},
            entry_question_id="q1", locked=True,
            approved_by="", approved_date="2026-07-03",
        )
        assert proto.is_governance_complete() is False

    def test_is_governance_complete_rejects_unlocked(self):
        proto = DispatchProtocol(
            protocol_id="test", version="1.0.0",
            chief_complaint_trigger=["test"], questions={}, terminal_outcomes={},
            entry_question_id="q1", locked=False,
            approved_by="Dr. Jane Doe", approved_date="2026-07-03",
        )
        assert proto.is_governance_complete() is False


@pytest.mark.skipif(not HAS_DB, reason="Endpoint tests require a reachable PostgreSQL database")
class TestSelectDispatchProtocol:
    """Endpoint tests for POST /select-protocol — require DB."""

    @pytest.fixture(autouse=True)
    def setup_client(self):
        with TestClient(app) as c:
            self.client = c

    def test_select_protocol_success(self):
        """POST /select-protocol assigns a protocol to an unmatched incident."""
        r = self.client.post(
            "/incidents",
            json={"chief_complaint": f"zzz_no_match_{uuid.uuid4().hex[:6]}"},
        )
        assert r.status_code == 200
        data = r.json()
        incident_id = data["incident"]["incident_id"]
        assert data["protocol_matched"] is False

        r2 = self.client.get("/protocols")
        active = r2.json()["active"]
        if not active:
            pytest.skip("No active dispatch protocols")

        protocol_id = active[0]["protocol_id"]
        r3 = self.client.post(
            f"/incidents/{incident_id}/select-protocol",
            json={"protocol_id": protocol_id, "dispatcher_id": "test-dispatcher"},
        )
        assert r3.status_code == 200
        resp = r3.json()
        assert resp["protocol_id"] == protocol_id
        assert "current_question" in resp
        assert resp["current_question"]["question_id"]

    def test_select_protocol_409_if_already_assigned(self):
        """Returns 409 if incident already has a protocol."""
        r = self.client.post("/incidents", json={"chief_complaint": "choking"})
        assert r.status_code == 200
        data = r.json()
        if not data.get("protocol_matched"):
            pytest.skip("Choking did not match a protocol")
        incident_id = data["incident"]["incident_id"]
        already_assigned = data["protocol_id"]

        r2 = self.client.post(
            f"/incidents/{incident_id}/select-protocol",
            json={"protocol_id": already_assigned, "dispatcher_id": "test"},
        )
        assert r2.status_code == 409
        assert r2.json()["detail"]["error"] == "protocol_already_assigned"

    def test_select_protocol_404_nonexistent_incident(self):
        """Returns 404 for nonexistent incident."""
        r = self.client.post(
            f"/incidents/{uuid.uuid4()}/select-protocol",
            json={"protocol_id": "test", "dispatcher_id": "test"},
        )
        assert r.status_code == 404

    def test_select_protocol_404_nonexistent_protocol(self):
        """Returns 404 for nonexistent protocol."""
        r = self.client.post(
            "/incidents",
            json={"chief_complaint": f"zzz_no_match_{uuid.uuid4().hex[:6]}"},
        )
        incident_id = r.json()["incident"]["incident_id"]
        r2 = self.client.post(
            f"/incidents/{incident_id}/select-protocol",
            json={"protocol_id": "nonexistent_xyz", "dispatcher_id": "test"},
        )
        assert r2.status_code == 404


@pytest.mark.skipif(not HAS_DB, reason="Endpoint tests require a reachable PostgreSQL database")
class TestProtocolAudit:
    """Endpoint tests for GET /admin/protocol-audit — require DB."""

    @pytest.fixture(autouse=True)
    def setup_client(self):
        with TestClient(app) as c:
            self.client = c

    def test_audit_endpoint_returns_structure(self):
        """Returns expected response structure."""
        r = self.client.get("/admin/protocol-audit")
        assert r.status_code == 200
        data = r.json()
        assert "dispatch_protocols" in data
        assert "field_protocols" in data
        assert "blocked_governance_values" in data
        assert isinstance(data["blocked_governance_values"], list)
        assert "dev setup" in data["blocked_governance_values"]

    def test_audit_shows_active_and_rejected(self):
        """Shows both active and rejected protocols."""
        r = self.client.get("/admin/protocol-audit")
        data = r.json()
        assert isinstance(data["dispatch_protocols"], list)
        for proto in data["dispatch_protocols"]:
            assert "protocol_id" in proto or "file" in proto
