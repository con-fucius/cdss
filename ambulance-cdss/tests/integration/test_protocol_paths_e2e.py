"""tests/integration/test_protocol_paths_e2e.py.

Comprehensive E2E tests hitting the live ambulance-cdss server on
localhost:8000. Tests multiple protocol paths and error paths.

Run with the server started: uvicorn app.main:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import uuid

import httpx

BASE = "http://localhost:8000"


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _client() -> httpx.Client:
    return httpx.Client(base_url=BASE, timeout=15)


def _create(client: httpx.Client, trigger: str) -> tuple[str, dict]:
    r = client.post("/incidents", json={"chief_complaint": trigger})
    assert r.status_code == 200, f"Create failed: {r.text}"
    data = r.json()
    assert data["protocol_matched"] is True, f"'{trigger}' did not match"
    return data["incident"]["incident_id"], data["current_question"]


def _walk(
    client: httpx.Client,
    iid: str,
    q: dict,
    choice: int = 0,
    choices: list[int] | None = None,
    cap: int = 30,
) -> dict:
    """Walk an incident to a terminal outcome.

    Args:
        choice: Default answer index for every step (used when *choices* is None).
        choices: Per-step answer indices, consumed in order.  When exhausted,
            falls back to *choice* for remaining steps.
    """
    step = 0
    for _ in range(caps := cap):
        valid = q["valid_answers"]
        if choices is not None and step < len(choices):
            idx = choices[step]
        else:
            idx = choice
        ans = valid[min(idx, len(valid) - 1)]
        r = client.post(
            f"/incidents/{iid}/answer",
            json={"current_question_id": q["question_id"], "answer": ans, "dispatcher_id": "e2e"},
        )
        assert r.status_code == 200, f"Answer failed ({q['question_id']}={ans}): {r.text}"
        d = r.json()
        if d.get("terminal"):
            return d["outcome"]
        q = d["current_question"]
        assert q is not None
        step += 1
    raise AssertionError(f"No terminal in {caps} steps")


# ─────────────────────────────────────────────────────────────────────────────
# Protocol paths
# ─────────────────────────────────────────────────────────────────────────────


class TestProtocolPaths:
    def test_choking(self):
        with _client() as c:
            iid, q = _create(c, "choking")
            out = _walk(c, iid, q)
            assert out["priority_code"]
            assert out["recommended_unit_type"]
            assert out["pre_arrival_instructions"]

    def test_respiratory_distress(self):
        with _client() as c:
            iid, q = _create(c, "difficulty breathing")
            out = _walk(c, iid, q)
            assert out["priority_code"]

    def test_obstetric_emergency(self):
        with _client() as c:
            iid, q = _create(c, "pregnant")
            out = _walk(c, iid, q)
            assert out["priority_code"]

    def test_paediatric_respiratory(self):
        with _client() as c:
            iid, q = _create(c, "child can't breathe")
            out = _walk(c, iid, q)
            assert out["priority_code"]

    def test_unresponsive_breathing(self):
        with _client() as c:
            iid, q = _create(c, "unresponsive")
            out = _walk(c, iid, q)
            assert out["priority_code"]

    def test_major_trauma_mva(self):
        with _client() as c:
            iid, q = _create(c, "car accident")
            out = _walk(c, iid, q)
            assert out["priority_code"]

    def test_trauma_moi(self):
        with _client() as c:
            iid, q = _create(c, "fell down")
            out = _walk(c, iid, q)
            assert out["priority_code"]

    def test_cardiac_arrest(self):
        with _client() as c:
            iid, q = _create(c, "not breathing")
            # Walk: q1_conscious=no(1), q2_breathing=not_breathing(2),
            # q3_pulse_check=no_pulse(1), q4_cpr_capability=yes(0)
            out = _walk(c, iid, q, choices=[1, 2, 1, 0])
            assert out["priority_code"] == "P1_CARDIAC_ARREST"

    def test_cardiac_arrest_alternate_branch(self):
        with _client() as c:
            iid, q = _create(c, "not breathing")
            out = _walk(c, iid, q, choice=1)
            assert out["priority_code"]

    def test_terminal_persists_in_record(self):
        with _client() as c:
            iid, q = _create(c, "choking")
            out = _walk(c, iid, q)
            inc = c.get(f"/incidents/{iid}").json()
            assert inc["priority_code"] == out["priority_code"]

    def test_dispatch_log_after_walk(self):
        with _client() as c:
            iid, q = _create(c, "difficulty breathing")
            _walk(c, iid, q)
            full = c.get(f"/incidents/{iid}/full").json()
            assert len(full["dispatch_log"]) > 0

    def test_handoff_after_walk(self):
        with _client() as c:
            iid, q = _create(c, "car accident")
            _walk(c, iid, q)
            ho = c.get(f"/incidents/{iid}/handoff").json()
            assert ho["chief_complaint"]
            assert ho["priority_code"]
            assert len(ho["dispatch_qa"]) > 0

    def test_timeline_after_walk(self):
        with _client() as c:
            iid, q = _create(c, "pregnant")
            _walk(c, iid, q)
            tl = c.get(f"/incidents/{iid}/timeline").json()
            assert tl["event_count"] > 0

    def test_export_after_walk(self):
        with _client() as c:
            iid, q = _create(c, "child can't breathe")
            _walk(c, iid, q)
            r = c.get(f"/incidents/{iid}/export")
            assert r.status_code == 200
            assert "INCIDENT AUDIT EXPORT" in r.text


# ─────────────────────────────────────────────────────────────────────────────
# Error paths
# ─────────────────────────────────────────────────────────────────────────────


class TestErrorPaths:
    def test_out_of_script_choking(self):
        with _client() as c:
            iid, q = _create(c, "choking")
            r = c.post(
                f"/incidents/{iid}/answer",
                json={"current_question_id": q["question_id"], "answer": "garbage_xyz", "dispatcher_id": "e"},
            )
            assert r.status_code == 422
            assert r.json()["detail"]["error"] == "out_of_script_answer"
            assert len(r.json()["detail"]["valid_answers"]) > 0

    def test_out_of_script_respiratory(self):
        with _client() as c:
            iid, q = _create(c, "difficulty breathing")
            r = c.post(
                f"/incidents/{iid}/answer",
                json={"current_question_id": q["question_id"], "answer": "invalid", "dispatcher_id": "e"},
            )
            assert r.status_code == 422
            assert r.json()["detail"]["error"] == "out_of_script_answer"

    def test_out_of_script_obstetric(self):
        with _client() as c:
            iid, q = _create(c, "pregnant")
            r = c.post(
                f"/incidents/{iid}/answer",
                json={"current_question_id": q["question_id"], "answer": "no_such", "dispatcher_id": "e"},
            )
            assert r.status_code == 422

    def test_invalid_question_id(self):
        with _client() as c:
            iid, _ = _create(c, "choking")
            r = c.post(
                f"/incidents/{iid}/answer",
                json={"current_question_id": "q_nonexistent", "answer": "yes", "dispatcher_id": "e"},
            )
            assert r.status_code == 404

    def test_answer_on_unmatched_incident(self):
        with _client() as c:
            r = c.post("/incidents", json={"chief_complaint": f"zzz_no_match_{uuid.uuid4().hex[:6]}"})
            iid = r.json()["incident"]["incident_id"]
            r2 = c.post(
                f"/incidents/{iid}/answer",
                json={"current_question_id": "q1", "answer": "yes", "dispatcher_id": "e"},
            )
            assert r2.status_code == 400

    def test_invalid_status_value(self):
        with _client() as c:
            r = c.post("/incidents", json={"chief_complaint": f"test_{uuid.uuid4().hex[:6]}"})
            iid = r.json()["incident"]["incident_id"]
            r2 = c.post(f"/incidents/{iid}/status", json={"status": "not_real"})
            assert r2.status_code == 422

    def test_set_received_returns_422(self):
        with _client() as c:
            r = c.post("/incidents", json={"chief_complaint": f"test_{uuid.uuid4().hex[:6]}"})
            iid = r.json()["incident"]["incident_id"]
            r2 = c.post(f"/incidents/{iid}/status", json={"status": "received"})
            assert r2.status_code == 422

    def test_backward_transition(self):
        with _client() as c:
            r = c.post("/incidents", json={"chief_complaint": f"test_{uuid.uuid4().hex[:6]}"})
            iid = r.json()["incident"]["incident_id"]
            c.post(f"/incidents/{iid}/status", json={"status": "dispatched"})
            c.post(f"/incidents/{iid}/status", json={"status": "on_scene"})
            c.post(f"/incidents/{iid}/status", json={"status": "transporting"})
            r2 = c.post(f"/incidents/{iid}/status", json={"status": "dispatched"})
            assert r2.status_code == 422
            assert r2.json()["detail"]["error"] == "invalid_status_transition"
            assert r2.json()["detail"]["current"] == "transporting"

    def test_skip_status_returns_422(self):
        with _client() as c:
            r = c.post("/incidents", json={"chief_complaint": f"test_{uuid.uuid4().hex[:6]}"})
            iid = r.json()["incident"]["incident_id"]
            r2 = c.post(f"/incidents/{iid}/status", json={"status": "transporting"})
            assert r2.status_code == 422

    def test_dispatch_without_terminal_returns_400(self):
        with _client() as c:
            r = c.post("/incidents", json={"chief_complaint": "choking"})
            iid = r.json()["incident"]["incident_id"]
            r2 = c.post(f"/incidents/{iid}/dispatch-unit", json={})
            assert r2.status_code == 400

    def test_vitals_on_nonexistent_returns_404(self):
        with _client() as c:
            r = c.post(
                f"/incidents/{uuid.uuid4()}/vitals",
                json={"recorded_by": "t", "respiratory_rate": 16, "heart_rate": 72, "bp_systolic": 120, "spo2": 97},
            )
            assert r.status_code == 404

    def test_medication_on_nonexistent_returns_404(self):
        with _client() as c:
            r = c.post(
                f"/incidents/{uuid.uuid4()}/medication",
                json={"drug_name": "Adrenaline", "dose": "1mg", "route": "IV", "given_by": "t"},
            )
            assert r.status_code == 404

    def test_handoff_on_unmatched_incident(self):
        with _client() as c:
            r = c.post("/incidents", json={"chief_complaint": f"test_{uuid.uuid4().hex[:6]}"})
            iid = r.json()["incident"]["incident_id"]
            r2 = c.get(f"/incidents/{iid}/handoff")
            assert r2.status_code == 200
            assert r2.json()["priority_code"] is None

    def test_guidance_lookup_without_protocol(self):
        with _client() as c:
            r = c.post("/incidents", json={"chief_complaint": f"test_{uuid.uuid4().hex[:6]}"})
            iid = r.json()["incident"]["incident_id"]
            r2 = c.post(
                f"/incidents/{iid}/guidance-lookup",
                json={"question_id": "q1", "dispatcher_id": "t"},
            )
            assert r2.status_code == 400

    def test_list_filter_by_status(self):
        with _client() as c:
            iid, q = _create(c, "choking")
            _walk(c, iid, q)
            c.post(f"/incidents/{iid}/dispatch-unit", json={})
            r = c.get("/incidents", params={"status": "dispatched"})
            assert r.status_code == 200
            ids = [i["incident_id"] for i in r.json()["incidents"]]
            assert iid in ids

    def test_list_filter_by_priority(self):
        with _client() as c:
            iid, q = _create(c, "choking")
            out = _walk(c, iid, q)
            r = c.get("/incidents", params={"priority_code": out["priority_code"]})
            assert r.status_code == 200
            ids = [i["incident_id"] for i in r.json()["incidents"]]
            assert iid in ids

    def test_dashboard_stats(self):
        with _client() as c:
            r = c.get("/dashboard/stats", params={"window_hours": 168})
            assert r.status_code == 200
            d = r.json()
            assert "total_incidents" in d
            assert "by_status" in d

    def test_dashboard_active(self):
        with _client() as c:
            r = c.get("/dashboard/active-incidents")
            assert r.status_code == 200
            assert "incidents" in r.json()

    def test_shift_handover(self):
        from datetime import UTC, datetime, timedelta

        now = datetime.now(UTC)
        with _client() as c:
            r = c.get(
                "/dashboard/shift-handover",
                params={
                    "shift_start": (now - timedelta(hours=8)).isoformat(),
                    "shift_end": now.isoformat(),
                },
            )
            assert r.status_code == 200
            d = r.json()
            assert "total_incidents" in d
            assert "text_rendering" in d

    def test_shift_handover_bad_dates(self):
        with _client() as c:
            r = c.get(
                "/dashboard/shift-handover",
                params={"shift_start": "2025-01-02T00:00:00", "shift_end": "2025-01-01T00:00:00"},
            )
            assert r.status_code == 422

    def test_reload_protocols(self):
        with _client() as c:
            r = c.post("/admin/reload-protocols")
            assert r.status_code == 200
            d = r.json()
            assert "dispatch" in d
            assert "active" in d["dispatch"]
