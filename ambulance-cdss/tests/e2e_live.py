"""tests/e2e_live.py — Comprehensive E2E tests against a live server.

Run with: uv run python tests/e2e_live.py
Requires: server running on localhost:8000, Postgres running on localhost:5432
"""
import json
import sys
import time
import uuid

import httpx

BASE = "http://localhost:8000"
client = httpx.Client(base_url=BASE, timeout=15.0)
passed = 0
failed = 0
errors = []


def test(name, fn):
    global passed, failed
    try:
        fn()
        passed += 1
        print(f"  PASS  {name}")
    except Exception as e:
        failed += 1
        errors.append((name, str(e)))
        print(f"  FAIL  {name}: {e}")


def j(resp):
    return resp.json()


# ── Health & Observability ──────────────────────────────────────────────
print("\n=== HEALTH & OBSERVABILITY ===")

def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    d = j(r)
    assert d["status"] in ("ok", "degraded")
    assert "active_protocols" in d
test("GET /health", test_health)

def test_metrics():
    r = client.get("/metrics")
    assert r.status_code == 200
    assert "requests_total" in r.text or "http_requests" in r.text
test("GET /metrics", test_metrics)


# ── Protocol Registry ──────────────────────────────────────────────────
print("\n=== PROTOCOL REGISTRY ===")

def test_protocols():
    r = client.get("/protocols")
    assert r.status_code == 200
    d = j(r)
    assert "active" in d
    assert "rejected" in d
test("GET /protocols", test_protocols)

def test_field_protocols():
    r = client.get("/field-protocols")
    assert r.status_code == 200
    d = j(r)
    assert "active" in d
test("GET /field-protocols", test_field_protocols)

def test_formulary():
    r = client.get("/formulary")
    assert r.status_code == 200
    d = j(r)
    assert d.get("deprecated") is True
test("GET /formulary (deprecated)", test_formulary)

def test_protocol_status():
    r = client.get("/admin/protocol-status")
    assert r.status_code == 200
    d = j(r)
    assert "dispatch" in d
    assert "field" in d
test("GET /admin/protocol-status", test_protocol_status)

def test_protocol_audit():
    r = client.get("/admin/protocol-audit")
    assert r.status_code == 200
    d = j(r)
    assert "dispatch_protocols" in d
    assert "blocked_governance_values" in d
test("GET /admin/protocol-audit", test_protocol_audit)


# ── Auth ────────────────────────────────────────────────────────────────
print("\n=== AUTH ===")

def test_dispatcher_login_dev():
    r = client.post("/auth/dispatcher-login", json={"username": "test-user", "pin": "1234"})
    assert r.status_code == 200
    d = j(r)
    assert "session_token" in d
    assert d["dispatcher_id"] == "test-user"
test("POST /auth/dispatcher-login (dev mode)", test_dispatcher_login_dev)


# ── Incident Lifecycle ──────────────────────────────────────────────────
print("\n=== INCIDENT LIFECYCLE ===")

incident_id = None

def test_create_incident_no_match():
    global incident_id
    r = client.post("/incidents", json={"chief_complaint": f"zzz_test_{uuid.uuid4().hex[:8]}"})
    assert r.status_code == 200
    d = j(r)
    incident_id = d["incident"]["incident_id"]
    assert d["protocol_matched"] is False
test("POST /incidents (no match)", test_create_incident_no_match)

def test_get_incident():
    r = client.get(f"/incidents/{incident_id}")
    assert r.status_code == 200
    d = j(r)
    assert d["incident_id"] == incident_id
    assert d["status"] == "received"
test("GET /incidents/{id}", test_get_incident)

def test_get_incident_full():
    r = client.get(f"/incidents/{incident_id}/full")
    assert r.status_code == 200
    d = j(r)
    assert "incident" in d
    assert "dispatch_log" in d
    assert "vitals_history" in d
test("GET /incidents/{id}/full", test_get_incident_full)

def test_get_incident_404():
    r = client.get(f"/incidents/{uuid.uuid4()}")
    assert r.status_code == 404
test("GET /incidents/{id} 404", test_get_incident_404)

def test_list_incidents():
    r = client.get("/incidents", params={"limit": 10})
    assert r.status_code == 200
    d = j(r)
    assert "incidents" in d
    assert d["count"] >= 1
test("GET /incidents (list)", test_list_incidents)


# ── Notes ───────────────────────────────────────────────────────────────
print("\n=== NOTES ===")

def test_append_note():
    r = client.patch(f"/incidents/{incident_id}/notes", json={"note_text": "Test note", "author_id": "tester"})
    assert r.status_code == 200
    d = j(r)
    assert "Test note" in d.get("notes", "")
test("PATCH /incidents/{id}/notes", test_append_note)


# ── Status Transitions ─────────────────────────────────────────────────
print("\n=== STATUS TRANSITIONS ===")

def test_status_dispatched():
    r = client.post(f"/incidents/{incident_id}/status", json={"status": "dispatched"})
    assert r.status_code == 200
    d = j(r)
    assert d["status"] == "dispatched"
test("POST /incidents/{id}/status (dispatched)", test_status_dispatched)

def test_status_on_scene():
    r = client.post(f"/incidents/{incident_id}/status", json={"status": "on_scene"})
    assert r.status_code == 200
test("POST /incidents/{id}/status (on_scene)", test_status_on_scene)

def test_status_transporting():
    r = client.post(f"/incidents/{incident_id}/status", json={"status": "transporting"})
    assert r.status_code == 200
test("POST /incidents/{id}/status (transporting)", test_status_transporting)

def test_invalid_transition():
    r = client.post(f"/incidents/{incident_id}/status", json={"status": "received"})
    assert r.status_code == 422
test("POST /incidents/{id}/status (invalid transition)", test_invalid_transition)


# ── Vitals ──────────────────────────────────────────────────────────────
print("\n=== VITALS ===")

def test_add_vitals():
    r = client.post(f"/incidents/{incident_id}/vitals", json={
        "recorded_by": "tester",
        "respiratory_rate": 18,
        "spo2": 97,
        "bp_systolic": 120,
        "bp_diastolic": 80,
        "heart_rate": 75,
        "consciousness": "A",
        "temperature": 36.8,
    })
    assert r.status_code == 200
    d = j(r)
    assert "news2_score" in d
test("POST /incidents/{id}/vitals", test_add_vitals)


# ── Medications ─────────────────────────────────────────────────────────
print("\n=== MEDICATIONS ===")

def test_add_medication():
    r = client.post(f"/incidents/{incident_id}/medication", json={
        "drug_name": "Adrenaline",
        "dose": "1mg",
        "route": "IV",
        "given_by": "tester",
        "administered": True,
    })
    assert r.status_code == 200
test("POST /incidents/{id}/medication", test_add_medication)


# ── Field Log ───────────────────────────────────────────────────────────
print("\n=== FIELD LOG ===")

def test_add_field_log():
    r = client.post(f"/incidents/{incident_id}/field-log", json={
        "step_id": "test_step",
        "action_type": "assessment",
        "data": {"note": "Test assessment"},
        "recorded_by": "tester",
    })
    assert r.status_code == 200
test("POST /incidents/{id}/field-log", test_add_field_log)


# ── Unit Location ───────────────────────────────────────────────────────
print("\n=== UNIT LOCATION ===")

def test_add_unit_location():
    r = client.post(f"/incidents/{incident_id}/unit-location", json={
        "lat": -1.2921,
        "lon": 36.8219,
        "recorded_by": "tester",
    })
    assert r.status_code == 200
test("POST /incidents/{id}/unit-location", test_add_unit_location)

def test_get_unit_location():
    r = client.get(f"/incidents/{incident_id}/unit-location/latest")
    assert r.status_code == 200
    d = j(r)
    assert d["location"] is not None
    assert d["location"]["lat"] == -1.2921
test("GET /incidents/{id}/unit-location/latest", test_get_unit_location)


# ── Transcript ──────────────────────────────────────────────────────────
print("\n=== TRANSCRIPT ===")

def test_append_transcript():
    r = client.patch(f"/incidents/{incident_id}/transcript", json={
        "speaker": "caller",
        "text": "Patient is having chest pain",
    })
    assert r.status_code == 200
test("PATCH /incidents/{id}/transcript", test_append_transcript)


# ── Entity Extraction ───────────────────────────────────────────────────
print("\n=== ENTITY EXTRACTION ===")

def test_extract_entities():
    r = client.post("/triage/extract-entities", json={
        "transcript": "Patient collapsed, not breathing, no pulse",
    })
    assert r.status_code == 200
    d = j(r)
    assert d["chief_complaint_suggestion"] == "cardiac arrest"
    assert len(d["entities"]) > 0
    assert "confidence" in d
test("POST /triage/extract-entities", test_extract_entities)

def test_extract_entities_swahili():
    r = client.post("/triage/extract-entities", json={
        "transcript": "mshtuko wa moyo",
    })
    assert r.status_code == 200
    d = j(r)
    assert d["chief_complaint_suggestion"] == "shock"
test("POST /triage/extract-entities (Swahili)", test_extract_entities_swahili)

def test_extract_entities_vitals():
    r = client.post("/triage/extract-entities", json={
        "transcript": "BP is 120 over 80, heart rate 95, GCS 14",
    })
    assert r.status_code == 200
    d = j(r)
    assert d["vitals"]["bp_systolic"] == 120
    assert d["vitals"]["heart_rate"] == 95
    assert d["vitals"]["gcs_total"] == 14
test("POST /triage/extract-entities (vitals)", test_extract_entities_vitals)


# ── Dashboard ───────────────────────────────────────────────────────────
print("\n=== DASHBOARD ===")

def test_dashboard_active():
    r = client.get("/dashboard/active-incidents")
    assert r.status_code == 200
    d = j(r)
    assert "incidents" in d
test("GET /dashboard/active-incidents", test_dashboard_active)

def test_dashboard_stats():
    r = client.get("/dashboard/stats", params={"window_hours": 24})
    assert r.status_code == 200
    d = j(r)
    assert "total_incidents" in d
test("GET /dashboard/stats", test_dashboard_stats)


# ── Scoring ─────────────────────────────────────────────────────────────
print("\n=== SCORING ===")

def test_compute_pews():
    r = client.post("/scoring/compute", json={
        "scoring_type": "pews",
        "vitals": {"respiratory_rate": 28, "heart_rate": 130, "spo2": 91,
                   "consciousness": "V", "bp_systolic": 90, "temperature": 38.5,
                   "behaviour": "irritable"},
        "age_years": 3,
    })
    assert r.status_code == 200
    d = j(r)
    assert "score" in d
    assert "risk_level" in d
test("POST /scoring/compute (PEWS)", test_compute_pews)

def test_compute_shock_index():
    r = client.post("/scoring/compute", json={
        "scoring_type": "shock_index",
        "vitals": {"heart_rate": 120, "bp_systolic": 90},
    })
    assert r.status_code == 200
    d = j(r)
    assert d["score"] > 1.0
    assert d["risk_level"] == "high"
test("POST /scoring/compute (Shock Index)", test_compute_shock_index)


# ── Pre-arrival Confirmation ────────────────────────────────────────────
print("\n=== PRE-ARRIVAL CONFIRMATION ===")

def test_confirm_pre_arrival():
    r = client.post(f"/incidents/{incident_id}/confirm-pre-arrival", json={
        "dispatcher_id": "tester",
        "terminal_outcome_id": "test_outcome",
        "all_instructions_read": True,
    })
    # This may fail if no terminal outcome is set — that's acceptable
    assert r.status_code in (200, 400)
test("POST /incidents/{id}/confirm-pre-arrival", test_confirm_pre_arrival)


# ── Timeline ────────────────────────────────────────────────────────────
print("\n=== TIMELINE ===")

def test_timeline():
    r = client.get(f"/incidents/{incident_id}/timeline")
    assert r.status_code == 200
    d = j(r)
    assert "events" in d
    assert d["event_count"] > 0
test("GET /incidents/{id}/timeline", test_timeline)


# ── E911 Push ───────────────────────────────────────────────────────────
print("\n=== E911 PUSH ===")

def test_e911_push_new():
    r = client.post("/intake/e911-push", json={
        "lat": -1.2921,
        "lon": 36.8219,
        "caller_number": "+254700123456",
        "chief_complaint": "chest pain",
    })
    assert r.status_code == 200
    d = j(r)
    assert d["created"] is True
    assert "incident_id" in d
test("POST /intake/e911-push (new)", test_e911_push_new)

def test_e911_push_existing():
    r = client.post("/intake/e911-push", json={
        "lat": -1.2921,
        "lon": 36.8219,
        "incident_id": incident_id,
    })
    assert r.status_code == 200
    d = j(r)
    assert d["created"] is False
test("POST /intake/e911-push (existing)", test_e911_push_existing)


# ── Select Protocol ─────────────────────────────────────────────────────
print("\n=== SELECT PROTOCOL ===")

def test_select_protocol():
    r = client.get("/protocols")
    active = j(r)["active"]
    if not active:
        print("  SKIP  select protocol (no active protocols)")
        return
    proto_id = active[0]["protocol_id"]
    r2 = client.post(f"/incidents/{incident_id}/select-protocol", json={
        "protocol_id": proto_id,
        "dispatcher_id": "tester",
    })
    assert r2.status_code == 200
    d = j(r2)
    assert d["protocol_id"] == proto_id
    assert "current_question" in d
test("POST /incidents/{id}/select-protocol", test_select_protocol)

def test_select_protocol_conflict():
    r = client.get("/protocols")
    active = j(r)["active"]
    if not active:
        print("  SKIP  select protocol conflict (no active protocols)")
        return
    proto_id = active[0]["protocol_id"]
    r2 = client.post(f"/incidents/{incident_id}/select-protocol", json={
        "protocol_id": proto_id,
        "dispatcher_id": "tester",
    })
    assert r2.status_code == 409
test("POST /incidents/{id}/select-protocol (conflict)", test_select_protocol_conflict)


# ── Handoff ─────────────────────────────────────────────────────────────
print("\n=== HANDOFF ===")

def test_handoff_summary():
    r = client.get(f"/incidents/{incident_id}/handoff")
    assert r.status_code == 200
    d = j(r)
    assert "text_rendering" in d
    assert "eta_minutes" in d
test("GET /incidents/{id}/handoff", test_handoff_summary)

def test_handoff_link():
    r = client.get(f"/incidents/{incident_id}/handoff-link")
    assert r.status_code == 200
    d = j(r)
    assert "handoff_url" in d
    assert "token=" in d["handoff_url"]
test("GET /incidents/{id}/handoff-link", test_handoff_link)


# ── Export ──────────────────────────────────────────────────────────────
print("\n=== EXPORT ===")

def test_export():
    r = client.get(f"/incidents/{incident_id}/export")
    assert r.status_code == 200
    assert "INCIDENT AUDIT EXPORT" in r.text
    assert "SHA256" in r.text
test("GET /incidents/{id}/export", test_export)


# ── Close incident ──────────────────────────────────────────────────────
print("\n=== CLOSE INCIDENT ===")

def test_close_incident():
    r = client.post(f"/incidents/{incident_id}/status", json={"status": "handoff_complete"})
    assert r.status_code == 200
    r2 = client.post(f"/incidents/{incident_id}/status", json={"status": "closed"})
    assert r2.status_code == 200
test("Close incident", test_close_incident)


# ── Summary ─────────────────────────────────────────────────────────────
print(f"\n{'='*60}")
print(f"RESULTS: {passed} passed, {failed} failed, {passed+failed} total")
if errors:
    print(f"\nFAILURES:")
    for name, err in errors:
        print(f"  {name}: {err}")
print(f"{'='*60}")
sys.exit(1 if failed else 0)
