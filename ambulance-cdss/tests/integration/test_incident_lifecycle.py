"""tests/integration/test_incident_lifecycle.py.

Integration tests for the three most critical paths:
1. Create incident -> submit answers -> reach terminal outcome
2. Add vitals -> NEWS2 + GCS trend alert returned
3. Purge expired incidents

These tests run against a real PostgreSQL database configured via
DATABASE_URL in the environment. They are skipped when DATABASE_URL is
not set so the unit test suite runs without infrastructure.

Discipline:
- No mocking of the database layer.
- External services (facility registry, emergency dispatch) are NOT
  called — no network calls in these tests.
- Each test creates its own incident and is order-independent.
- asyncio_mode = "auto" in pyproject.toml.
- The httpx ASGITransport is used so the full FastAPI middleware stack
  (CORS, rate limiting, metrics) runs, giving a realistic integration.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import update

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="Integration tests require DATABASE_URL to be set.",
)


@pytest_asyncio.fixture(scope="module")
async def client():
    """Async httpx client pointed at the FastAPI app via ASGI transport.
    Initialises the DB engine once for the module and tears down after.
    """
    from app.db import close_engine, init_engine
    from app.main import app
    from app.protocols.field_registry import field_registry
    from app.protocols.registry import registry

    await init_engine()
    registry.load_all()
    field_registry.load_all()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac

    await close_engine()


# ─────────────────────────────────────────────────────────────────────────────
# Helper
# ─────────────────────────────────────────────────────────────────────────────


async def _create_incident(client: AsyncClient, complaint: str = "test incident") -> str:
    resp = await client.post("/incidents", json={"chief_complaint": complaint})
    assert resp.status_code == 200, resp.text
    return resp.json()["incident"]["incident_id"]


# ─────────────────────────────────────────────────────────────────────────────
# Path 1 — Create incident → submit answers → terminal outcome
# ─────────────────────────────────────────────────────────────────────────────


class TestIncidentLifecycle:
    async def test_create_incident_no_protocol_match(self, client: AsyncClient):
        """Unmatched chief complaint still creates the incident successfully."""
        resp = await client.post(
            "/incidents",
            json={
                "chief_complaint": "test_integration_complaint_no_match_xyz",
            },
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert "incident" in data
        assert data["protocol_matched"] is False
        assert uuid.UUID(data["incident"]["incident_id"])  # valid UUID

    async def test_get_unknown_incident_returns_404(self, client: AsyncClient):
        resp = await client.get(f"/incidents/{uuid.uuid4()}")
        assert resp.status_code == 404

    async def test_incident_retrieved_after_creation(self, client: AsyncClient):
        incident_id = await _create_incident(client, "test retrieve")
        resp = await client.get(f"/incidents/{incident_id}")
        assert resp.status_code == 200
        assert resp.json()["incident_id"] == incident_id

    async def test_status_transition_forward_succeeds(self, client: AsyncClient):
        incident_id = await _create_incident(client, "test status transitions")

        for status in ("dispatched", "on_scene", "transporting", "handoff_complete", "closed"):
            r = await client.post(
                f"/incidents/{incident_id}/status",
                json={"status": status},
            )
            assert r.status_code == 200, f"Failed on status={status}: {r.text}"
            assert r.json()["status"] == status

    async def test_invalid_status_transition_returns_422(self, client: AsyncClient):
        incident_id = await _create_incident(client, "test invalid transition")

        # Advance to on_scene
        await client.post(f"/incidents/{incident_id}/status", json={"status": "dispatched"})
        await client.post(f"/incidents/{incident_id}/status", json={"status": "on_scene"})

        # on_scene → dispatched is not a valid forward transition
        r = await client.post(
            f"/incidents/{incident_id}/status",
            json={"status": "dispatched"},
        )
        assert r.status_code == 422
        detail = r.json()["detail"]
        assert detail["error"] == "invalid_status_transition"
        assert detail["current"] == "on_scene"
        assert detail["requested"] == "dispatched"
        assert isinstance(detail["allowed"], list)

    async def test_closed_incident_cannot_transition(self, client: AsyncClient):
        incident_id = await _create_incident(client, "test closed terminal")
        await client.post(f"/incidents/{incident_id}/status", json={"status": "closed"})

        r = await client.post(f"/incidents/{incident_id}/status", json={"status": "dispatched"})
        assert r.status_code == 422
        assert r.json()["detail"]["error"] == "invalid_status_transition"
        assert r.json()["detail"]["allowed"] == []

    async def test_append_note_accumulates(self, client: AsyncClient):
        incident_id = await _create_incident(client, "test notes")

        r1 = await client.patch(
            f"/incidents/{incident_id}/notes",
            json={"note_text": "First note.", "author_id": "disp-1"},
        )
        assert r1.status_code == 200
        assert "First note." in r1.json()["notes"]

        r2 = await client.patch(
            f"/incidents/{incident_id}/notes",
            json={"note_text": "Second note.", "author_id": "disp-1"},
        )
        assert r2.status_code == 200
        notes = r2.json()["notes"]
        assert "First note." in notes
        assert "Second note." in notes
        # Second note must appear after first note (appended)
        assert notes.index("First note.") < notes.index("Second note.")

    async def test_list_incidents_returns_results(self, client: AsyncClient):
        await _create_incident(client, "test list incidents")
        r = await client.get("/incidents", params={"limit": 10})
        assert r.status_code == 200
        data = r.json()
        assert "incidents" in data
        assert "count" in data
        assert data["count"] >= 1

    async def test_list_incidents_limit_validation(self, client: AsyncClient):
        r = await client.get("/incidents", params={"limit": 201})
        assert r.status_code == 422

    async def test_list_incidents_chief_complaint_too_short(self, client: AsyncClient):
        r = await client.get("/incidents", params={"chief_complaint_contains": "a"})
        assert r.status_code == 422

    async def test_list_incidents_chief_complaint_filter(self, client: AsyncClient):
        unique = f"test_complaint_filter_{uuid.uuid4().hex[:8]}"
        incident_id = await _create_incident(client, unique)

        r = await client.get(
            "/incidents",
            params={"chief_complaint_contains": unique[:20]},
        )
        assert r.status_code == 200
        ids = [inc["incident_id"] for inc in r.json()["incidents"]]
        assert incident_id in ids

    async def test_protocol_driven_answer_walk(self, client: AsyncClient):
        """If an approved active protocol exists, walk it to terminal outcome.
        Skipped when all protocols are still PLACEHOLDER-approved.
        """
        from app.protocols.registry import registry

        active = registry.list_active()
        if not active:
            pytest.skip("No approved active protocols — skipping answer walk.")

        proto = registry.get(active[0]["protocol_id"])
        trigger = proto.chief_complaint_trigger[0]

        create_resp = await client.post("/incidents", json={"chief_complaint": trigger})
        assert create_resp.status_code == 200
        data = create_resp.json()
        assert data["protocol_matched"] is True
        incident_id = data["incident"]["incident_id"]

        current_question = data["current_question"]
        terminal = False
        for _ in range(30):  # hard cap — protocol must terminate
            first_answer = current_question["valid_answers"][0]
            answer_resp = await client.post(
                f"/incidents/{incident_id}/answer",
                json={
                    "current_question_id": current_question["question_id"],
                    "answer": first_answer,
                    "dispatcher_id": "integration-test",
                },
            )
            assert answer_resp.status_code == 200, answer_resp.text
            resp_data = answer_resp.json()
            if resp_data.get("terminal"):
                terminal = True
                outcome = resp_data["outcome"]
                assert "priority_code" in outcome
                assert "recommended_unit_type" in outcome
                assert "pre_arrival_instructions" in outcome
                break
            current_question = resp_data["current_question"]

        assert terminal, "Did not reach terminal outcome within 30 steps"

        # Incident priority_code must now be set
        get_resp = await client.get(f"/incidents/{incident_id}")
        assert get_resp.json()["priority_code"] == outcome["priority_code"]

        # Full record must assemble
        full_resp = await client.get(f"/incidents/{incident_id}/full")
        assert full_resp.status_code == 200
        assert len(full_resp.json()["dispatch_log"]) > 0

        # Handoff summary must return
        handoff_resp = await client.get(f"/incidents/{incident_id}/handoff")
        assert handoff_resp.status_code == 200
        assert "text_rendering" in handoff_resp.json()

        # Timeline must return events
        timeline_resp = await client.get(f"/incidents/{incident_id}/timeline")
        assert timeline_resp.status_code == 200
        assert timeline_resp.json()["event_count"] > 0

        # Audit export must return plain text
        export_resp = await client.get(f"/incidents/{incident_id}/export")
        assert export_resp.status_code == 200
        assert "INCIDENT AUDIT EXPORT" in export_resp.text

    async def test_out_of_script_answer_returns_422(self, client: AsyncClient):
        """Submitting an unrecognised answer to a question returns 422 with
        valid_answers in the response body.
        """
        from app.protocols.registry import registry

        active = registry.list_active()
        if not active:
            pytest.skip("No approved active protocols — skipping out-of-script test.")

        proto = registry.get(active[0]["protocol_id"])
        trigger = proto.chief_complaint_trigger[0]

        create_resp = await client.post("/incidents", json={"chief_complaint": trigger})
        incident_id = create_resp.json()["incident"]["incident_id"]
        entry_question = create_resp.json()["current_question"]

        r = await client.post(
            f"/incidents/{incident_id}/answer",
            json={
                "current_question_id": entry_question["question_id"],
                "answer": "this_is_not_a_valid_answer",
                "dispatcher_id": "test",
            },
        )
        assert r.status_code == 422
        detail = r.json()["detail"]
        assert detail["error"] == "out_of_script_answer"
        assert "valid_answers" in detail
        assert isinstance(detail["valid_answers"], list)


# ─────────────────────────────────────────────────────────────────────────────
# Path 2 — Add vitals → trend alert returned
# ─────────────────────────────────────────────────────────────────────────────


class TestVitalsTrendAlert:
    # Full valid vitals that produce a complete NEWS2 score
    _NORMAL_VITALS = {
        "recorded_by": "test-paramedic",
        "respiratory_rate": 16,
        "spo2": 97,
        "spo2_scale": 1,
        "supplemental_o2": False,
        "bp_systolic": 120,
        "heart_rate": 72,
        "consciousness": "A",
        "temperature": 36.8,
    }

    _DETERIORATED_VITALS = {
        "recorded_by": "test-paramedic",
        "respiratory_rate": 28,  # score 3
        "spo2": 91,  # score 2
        "spo2_scale": 1,
        "supplemental_o2": True,  # score 2
        "bp_systolic": 90,  # score 2
        "heart_rate": 115,  # score 2
        "consciousness": "V",  # score 3
        "temperature": 38.5,  # score 1
    }

    async def test_first_vitals_has_no_prior_data_trend(self, client: AsyncClient):
        incident_id = await _create_incident(client, "test vitals first")
        r = await client.post(f"/incidents/{incident_id}/vitals", json=self._NORMAL_VITALS)
        assert r.status_code == 200, r.text
        data = r.json()

        # All three fields must always be present
        assert "trend_alert" in data
        assert "gcs_trend_alert" in data
        assert "news2_missing_fields" in data

        # No prior vitals
        assert data["trend_alert"]["trend"] == "no_prior_data"
        assert data["trend_alert"]["delta"] is None

        # Normal vitals → low NEWS2 score
        assert data["news2_score"] is not None
        assert data["news2_score"] <= 2

    async def test_second_vitals_shows_deterioration(self, client: AsyncClient):
        incident_id = await _create_incident(client, "test vitals deterioration")

        r1 = await client.post(f"/incidents/{incident_id}/vitals", json=self._NORMAL_VITALS)
        prior_news2 = r1.json()["news2_score"]
        assert prior_news2 is not None

        r2 = await client.post(f"/incidents/{incident_id}/vitals", json=self._DETERIORATED_VITALS)
        assert r2.status_code == 200
        data = r2.json()

        trend = data["trend_alert"]
        assert trend["trend"] in ("deteriorating", "rapid_deterioration")
        assert trend["delta"] > 0
        assert trend["prior_news2"] == prior_news2
        assert trend["new_news2"] == data["news2_score"]

    async def test_improving_vitals_shows_improving_trend(self, client: AsyncClient):
        incident_id = await _create_incident(client, "test vitals improving")

        # Submit bad vitals first
        await client.post(f"/incidents/{incident_id}/vitals", json=self._DETERIORATED_VITALS)

        # Then good vitals
        r = await client.post(f"/incidents/{incident_id}/vitals", json=self._NORMAL_VITALS)
        assert r.status_code == 200
        trend = r.json()["trend_alert"]
        assert trend["trend"] == "improving"
        assert trend["delta"] < 0

    async def test_incomplete_vitals_returns_missing_fields(self, client: AsyncClient):
        incident_id = await _create_incident(client, "test vitals incomplete")
        incomplete = {k: v for k, v in self._NORMAL_VITALS.items() if k != "consciousness"}
        r = await client.post(f"/incidents/{incident_id}/vitals", json=incomplete)
        assert r.status_code == 200
        data = r.json()
        assert "consciousness" in data["news2_missing_fields"]
        assert data["news2_score"] is None

    async def test_gcs_trend_alert_no_prior(self, client: AsyncClient):
        incident_id = await _create_incident(client, "test gcs no prior")
        vitals_with_gcs = {**self._NORMAL_VITALS, "gcs_eye": 4, "gcs_verbal": 5, "gcs_motor": 6}
        r = await client.post(f"/incidents/{incident_id}/vitals", json=vitals_with_gcs)
        assert r.status_code == 200
        data = r.json()
        assert data["gcs_total"] == 15
        assert data["gcs_trend_alert"]["trend"] == "no_prior_data"

    async def test_gcs_deterioration_crosses_severity_threshold(self, client: AsyncClient):
        incident_id = await _create_incident(client, "test gcs threshold")

        # First: mild TBI range (GCS 15)
        vitals_high_gcs = {**self._NORMAL_VITALS, "gcs_eye": 4, "gcs_verbal": 5, "gcs_motor": 6}
        await client.post(f"/incidents/{incident_id}/vitals", json=vitals_high_gcs)

        # Second: severe TBI range (GCS 7)
        vitals_low_gcs = {**self._NORMAL_VITALS, "gcs_eye": 1, "gcs_verbal": 2, "gcs_motor": 4}
        r = await client.post(f"/incidents/{incident_id}/vitals", json=vitals_low_gcs)
        assert r.status_code == 200
        data = r.json()
        assert data["gcs_total"] == 7
        gcs_trend = data["gcs_trend_alert"]
        assert gcs_trend["trend"] in ("deteriorating", "rapid_deterioration")
        assert gcs_trend["crossed_severity_threshold"] is True

    async def test_vitals_history_accessible_on_full_record(self, client: AsyncClient):
        incident_id = await _create_incident(client, "test vitals history")
        await client.post(f"/incidents/{incident_id}/vitals", json=self._NORMAL_VITALS)
        await client.post(f"/incidents/{incident_id}/vitals", json=self._DETERIORATED_VITALS)

        full = await client.get(f"/incidents/{incident_id}/full")
        assert full.status_code == 200
        history = full.json()["vitals_history"]
        assert len(history) == 2
        # Must be in chronological order (asc)
        assert history[0]["recorded_at"] <= history[1]["recorded_at"]


# ─────────────────────────────────────────────────────────────────────────────
# Path 3 — Purge expired incidents
# ─────────────────────────────────────────────────────────────────────────────


class TestPurgeExpiredIncidents:
    async def _force_closed_at(self, incident_id: str, days_ago: int) -> None:
        """Directly stamp closed_at to N days ago in the DB."""
        from app.db import get_session
        from app.models import Incident

        stale = datetime.now(UTC) - timedelta(days=days_ago)
        async with get_session() as session:
            await session.execute(
                update(Incident)
                .where(Incident.incident_id == uuid.UUID(incident_id))
                .values(status="closed", closed_at=stale)
            )
            await session.commit()

    async def test_purge_nullifies_pii_on_expired_incident(self, client: AsyncClient):
        incident_id = await _create_incident(client, "test purge expired")

        # Stamp closed_at 31 days ago (past the 30-day retention window)
        await self._force_closed_at(incident_id, days_ago=31)

        r = await client.post("/admin/purge-expired-incidents")
        assert r.status_code == 200
        result = r.json()
        assert result["purged"] >= 1

        # Verify PII fields are null
        get_r = await client.get(f"/incidents/{incident_id}")
        assert get_r.status_code == 200
        data = get_r.json()
        assert data["caller_location_lat"] is None
        assert data["caller_location_lon"] is None
        assert data["caller_location_text"] is None
        assert data["pii_purged_at"] is not None

    async def test_purge_leaves_recent_incident_intact(self, client: AsyncClient):
        incident_id = await _create_incident(client, "test purge recent incident")
        # Stamp closed_at to just 5 days ago (within retention window)
        await self._force_closed_at(incident_id, days_ago=5)

        await client.post("/admin/purge-expired-incidents")

        data = (await client.get(f"/incidents/{incident_id}")).json()
        assert data["pii_purged_at"] is None  # must not be purged

    async def test_purge_is_idempotent(self, client: AsyncClient):
        """Calling purge twice does not error; second call reports 0 new purges."""
        incident_id = await _create_incident(client, "test purge idempotent")
        await self._force_closed_at(incident_id, days_ago=60)

        r1 = await client.post("/admin/purge-expired-incidents")
        assert r1.status_code == 200
        r1.json()["purged"]

        r2 = await client.post("/admin/purge-expired-incidents")
        assert r2.status_code == 200
        # pii_purged_at IS NULL filter means already-purged rows are skipped
        # The second call might purge 0 NEW incidents (our test incident
        # was already handled). It must not error.
        assert r2.json()["purged"] == 0

    async def test_admin_key_required_when_configured(self, client: AsyncClient):
        """When ADMIN_API_KEY is set in the environment, the purge endpoint
        must reject requests without the header. If ADMIN_API_KEY is not
        set (development default), this test is skipped.
        """
        key = os.getenv("ADMIN_API_KEY", "").strip()
        if not key:
            pytest.skip("ADMIN_API_KEY not configured — admin key check is bypassed in dev.")

        # Without the header — should be 403
        r = await client.post("/admin/purge-expired-incidents")
        assert r.status_code == 403
        assert r.json()["detail"]["error"] == "forbidden"

        # With the correct header — should succeed
        r2 = await client.post(
            "/admin/purge-expired-incidents",
            headers={"X-Admin-Key": key},
        )
        assert r2.status_code == 200
