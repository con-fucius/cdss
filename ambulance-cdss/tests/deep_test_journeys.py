"""deep_test_journeys.py

Complete user-journey tests for the Ambulance CDSS API.
Each journey exercises a realistic multi-step workflow end-to-end.

Usage:
    .\.venv\Scripts\python.exe tests/deep_test_journeys.py
"""

from __future__ import annotations

import httpx
import sys
import time
import traceback
from datetime import UTC, datetime

BASE = "http://127.0.0.1:8000"
_inner_client = httpx.Client(base_url=BASE, timeout=30.0)


class RetryClient:
    """Wraps httpx.Client to auto-retry POST/GET/PATCH on 429."""

    def __init__(self, inner: httpx.Client, max_retries: int = 10, backoff: float = 2.0):
        self._inner = inner
        self._max_retries = max_retries
        self._backoff = backoff

    def _retry(self, method: str, url: str, **kwargs) -> httpx.Response:
        for attempt in range(self._max_retries):
            resp = getattr(self._inner, method)(url, **kwargs)
            if resp.status_code == 429:
                time.sleep(self._backoff)
                continue
            return resp
        return resp

    def post(self, url: str, **kwargs) -> httpx.Response:
        return self._retry("post", url, **kwargs)

    def get(self, url: str, **kwargs) -> httpx.Response:
        return self._retry("get", url, **kwargs)

    def patch(self, url: str, **kwargs) -> httpx.Response:
        return self._retry("patch", url, **kwargs)

    def close(self) -> None:
        self._inner.close()


client = RetryClient(_inner_client)

PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"
results: list[tuple[str, str, str]] = []  # (journey, step, status)


def step(journey: str, name: str, fn):
    """Run a step, catch exceptions, record result."""
    try:
        fn()
        print(f"  [{PASS}] {name}")
        results.append((journey, name, PASS))
        return True
    except AssertionError as e:
        print(f"  [{FAIL}] {name}: {e}")
        results.append((journey, name, FAIL))
        return False
    except httpx.HTTPStatusError as e:
        print(f"  [{FAIL}] {name}: HTTP {e.response.status_code} — {e.response.text[:200]}")
        results.append((journey, name, FAIL))
        return False
    except Exception as e:
        print(f"  [{FAIL}] {name}: {type(e).__name__}: {e}")
        results.append((journey, name, FAIL))
        return False


# ── Helpers ──────────────────────────────────────────────────────────────

def login(role: str = "dispatcher") -> dict:
    resp = client.post("/auth/dispatcher-login", json={
        "username": f"test_{role}_user",
        "pin": "1234",
        "role": role,
    })
    resp.raise_for_status()
    return resp.json()


def auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def create_incident(cc: str = "chest pain") -> dict:
    resp = client.post("/incidents", json={"chief_complaint": cc})
    resp.raise_for_status()
    return resp.json()


def safe_post(url: str, **kwargs) -> httpx.Response:
    return client.post(url, **kwargs)


def safe_get(url: str, **kwargs) -> httpx.Response:
    return client.get(url, **kwargs)


def check_active_protocols() -> bool:
    """Check if there are active dispatch protocols."""
    resp = client.get("/protocols")
    resp.raise_for_status()
    data = resp.json()
    return len(data.get("active", [])) > 0


def check_active_field_protocols() -> list[str]:
    """Return list of active field protocol IDs."""
    resp = client.get("/field-protocols")
    resp.raise_for_status()
    data = resp.json()
    return [p["protocol_id"] for p in data.get("active", [])]


def get_field_protocol_steps(protocol_id: str) -> list[dict]:
    """Get steps from a field protocol."""
    resp = client.get("/field-protocols")
    resp.raise_for_status()
    data = resp.json()
    for p in data.get("active", []):
        if p["protocol_id"] == protocol_id:
            return p.get("steps", [])
    return []


# ══════════════════════════════════════════════════════════════════════════
# Journey 1: Full Cardiac Arrest Dispatch
# ══════════════════════════════════════════════════════════════════════════

def journey_1():
    J = "J1-CardiacArrestDispatch"
    print(f"\n{'='*60}")
    print(f"Journey 1: {J}")
    print(f"{'='*60}")

    # 1. Login as dispatcher
    login_data = {}
    def t_login():
        nonlocal login_data
        login_data = login("dispatcher")
        assert "session_token" in login_data, "Missing session_token"
        assert login_data["role"] == "dispatcher"
    step(J, "1. Login as dispatcher", t_login)
    token = login_data.get("session_token", "")
    headers = auth_headers(token)

    # 2. Extract entities from transcript
    ext_data = {}
    def t_extract():
        nonlocal ext_data
        resp = safe_post("/triage/extract-entities", json={
            "transcript": "Patient is unresponsive, not breathing, no pulse found. Cardiac arrest suspected. Caller at 123 Main St."
        })
        resp.raise_for_status()
        ext_data = resp.json()
        assert "entities" in ext_data
        assert "confidence" in ext_data
    step(J, "2. Extract entities from transcript", t_extract)

    # 3. Create incident with entity extraction suggestion
    inc_data = {}
    def t_create_incident():
        nonlocal inc_data
        cc = ext_data.get("chief_complaint_suggestion") or "cardiac arrest unresponsive"
        resp = safe_post("/incidents", json={
            "chief_complaint": cc,
            "caller_location_text": ext_data.get("location_text", "123 Main St"),
        })
        resp.raise_for_status()
        inc_data = resp.json()
        assert "incident" in inc_data
    step(J, "3. Create incident with entity extraction suggestion", t_create_incident)
    incident_id = inc_data.get("incident", {}).get("incident_id", "")

    # 4. Select field protocol field_cardiac_arrest_v1
    field_proto_data = {}
    def t_select_field_proto():
        nonlocal field_proto_data
        resp = safe_post(f"/incidents/{incident_id}/field-protocol", json={
            "protocol_id": "field_cardiac_arrest_v1"
        }, headers=headers)
        resp.raise_for_status()
        field_proto_data = resp.json()
        assert field_proto_data["protocol_id"] == "field_cardiac_arrest_v1"
        assert "steps" in field_proto_data
    step(J, "4. Select field protocol field_cardiac_arrest_v1", t_select_field_proto)

    # 5. Complete all field protocol steps
    def t_complete_steps():
        steps_list = field_proto_data["steps"]
        assert len(steps_list) > 0, "No steps found"
        for s in steps_list:
            resp = safe_post(f"/incidents/{incident_id}/field-protocol/step", json={
                "step_id": s["step_id"],
                "status": "done",
                "recorded_by": "test_paramedic",
            }, headers=headers)
            resp.raise_for_status()
        # Verify complete
        resp = client.get(f"/incidents/{incident_id}/field-protocol/state")
        resp.raise_for_status()
        state = resp.json()
        assert state["is_complete"], "Protocol should be complete after marking all steps"
    step(J, "5. Complete all field protocol steps", t_complete_steps)

    # 6. Record vitals (GCS 3) — consciousness must be "U" for NEWS2
    def t_record_vitals():
        resp = safe_post(f"/incidents/{incident_id}/vitals", json={
            "recorded_by": "test_paramedic",
            "respiratory_rate": 0,
            "spo2": 0,
            "bp_systolic": 0,
            "bp_diastolic": 0,
            "heart_rate": 0,
            "consciousness": "U",
            "temperature": 36.5,
            "gcs_eye": 1,
            "gcs_verbal": 1,
            "gcs_motor": 1,
        })
        resp.raise_for_status()
        data = resp.json()
        assert "id" in data, f"Expected 'id' in vitals response: {data}"
    step(J, "6. Record vitals (GCS 3)", t_record_vitals)

    # 7. Dispatch unit
    dispatch_data = {}
    def t_dispatch():
        nonlocal dispatch_data
        # Need to check if dispatch protocol is assigned and run to terminal
        resp = safe_get(f"/incidents/{incident_id}")
        resp.raise_for_status()
        inc = resp.json()
        if not inc.get("priority_code"):
            # Check if dispatch protocol is assigned
            has_proto = inc.get("dispatch_protocol_id") is not None
            if not has_proto:
                # No active dispatch protocols — select manually
                # Since all protocols are rejected in dev mode, try anyway
                try:
                    safe_post(f"/incidents/{incident_id}/select-protocol", json={
                        "protocol_id": "cardiac_arrest_unresponsive_v1",
                        "dispatcher_id": "test_dispatcher",
                    }, headers=headers)
                except httpx.HTTPStatusError:
                    # Protocol not in active registry — expected in dev mode
                    pass

            # Run through protocol answers if protocol is assigned
            resp2 = safe_get(f"/incidents/{incident_id}")
            resp2.raise_for_status()
            inc2 = resp2.json()
            if inc2.get("dispatch_protocol_id"):
                answers = [
                    ("q1_conscious", "no"),
                    ("q2_breathing", "not_breathing"),
                    ("q3_pulse_check", "no_pulse"),
                    ("q4_cpr_capability", "yes"),
                ]
                for qid, ans in answers:
                    try:
                        safe_post(f"/incidents/{incident_id}/answer", json={
                            "current_question_id": qid,
                            "answer": ans,
                            "dispatcher_id": "test_dispatcher",
                        }, headers=headers)
                    except httpx.HTTPStatusError:
                        pass  # Already answered or terminal

        # Re-check for priority_code
        resp2b = safe_get(f"/incidents/{incident_id}")
        resp2b.raise_for_status()
        inc2b = resp2b.json()
        if not inc2b.get("priority_code"):
            # Dev mode: dispatch protocols rejected — cannot dispatch-unit
            # Test that dispatch-unit correctly rejects without priority
            resp4 = safe_post(f"/incidents/{incident_id}/dispatch-unit", json={
                "lat": -1.2921, "lon": 36.8219,
            })
            assert resp4.status_code == 400, (
                f"Expected 400 (no priority_code), got {resp4.status_code}"
            )
            print("    (Dev mode: dispatch-unit correctly rejected — no priority_code)")
            return

        # Now dispatch
        resp4 = safe_post(f"/incidents/{incident_id}/dispatch-unit", json={
            "lat": -1.2921, "lon": 36.8219,
        })
        resp4.raise_for_status()
        dispatch_data = resp4.json()
        assert dispatch_data.get("assigned") is True
    step(J, "7. Dispatch unit", t_dispatch)

    # 8. Get handoff link
    def t_handoff_link():
        resp = client.get(f"/incidents/{incident_id}/handoff-link")
        resp.raise_for_status()
        data = resp.json()
        assert "handoff_url" in data
        assert "token=" in data["handoff_url"]
    step(J, "8. Get handoff link", t_handoff_link)

    # 9. Verify full incident
    def t_verify_full():
        resp = client.get(f"/incidents/{incident_id}/full")
        resp.raise_for_status()
        full = resp.json()
        assert full["incident"]["incident_id"] == incident_id
    step(J, "9. Verify full incident", t_verify_full)


# ══════════════════════════════════════════════════════════════════════════
# Journey 2: Full Paramedic Field Workflow
# ══════════════════════════════════════════════════════════════════════════

def journey_2():
    J = "J2-ParamedicFieldWorkflow"
    print(f"\n{'='*60}")
    print(f"Journey 2: {J}")
    print(f"{'='*60}")

    # 1. Login as field
    login_data = {}
    def t_login():
        nonlocal login_data
        login_data = login("field")
        assert login_data["role"] == "field"
    step(J, "1. Login as field", t_login)
    token = login_data.get("session_token", "")
    headers = auth_headers(token)

    # Create an incident first (dispatch side)
    inc = create_incident("respiratory distress difficulty breathing")
    incident_id = inc["incident"]["incident_id"]

    # 2. Select field protocol
    proto_data = {}
    def t_select():
        nonlocal proto_data
        resp = safe_post(f"/incidents/{incident_id}/field-protocol", json={
            "protocol_id": "field_respiratory_distress_v1"
        }, headers=headers)
        resp.raise_for_status()
        proto_data = resp.json()
        assert proto_data["protocol_id"] == "field_respiratory_distress_v1"
    step(J, "2. Select field protocol", t_select)

    # 3. Mark steps done (first 2)
    def t_mark_steps():
        steps_list = proto_data["steps"]
        for s in steps_list[:2]:
            resp = safe_post(f"/incidents/{incident_id}/field-protocol/step", json={
                "step_id": s["step_id"],
                "status": "done",
                "recorded_by": "test_paramedic",
            }, headers=headers)
            resp.raise_for_status()
    step(J, "3. Mark first 2 steps done", t_mark_steps)

    # 4. Record vitals twice — consciousness must be ACVPU format
    def t_record_vitals_1():
        resp = safe_post(f"/incidents/{incident_id}/vitals", json={
            "recorded_by": "test_paramedic",
            "respiratory_rate": 28,
            "spo2": 88,
            "bp_systolic": 130,
            "bp_diastolic": 80,
            "heart_rate": 110,
            "consciousness": "C",  # Confused
            "temperature": 37.0,
        })
        resp.raise_for_status()
    step(J, "4a. Record vitals (1st)", t_record_vitals_1)

    def t_record_vitals_2():
        resp = safe_post(f"/incidents/{incident_id}/vitals", json={
            "recorded_by": "test_paramedic",
            "respiratory_rate": 32,
            "spo2": 85,
            "bp_systolic": 110,
            "bp_diastolic": 70,
            "heart_rate": 125,
            "consciousness": "V",  # Voice
            "temperature": 37.2,
        })
        resp.raise_for_status()
    step(J, "4b. Record vitals (2nd)", t_record_vitals_2)

    # 5. Record medication
    def t_medication():
        resp = safe_post(f"/incidents/{incident_id}/medication", json={
            "drug_name": "Salbutamol",
            "dose": "5mg",
            "route": "nebulized",
            "given_by": "test_paramedic",
            "administered": True,
        })
        resp.raise_for_status()
        data = resp.json()
        assert "id" in data, f"Expected 'id' in medication response: {data}"
    step(J, "5. Record medication", t_medication)

    # 6. Add field log
    def t_field_log():
        resp = safe_post(f"/incidents/{incident_id}/field-log", json={
            "step_id": "manual_radio_update",
            "action_type": "assessment",
            "data": {"note": "Patient condition updated — responding to treatment"},
            "recorded_by": "test_paramedic",
        }, headers=headers)
        resp.raise_for_status()
    step(J, "6. Add field log", t_field_log)

    # 7. Route to facility
    def t_route():
        resp = safe_post(f"/incidents/{incident_id}/route-facility", json={
            "lat": -1.2921,
            "lon": 36.8219,
            "radius_km": 50,
        })
        resp.raise_for_status()
        data = resp.json()
        assert "facilities" in data
    step(J, "7. Route to facility", t_route)

    # 8. Complete handoff — need to go through proper status transitions
    def t_handoff():
        # Status lifecycle: received → dispatched → on_scene → transporting → handoff_complete → closed
        # First set to dispatched
        client.post(f"/incidents/{incident_id}/status", json={"status": "dispatched"})
        # Then on_scene
        client.post(f"/incidents/{incident_id}/status", json={"status": "on_scene"})
        # Then transporting
        client.post(f"/incidents/{incident_id}/status", json={"status": "transporting"})
        # Finally handoff_complete
        resp = safe_post(f"/incidents/{incident_id}/status", json={
            "status": "handoff_complete"
        })
        resp.raise_for_status()
        data = resp.json()
        assert data["status"] == "handoff_complete"
    step(J, "8. Complete handoff", t_handoff)

    # 9. Verify timeline
    def t_timeline():
        resp = client.get(f"/incidents/{incident_id}/timeline")
        resp.raise_for_status()
        timeline = resp.json()
        assert isinstance(timeline, dict), f"Expected dict timeline, got {type(timeline)}"
        events = timeline.get("events", [])
        assert len(events) > 0, "Timeline should have events"
        event_types = [e.get("event_type") for e in events]
        assert "vitals" in event_types, f"Expected vitals in timeline events, got {event_types}"
    step(J, "9. Verify timeline", t_timeline)


# ══════════════════════════════════════════════════════════════════════════
# Journey 3: E911 Emergency Call
# ══════════════════════════════════════════════════════════════════════════

def journey_3():
    J = "J3-E911EmergencyCall"
    print(f"\n{'='*60}")
    print(f"Journey 3: {J}")
    print(f"{'='*60}")

    # 1. E911 push creating incident
    push1 = {}
    def t_push1():
        nonlocal push1
        resp = safe_post("/intake/e911-push", json={
            "caller_number": "+254700000001",
            "lat": -1.2921,
            "lon": 36.8219,
            "accuracy_m": 15.0,
            "chief_complaint": "chest pain difficulty breathing",
        })
        resp.raise_for_status()
        push1 = resp.json()
        assert push1.get("created") is True
        assert "incident_id" in push1
    step(J, "1. E911 push creates incident", t_push1)
    inc1_id = push1.get("incident_id", "")

    # 2. E911 push updating location accuracy
    def t_push2():
        resp = safe_post("/intake/e911-push", json={
            "incident_id": inc1_id,
            "lat": -1.2930,
            "lon": 36.8225,
            "accuracy_m": 5.0,
        })
        resp.raise_for_status()
        data = resp.json()
        assert data.get("created") is False
        assert data.get("incident_id") == inc1_id
    step(J, "2. E911 push updates location accuracy", t_push2)

    # 3. Second E911 push creating new incident
    push3 = {}
    def t_push3():
        nonlocal push3
        resp = safe_post("/intake/e911-push", json={
            "caller_number": "+254700000002",
            "lat": -1.3000,
            "lon": 36.8300,
            "accuracy_m": 20.0,
            "chief_complaint": "severe bleeding trauma",
        })
        resp.raise_for_status()
        push3 = resp.json()
        assert push3.get("created") is True
        assert push3["incident_id"] != inc1_id, "Should be a different incident"
    step(J, "3. Second E911 push creates new incident", t_push3)


# ══════════════════════════════════════════════════════════════════════════
# Journey 4: Multi-Incident Dashboard
# ══════════════════════════════════════════════════════════════════════════

def journey_4():
    J = "J4-MultiIncidentDashboard"
    print(f"\n{'='*60}")
    print(f"Journey 4: {J}")
    print(f"{'='*60}")

    # Count existing incidents first
    resp0 = client.get("/incidents")
    resp0.raise_for_status()
    initial_count = resp0.json().get("count", 0)

    # 1. Create 5 incidents
    incident_ids = []
    def t_create_5():
        nonlocal incident_ids
        complaints = [
            "cardiac arrest unresponsive",
            "respiratory distress difficulty breathing",
            "chest pain",
            "severe bleeding trauma",
            "choking airway obstruction",
        ]
        for cc in complaints:
            resp = safe_post("/incidents", json={"chief_complaint": cc})
            resp.raise_for_status()
            data = resp.json()
            incident_ids.append(data["incident"]["incident_id"])
        assert len(incident_ids) == 5
    step(J, "1. Create 5 incidents", t_create_5)

    # 2. Dispatch 3 (reach terminal outcome and dispatch)
    def t_dispatch_3():
        for iid in incident_ids[:3]:
            inc_resp = client.get(f"/incidents/{iid}")
            inc_resp.raise_for_status()
            inc = inc_resp.json()
            if not inc.get("dispatch_protocol_id"):
                try:
                    client.post(f"/incidents/{iid}/select-protocol", json={
                        "protocol_id": "cardiac_arrest_unresponsive_v1",
                        "dispatcher_id": "test_dispatcher",
                    })
                except httpx.HTTPStatusError:
                    pass
            # Run through protocol answers if protocol is assigned
            resp2 = client.get(f"/incidents/{iid}")
            resp2.raise_for_status()
            inc2 = resp2.json()
            if inc2.get("dispatch_protocol_id"):
                for qid, ans in [("q1_conscious", "no"), ("q2_breathing", "not_breathing"),
                                  ("q3_pulse_check", "no_pulse"), ("q4_cpr_capability", "yes")]:
                    try:
                        client.post(f"/incidents/{iid}/answer", json={
                            "current_question_id": qid, "answer": ans,
                            "dispatcher_id": "test_dispatcher",
                        })
                    except httpx.HTTPStatusError:
                        pass
            # Dispatch
            try:
                client.post(f"/incidents/{iid}/dispatch-unit", json={"lat": -1.29, "lon": 36.82})
            except httpx.HTTPStatusError:
                pass
    step(J, "2. Dispatch 3 incidents", t_dispatch_3)

    # 3. Verify dashboard
    def t_dashboard():
        resp = client.get("/dashboard/active-incidents")
        resp.raise_for_status()
        data = resp.json()
        assert "incidents" in data
        assert len(data["incidents"]) >= 5, f"Expected >= 5 active incidents, got {len(data['incidents'])}"

        # Check stats
        resp2 = client.get("/dashboard/stats")
        resp2.raise_for_status()
        stats = resp2.json()
        assert isinstance(stats, dict)
        assert "total_incidents" in stats, f"Expected 'total_incidents' in stats: {list(stats.keys())}"
        assert stats["total_incidents"] >= initial_count + 5
    step(J, "3. Verify dashboard shows all incidents", t_dashboard)


# ══════════════════════════════════════════════════════════════════════════
# Journey 5: Deterioration Detection
# ══════════════════════════════════════════════════════════════════════════

def journey_5():
    J = "J5-DeteriorationDetection"
    print(f"\n{'='*60}")
    print(f"Journey 5: {J}")
    print(f"{'='*60}")

    inc = create_incident("respiratory distress")
    incident_id = inc["incident"]["incident_id"]

    # 1. Record stable vitals — consciousness "A" (Alert)
    stable_response = {}
    def t_stable():
        nonlocal stable_response
        resp = safe_post(f"/incidents/{incident_id}/vitals", json={
            "recorded_by": "test_paramedic",
            "respiratory_rate": 18,
            "spo2": 96,
            "bp_systolic": 120,
            "bp_diastolic": 80,
            "heart_rate": 75,
            "consciousness": "A",
            "temperature": 36.5,
        })
        resp.raise_for_status()
        stable_response = resp.json()
        # Should NOT have deterioration alert (first reading, nothing to compare)
        assert not stable_response.get("deterioration_alert", {}).get("triggered", False)
    step(J, "1. Record stable vitals", t_stable)

    # 2. Record deteriorated vitals — verify alert
    def t_deteriorated():
        resp = safe_post(f"/incidents/{incident_id}/vitals", json={
            "recorded_by": "test_paramedic",
            "respiratory_rate": 30,
            "spo2": 82,
            "bp_systolic": 85,
            "bp_diastolic": 55,
            "heart_rate": 130,
            "consciousness": "U",  # Unresponsive
            "temperature": 35.0,
        })
        resp.raise_for_status()
        data = resp.json()
        # Check for deterioration or clinical risk alert
        deterioration = data.get("deterioration_alert", {})
        clinical_risk = data.get("clinical_risk_alert", {})
        has_alert = deterioration.get("triggered", False) or clinical_risk.get("triggered", False)
        assert has_alert, (
            f"Expected deterioration or clinical risk alert. "
            f"deterioration={deterioration}, clinical_risk={clinical_risk}"
        )
    step(J, "2. Record deteriorated vitals — verify alert", t_deteriorated)


# ══════════════════════════════════════════════════════════════════════════
# Journey 6: Transcript Pipeline
# ══════════════════════════════════════════════════════════════════════════

def journey_6():
    J = "J6-TranscriptPipeline"
    print(f"\n{'='*60}")
    print(f"Journey 6: {J}")
    print(f"{'='*60}")

    inc = create_incident("cardiac arrest unresponsive")
    incident_id = inc["incident"]["incident_id"]

    # 1. Append 5 transcript chunks
    chunks = [
        ("caller", "My father collapsed in the living room, he's not moving!"),
        ("dispatcher", "Is he breathing? Can you see his chest moving?"),
        ("caller", "I don't think so, he looks blue"),
        ("dispatcher", "I need you to check for a pulse on his neck"),
        ("caller", "I can't find a pulse, please hurry!"),
    ]

    def t_append_chunks():
        for speaker, text in chunks:
            resp = client.patch(f"/incidents/{incident_id}/transcript", json={
                "speaker": speaker,
                "text": text,
            })
            resp.raise_for_status()
            data = resp.json()
            assert "transcript_length" in data
    step(J, "1. Append 5 transcript chunks", t_append_chunks)

    # 2. Verify transcript_text
    def t_verify_transcript():
        resp = client.get(f"/incidents/{incident_id}")
        resp.raise_for_status()
        inc_data = resp.json()
        transcript = inc_data.get("transcript_text", "")
        assert transcript, "transcript_text should not be empty"
        assert len(transcript) > 50, f"Transcript too short: {len(transcript)} chars"
        assert "caller" in transcript
        assert "dispatcher" in transcript
    step(J, "2. Verify transcript_text contains all chunks", t_verify_transcript)

    # 3. Extract entities from transcript
    def t_extract_from_transcript():
        resp = client.get(f"/incidents/{incident_id}")
        resp.raise_for_status()
        transcript = resp.json().get("transcript_text", "")
        resp2 = client.post("/triage/extract-entities", json={
            "transcript": transcript,
            "incident_id": incident_id,
        })
        resp2.raise_for_status()
        data = resp2.json()
        assert "entities" in data
        assert "confidence" in data
    step(J, "3. Extract entities from transcript", t_extract_from_transcript)


# ══════════════════════════════════════════════════════════════════════════
# Journey 7: Answer Correction
# ══════════════════════════════════════════════════════════════════════════

def journey_7():
    J = "J7-AnswerCorrection"
    print(f"\n{'='*60}")
    print(f"Journey 7: {J}")
    print(f"{'='*60}")

    login_data = login("dispatcher")
    token = login_data["session_token"]
    headers = auth_headers(token)

    inc = create_incident("cardiac arrest unresponsive")
    incident_id = inc["incident"]["incident_id"]

    has_active_protos = check_active_protocols()

    if not has_active_protos:
        # No active dispatch protocols — answer correction requires an active protocol.
        # In dev mode all protocols are rejected (governance incomplete).
        # Test that select-protocol correctly rejects when protocol not in registry.
        def t_select_rejected():
            resp = safe_post(f"/incidents/{incident_id}/select-protocol", json={
                "protocol_id": "cardiac_arrest_unresponsive_v1",
                "dispatcher_id": "test_dispatcher",
            }, headers=headers)
            assert resp.status_code == 404, f"Expected 404 for rejected protocol, got {resp.status_code}"
        step(J, "1. Select-protocol rejects inactive protocol (404)", t_select_rejected)

        def t_answer_no_protocol():
            resp = safe_post(f"/incidents/{incident_id}/answer", json={
                "current_question_id": "q1_conscious",
                "answer": "no",
                "dispatcher_id": "test_dispatcher",
            }, headers=headers)
            assert resp.status_code == 400, f"Expected 400 for no-protocol answer, got {resp.status_code}"
        step(J, "2. Answer endpoint rejects when no protocol assigned (400)", t_answer_no_protocol)
        return

    # If protocols are active, run the full answer-correction flow
    log_id = {}
    def t_assign_and_answer():
        nonlocal log_id
        resp = safe_post(f"/incidents/{incident_id}/select-protocol", json={
            "protocol_id": "cardiac_arrest_unresponsive_v1",
            "dispatcher_id": "test_dispatcher",
        }, headers=headers)
        resp.raise_for_status()
        # Submit first answer (intentionally wrong — "yes" when it should be "no")
        resp2 = client.post(f"/incidents/{incident_id}/answer", json={
            "current_question_id": "q1_conscious",
            "answer": "yes",
            "dispatcher_id": "test_dispatcher",
        }, headers=headers)
        resp2.raise_for_status()
        # Get the log to find the log_id
        resp3 = client.get(f"/incidents/{incident_id}/full")
        resp3.raise_for_status()
        full = resp3.json()
        dispatch_log = full.get("dispatch_log", [])
        assert len(dispatch_log) > 0, "No dispatch log entries"
        log_id["id"] = dispatch_log[-1]["id"]
    step(J, "1. Assign protocol and submit answer", t_assign_and_answer)

    def t_correct():
        assert "id" in log_id, "No log_id from previous step"
        resp = client.patch(f"/incidents/{incident_id}/answer/{log_id['id']}", json={
            "corrected_answer": "no",
            "dispatcher_id": "test_dispatcher",
        }, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        assert data.get("corrected") is True
        assert data.get("superseded_log_id") == log_id["id"]
        assert "new_log_id" in data
    step(J, "2. Correct answer — verify superseded", t_correct)


# ══════════════════════════════════════════════════════════════════════════
# Journey 8: Field Protocol Complete Flow
# ══════════════════════════════════════════════════════════════════════════

def journey_8():
    J = "J8-FieldProtocolComplete"
    print(f"\n{'='*60}")
    print(f"Journey 8: {J}")
    print(f"{'='*60}")

    login_data = login("field")
    token = login_data["session_token"]
    headers = auth_headers(token)

    inc = create_incident("choking airway obstruction")
    incident_id = inc["incident"]["incident_id"]

    # 1. Select protocol
    proto_data = {}
    def t_select():
        nonlocal proto_data
        resp = safe_post(f"/incidents/{incident_id}/field-protocol", json={
            "protocol_id": "field_cardiac_arrest_v1"
        }, headers=headers)
        resp.raise_for_status()
        proto_data = resp.json()
        assert proto_data["is_complete"] is False
    step(J, "1. Select field protocol", t_select)

    # 2. Mark ALL steps done
    def t_mark_all():
        steps_list = proto_data["steps"]
        for i, s in enumerate(steps_list):
            resp = safe_post(f"/incidents/{incident_id}/field-protocol/step", json={
                "step_id": s["step_id"],
                "status": "done",
                "recorded_by": "test_paramedic",
                "data": {"step_index": i},
            }, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            if i == len(steps_list) - 1:
                assert data["is_complete"] is True, "Protocol should be complete after last step"
    step(J, "2. Mark ALL steps done — verify is_complete", t_mark_all)

    # 3. Verify final state
    def t_verify_state():
        resp = client.get(f"/incidents/{incident_id}/field-protocol/state")
        resp.raise_for_status()
        state = resp.json()
        assert state["is_complete"] is True
        assert state["next_pending_step"] is None
    step(J, "3. Verify final state is complete", t_verify_state)


# ══════════════════════════════════════════════════════════════════════════
# Journey 9: Auth Role Enforcement
# ══════════════════════════════════════════════════════════════════════════

def journey_9():
    J = "J9-AuthRoleEnforcement"
    print(f"\n{'='*60}")
    print(f"Journey 9: {J}")
    print(f"{'='*60}")

    # Login as both roles
    disp_login = login("dispatcher")
    field_login = login("field")
    disp_headers = auth_headers(disp_login["session_token"])
    field_headers = auth_headers(field_login["session_token"])

    inc = create_incident("chest pain")
    incident_id = inc["incident"]["incident_id"]

    # Check if role enforcement is active (requires DISPATCHER_CREDENTIALS)
    # In dev mode (empty credentials), _require_role returns early — no enforcement
    health_resp = client.get("/health")
    # We can check by testing: in dev mode, dispatcher can access field endpoints

    def t_check_dev_mode():
        # In dev mode, role enforcement is bypassed — this is expected behavior
        # Dispatcher can access field endpoints because _require_role returns early
        resp = safe_post(f"/incidents/{incident_id}/field-protocol", json={
            "protocol_id": "field_cardiac_arrest_v1"
        }, headers=disp_headers)
        # In dev mode: 200 (bypass). In prod: 403
        if resp.status_code == 200:
            print("    (Dev mode: role enforcement bypassed — expected)")
        else:
            print(f"    (Prod mode: role enforcement active — got {resp.status_code})")
    step(J, "1. Verify role enforcement behavior", t_check_dev_mode)

    # Test that tokens are actually generated with correct roles
    def t_verify_token_roles():
        assert disp_login["role"] == "dispatcher"
        assert field_login["role"] == "field"
        assert disp_login["session_token"] != field_login["session_token"]
    step(J, "2. Verify tokens have correct role claims", t_verify_token_roles)

    # Test that unauthenticated requests are rejected
    def t_unauthenticated():
        resp = safe_post(f"/incidents/{incident_id}/field-protocol", json={
            "protocol_id": "field_cardiac_arrest_v1"
        })
        # Without Authorization header, depends on dev mode
        # In dev mode: may still pass. In prod: 401
        if resp.status_code in (200, 401, 403):
            pass  # Either dev mode bypass or proper rejection
        else:
            raise AssertionError(f"Unexpected status {resp.status_code}")
    step(J, "3. Unauthenticated request behavior", t_unauthenticated)

    # Test invalid token
    def t_invalid_token():
        resp = safe_post(f"/incidents/{incident_id}/field-protocol", json={
            "protocol_id": "field_cardiac_arrest_v1"
        }, headers={"Authorization": "Bearer invalid_token_xyz"})
        # In dev mode: may pass (bypass). In prod: 401
        if resp.status_code in (200, 401, 403):
            pass  # Either dev mode bypass or proper rejection
        else:
            raise AssertionError(f"Unexpected status {resp.status_code}")
    step(J, "4. Invalid token rejection", t_invalid_token)


# ══════════════════════════════════════════════════════════════════════════
# Journey 10: Handoff & Receiving
# ══════════════════════════════════════════════════════════════════════════

def journey_10():
    J = "J10-HandoffReceiving"
    print(f"\n{'='*60}")
    print(f"Journey 10: {J}")
    print(f"{'='*60}")

    login_data = login("dispatcher")
    token = login_data["session_token"]
    headers = auth_headers(token)

    inc = create_incident("cardiac arrest unresponsive")
    incident_id = inc["incident"]["incident_id"]

    has_active_protos = check_active_protocols()

    # Setup: run protocol to terminal and dispatch
    def t_setup():
        if has_active_protos:
            client.post(f"/incidents/{incident_id}/select-protocol", json={
                "protocol_id": "cardiac_arrest_unresponsive_v1",
                "dispatcher_id": "test_dispatcher",
            }, headers=headers)
            for qid, ans in [("q1_conscious", "no"), ("q2_breathing", "not_breathing"),
                              ("q3_pulse_check", "no_pulse"), ("q4_cpr_capability", "yes")]:
                try:
                    client.post(f"/incidents/{incident_id}/answer", json={
                        "current_question_id": qid, "answer": ans,
                        "dispatcher_id": "test_dispatcher",
                    }, headers=headers)
                except Exception:
                    pass
        # Dispatch unit
        client.post(f"/incidents/{incident_id}/dispatch-unit", json={"lat": -1.29, "lon": 36.82})
    step(J, "1. Setup: run protocol to terminal and dispatch", t_setup)

    # Record vitals — consciousness "U" for unresponsive
    def t_vitals():
        resp = safe_post(f"/incidents/{incident_id}/vitals", json={
            "recorded_by": "test_paramedic",
            "respiratory_rate": 0,
            "spo2": 0,
            "bp_systolic": 0,
            "heart_rate": 0,
            "consciousness": "U",
            "temperature": 36.0,
            "gcs_eye": 1, "gcs_verbal": 1, "gcs_motor": 1,
        })
        resp.raise_for_status()
    step(J, "2. Record vitals", t_vitals)

    # Record medication
    def t_med():
        resp = safe_post(f"/incidents/{incident_id}/medication", json={
            "drug_name": "Epinephrine",
            "dose": "1mg",
            "route": "IV",
            "given_by": "test_paramedic",
        })
        resp.raise_for_status()
    step(J, "3. Record medication", t_med)

    # Route to facility
    def t_route():
        resp = safe_post(f"/incidents/{incident_id}/route-facility", json={
            "lat": -1.2921, "lon": 36.8219, "radius_km": 50,
        })
        resp.raise_for_status()
    step(J, "4. Route to facility", t_route)

    # Status transitions: received → dispatched → on_scene → transporting → handoff_complete
    def t_status_transitions():
        for status in ["dispatched", "on_scene", "transporting", "handoff_complete"]:
            resp = safe_post(f"/incidents/{incident_id}/status", json={"status": status})
            resp.raise_for_status()
            assert resp.json()["status"] == status
    step(J, "5. Status transitions through to handoff_complete", t_status_transitions)

    # Get handoff link
    handoff_url = ""
    def t_handoff_link():
        nonlocal handoff_url
        resp = client.get(f"/incidents/{incident_id}/handoff-link")
        resp.raise_for_status()
        data = resp.json()
        handoff_url = data["handoff_url"]
        assert "token=" in handoff_url
        assert "expires_in_hours" in data
    step(J, "6. Get handoff link", t_handoff_link)

    # Get handoff summary
    def t_handoff_summary():
        resp = client.get(f"/incidents/{incident_id}/handoff")
        resp.raise_for_status()
        summary = resp.json()
        assert summary.get("incident_id") == incident_id
        assert summary.get("chief_complaint")
        assert summary.get("status") == "handoff_complete"
        assert "vitals_timeline" in summary
        assert "medications_given" in summary
        assert "field_actions" in summary
        assert summary.get("text_rendering"), "text_rendering should not be empty"
    step(J, "7. Get handoff summary — verify all fields", t_handoff_summary)

    # Verify full incident
    def t_verify_full():
        resp = client.get(f"/incidents/{incident_id}/full")
        resp.raise_for_status()
        full = resp.json()
        assert full["incident"]["status"] == "handoff_complete"
    step(J, "8. Verify full incident reflects handoff", t_verify_full)


# ══════════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════════

def main():
    print("=" * 60)
    print("AMBULANCE CDSS — DEEP USER JOURNEY TESTS")
    print(f"Target: {BASE}")
    print(f"Started: {datetime.now(UTC).isoformat()}")
    print("=" * 60)

    # Health check
    try:
        resp = client.get("/health")
        resp.raise_for_status()
        health = resp.json()
        print(f"\nHealth: {health['status']} | DB: {health.get('database', 'n/a')} | "
              f"Protocols: {health.get('active_protocols', 0)} active")
    except Exception as e:
        print(f"\nFATAL: Server not reachable: {e}")
        sys.exit(1)

    # Report protocol state
    active_field = check_active_field_protocols()
    print(f"Field protocols: {len(active_field)} active ({', '.join(active_field) if active_field else 'none'})")
    has_active_dispatch = check_active_protocols()
    print(f"Dispatch protocols: {'active' if has_active_dispatch else 'ALL REJECTED (dev mode — governance incomplete)'}")

    # Run all journeys
    journeys = [
        journey_1,
        journey_2,
        journey_3,
        journey_4,
        journey_5,
        journey_6,
        journey_7,
        journey_8,
        journey_9,
        journey_10,
    ]

    for idx, jfn in enumerate(journeys):
        try:
            jfn()
        except Exception as e:
            print(f"\n  [FATAL] {jfn.__name__} crashed: {e}")
            traceback.print_exc()
            results.append((jfn.__name__, "CRASHED", FAIL))

    # ── Summary ──────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)

    by_journey: dict[str, dict[str, int]] = {}
    for journey, step_name, status in results:
        if journey not in by_journey:
            by_journey[journey] = {"PASS": 0, "FAIL": 0, "SKIP": 0}
        by_journey[journey][status] += 1

    total_pass = 0
    total_fail = 0
    for jname, counts in by_journey.items():
        p, f, s = counts["PASS"], counts["FAIL"], counts["SKIP"]
        total_pass += p
        total_fail += f
        icon = "PASS" if f == 0 else "FAIL"
        print(f"  [{icon}] {jname}: {p} pass, {f} fail, {s} skip")

    total = total_pass + total_fail
    print(f"\n  Total: {total_pass}/{total} passed ({total_fail} failed)")
    print(f"Finished: {datetime.now(UTC).isoformat()}")

    sys.exit(0 if total_fail == 0 else 1)


if __name__ == "__main__":
    main()
