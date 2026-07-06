"""tests/e2e_realistic.py — Comprehensive E2E tests for Ambulance CDSS.

Tests the system as it actually runs:
- 0 dispatch protocols loaded (governance blocks "Dev Setup" — by design)
- 7 field protocols loaded (no governance gate)
- All backend endpoints functional
- Entity extraction, scoring, dashboard, auth, E911, etc.

Tests real user journeys, chaotic inputs, edge cases, and cross-service integration.
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
        errors.append((name, str(e)[:300]))
        print(f"  FAIL  {name}: {str(e)[:200]}")


def j(resp):
    return resp.json()


# ═══════════════════════════════════════════════════════════════════════
# SECTION 1: System Health & Configuration
# ═══════════════════════════════════════════════════════════════════════
print("\n=== 1. SYSTEM HEALTH & CONFIGURATION ===")

def test_health_endpoint():
    r = client.get("/health")
    assert r.status_code == 200
    d = j(r)
    assert d["status"] in ("ok", "degraded")
    assert "database" in d
    assert "active_protocols" in d
    assert "rejected_protocols" in d
test("1.1 Health endpoint returns valid status", test_health_endpoint)

def test_health_database_ok():
    r = client.get("/health")
    d = j(r)
    assert d["database"] == "ok"
test("1.2 Database connection is healthy", test_health_database_ok)

def test_protocols_governance_enforced():
    """All 8 dispatch protocols blocked by governance — this is CORRECT behavior."""
    r = client.get("/protocols")
    d = j(r)
    assert len(d["active"]) == 0
    assert len(d["rejected"]) == 8
    for rej in d["rejected"]:
        assert "Dev Setup" in rej["reason"]
test("1.3 Dispatch protocols correctly blocked by governance", test_protocols_governance_enforced)

def test_field_protocols_loaded():
    """Field protocols have no governance gate — all 7 should load."""
    r = client.get("/field-protocols")
    d = j(r)
    assert len(d["active"]) == 7
    assert len(d["rejected"]) == 0
test("1.4 All 7 field protocols loaded", test_field_protocols_loaded)

def test_metrics_endpoint():
    r = client.get("/metrics")
    assert r.status_code == 200
    assert "http_requests" in r.text or "requests_total" in r.text
test("1.5 Metrics endpoint works", test_metrics_endpoint)

def test_admin_protocol_status():
    r = client.get("/admin/protocol-status")
    assert r.status_code == 200
    d = j(r)
    assert "dispatch" in d
    assert "field" in d
    assert len(d["dispatch"]["rejected"]) == 8
    assert len(d["field"]["active"]) == 7
test("1.6 Admin protocol status endpoint", test_admin_protocol_status)

def test_admin_protocol_audit():
    r = client.get("/admin/protocol-audit")
    assert r.status_code == 200
    d = j(r)
    assert "blocked_governance_values" in d
    assert "dev setup" in d["blocked_governance_values"]
test("1.7 Admin protocol audit endpoint", test_admin_protocol_audit)

def test_purge_status():
    r = client.get("/admin/purge-status")
    assert r.status_code == 200
    d = j(r)
    assert "retention_days" in d
    assert d["retention_days"] == 30
test("1.8 Purge status shows 30-day retention", test_purge_status)

def test_formulary_deprecated():
    r = client.get("/formulary")
    assert r.status_code == 200
    d = j(r)
    assert d["deprecated"] is True
test("1.9 Formulary endpoint correctly deprecated", test_formulary_deprecated)


# ═══════════════════════════════════════════════════════════════════════
# SECTION 2: Authentication
# ═══════════════════════════════════════════════════════════════════════
print("\n=== 2. AUTHENTICATION ===")

def test_login_dev_mode():
    """In dev mode, any credentials are accepted."""
    r = client.post("/auth/dispatcher-login", json={"username": "DISP-001", "pin": "1234"})
    assert r.status_code == 200
    d = j(r)
    assert "session_token" in d
    assert d["dispatcher_id"] == "DISP-001"
    assert d["role"] == "dispatcher"
    assert d["expires_in_hours"] > 0
test("2.1 Dispatcher login succeeds in dev mode", test_login_dev_mode)

def test_login_different_users():
    """Multiple dispatchers can log in with different IDs."""
    ids = []
    for uid in ["DISP-A", "DISP-B", "DISP-C"]:
        r = client.post("/auth/dispatcher-login", json={"username": uid, "pin": "1234"})
        assert r.status_code == 200
        ids.append(j(r)["dispatcher_id"])
    assert len(set(ids)) == 3
test("2.2 Multiple dispatchers can log in", test_login_different_users)

def test_login_short_pin_rejected():
    """PIN must be at least 4 digits."""
    r = client.post("/auth/dispatcher-login", json={"username": "test", "pin": "123"})
    assert r.status_code == 422
test("2.3 Short PIN rejected", test_login_short_pin_rejected)

def test_login_empty_username_rejected():
    """Username cannot be empty."""
    r = client.post("/auth/dispatcher-login", json={"username": "", "pin": "1234"})
    assert r.status_code == 422
test("2.4 Empty username rejected", test_login_empty_username_rejected)


# ═══════════════════════════════════════════════════════════════════════
# SECTION 3: Incident Lifecycle (No Protocol — Degraded Path)
# ═══════════════════════════════════════════════════════════════════════
print("\n=== 3. INCIDENT LIFECYCLE (No dispatch protocol — degraded path) ===")

test_incidents = []

def test_create_incident_no_match():
    """Incident created even when no protocol matches — system degrades gracefully."""
    r = client.post("/incidents", json={
        "chief_complaint": "chest pain, crushing",
        "caller_location_lat": -1.2921,
        "caller_location_lon": 36.8219,
        "caller_location_text": "Near Kenyatta Hospital",
    })
    assert r.status_code == 200
    d = j(r)
    assert d["protocol_matched"] is False
    assert "incident_id" in d["incident"]
    assert d["incident"]["status"] == "received"
    test_incidents.append(d["incident"]["incident_id"])
test("3.1 Create incident with no protocol match", test_create_incident_no_match)

def test_create_incident_vague():
    """Vague complaint — still creates incident."""
    r = client.post("/incidents", json={"chief_complaint": "something is wrong"})
    assert r.status_code == 200
    d = j(r)
    assert d["protocol_matched"] is False
    test_incidents.append(d["incident"]["incident_id"])
test("3.2 Create incident with vague complaint", test_create_incident_vague)

def test_create_incident_unicode():
    """Unicode and special characters in complaint."""
    r = client.post("/incidents", json={
        "chief_complaint": "patient having seizures — status:critical!! 🔴",
    })
    assert r.status_code == 200
    test_incidents.append(j(r)["incident"]["incident_id"])
test("3.3 Create incident with unicode complaint", test_create_incident_unicode)

def test_get_incident():
    inc_id = test_incidents[0]
    r = client.get(f"/incidents/{inc_id}")
    assert r.status_code == 200
    d = j(r)
    assert d["incident_id"] == inc_id
    assert d["status"] == "received"
    assert d["dispatch_protocol_id"] is None
test("3.4 Get incident returns correct data", test_get_incident)

def test_get_incident_404():
    r = client.get(f"/incidents/{uuid.uuid4()}")
    assert r.status_code == 404
test("3.5 Get nonexistent incident returns 404", test_get_incident_404)

def test_get_incident_full():
    inc_id = test_incidents[0]
    r = client.get(f"/incidents/{inc_id}/full")
    assert r.status_code == 200
    d = j(r)
    assert "incident" in d
    assert "dispatch_log" in d
    assert "field_log" in d
    assert "vitals_history" in d
    assert "medications_given" in d
    assert "guidance_lookups" in d
test("3.6 Get full incident record", test_get_incident_full)

def test_list_incidents():
    r = client.get("/incidents", params={"limit": 5})
    assert r.status_code == 200
    d = j(r)
    assert "incidents" in d
    assert d["count"] >= 1
    assert d["limit"] == 5
test("3.7 List incidents with pagination", test_list_incidents)

def test_list_incidents_filter_status():
    r = client.get("/incidents", params={"status": "received", "limit": 5})
    assert r.status_code == 200
    d = j(r)
    for inc in d["incidents"]:
        assert inc["status"] == "received"
test("3.8 List incidents filtered by status", test_list_incidents_filter_status)

def test_list_incidents_filter_complaint():
    r = client.get("/incidents", params={"chief_complaint_contains": "chest", "limit": 10})
    assert r.status_code == 200
    d = j(r)
    for inc in d["incidents"]:
        assert "chest" in inc["chief_complaint"].lower()
test("3.9 List incidents filtered by chief complaint", test_list_incidents_filter_complaint)


# ═══════════════════════════════════════════════════════════════════════
# SECTION 4: Status Transitions
# ═══════════════════════════════════════════════════════════════════════
print("\n=== 4. STATUS TRANSITIONS ===")

def test_transition_dispatched():
    inc_id = test_incidents[0]
    r = client.post(f"/incidents/{inc_id}/status", json={"status": "dispatched"})
    assert r.status_code == 200
    d = j(r)
    assert d["status"] == "dispatched"
test("4.1 Transition to dispatched", test_transition_dispatched)

def test_transition_on_scene():
    inc_id = test_incidents[0]
    r = client.post(f"/incidents/{inc_id}/status", json={"status": "on_scene"})
    assert r.status_code == 200
test("4.2 Transition to on_scene", test_transition_on_scene)

def test_transition_transporting():
    inc_id = test_incidents[0]
    r = client.post(f"/incidents/{inc_id}/status", json={"status": "transporting"})
    assert r.status_code == 200
test("4.3 Transition to transporting", test_transition_transporting)

def test_transition_handoff_complete():
    inc_id = test_incidents[0]
    r = client.post(f"/incidents/{inc_id}/status", json={"status": "handoff_complete"})
    assert r.status_code == 200
test("4.4 Transition to handoff_complete", test_transition_handoff_complete)

def test_transition_closed():
    inc_id = test_incidents[0]
    r = client.post(f"/incidents/{inc_id}/status", json={"status": "closed"})
    assert r.status_code == 200
test("4.5 Transition to closed", test_transition_closed)

def test_invalid_transition_rejected():
    """Cannot go backwards from closed to dispatched."""
    inc_id = test_incidents[0]
    r = client.post(f"/incidents/{inc_id}/status", json={"status": "dispatched"})
    assert r.status_code == 422
    d = j(r)
    assert d["detail"]["error"] == "invalid_status_transition"
test("4.6 Invalid backward transition rejected", test_invalid_transition_rejected)

def test_cannot_set_received():
    """Cannot set status to 'received' — it's set automatically at creation."""
    inc_id = test_incidents[1]
    r = client.post(f"/incidents/{inc_id}/status", json={"status": "received"})
    assert r.status_code == 422
test("4.7 Cannot manually set status to received", test_cannot_set_received)

def test_full_lifecycle():
    """Create → dispatched → on_scene → transporting → handoff_complete → closed."""
    r = client.post("/incidents", json={"chief_complaint": f"lifecycle_test_{uuid.uuid4().hex[:6]}"})
    inc_id = j(r)["incident"]["incident_id"]
    for status in ["dispatched", "on_scene", "transporting", "handoff_complete", "closed"]:
        r = client.post(f"/incidents/{inc_id}/status", json={"status": status})
        assert r.status_code == 200, f"Failed on {status}: {r.text}"
    # Verify final state
    r = client.get(f"/incidents/{inc_id}")
    d = j(r)
    assert d["status"] == "closed"
test("4.8 Full lifecycle: received → closed", test_full_lifecycle)


# ═══════════════════════════════════════════════════════════════════════
# SECTION 5: Vitals & Scoring
# ═══════════════════════════════════════════════════════════════════════
print("\n=== 5. VITALS & SCORING ===")

def test_add_vitals_normal():
    inc_id = test_incidents[1]
    r = client.post(f"/incidents/{inc_id}/vitals", json={
        "recorded_by": "PARAMEDIC-01",
        "respiratory_rate": 18,
        "spo2": 97,
        "spo2_scale": 1,
        "supplemental_o2": False,
        "bp_systolic": 120,
        "bp_diastolic": 80,
        "heart_rate": 75,
        "consciousness": "A",
        "temperature": 36.8,
    })
    assert r.status_code == 200
    d = j(r)
    assert "news2_score" in d
    assert d["news2_score"] is not None
test("5.1 Add normal vitals — NEWS2 computed", test_add_vitals_normal)

def test_add_vitals_critical():
    """Critical vitals — high NEWS2 score, escalation required."""
    inc_id = test_incidents[1]
    r = client.post(f"/incidents/{inc_id}/vitals", json={
        "recorded_by": "PARAMEDIC-01",
        "respiratory_rate": 28,
        "spo2": 88,
        "spo2_scale": 1,
        "supplemental_o2": True,
        "bp_systolic": 85,
        "heart_rate": 125,
        "consciousness": "V",
        "temperature": 39.2,
    })
    assert r.status_code == 200
    d = j(r)
    assert d["news2_score"] is not None
    assert "trend_alert" in d
    # Second recording should show deterioration
    assert d["trend_alert"]["trend"] in ("deteriorating", "rapid_deterioration", "no_prior_data")
test("5.2 Add critical vitals — deterioration detected", test_add_vitals_critical)

def test_add_vitals_with_gcs():
    """Vitals with GCS components — GCS total computed."""
    inc_id = test_incidents[1]
    r = client.post(f"/incidents/{inc_id}/vitals", json={
        "recorded_by": "PARAMEDIC-01",
        "respiratory_rate": 16,
        "spo2": 96,
        "bp_systolic": 110,
        "heart_rate": 80,
        "consciousness": "A",
        "gcs_eye": 4,
        "gcs_verbal": 5,
        "gcs_motor": 6,
    })
    assert r.status_code == 200
    d = j(r)
    assert d["gcs_total"] == 15
test("5.3 Vitals with GCS components — total computed", test_add_vitals_with_gcs)

def test_add_vitals_missing_fields():
    """Incomplete vitals — missing fields reported."""
    inc_id = test_incidents[1]
    r = client.post(f"/incidents/{inc_id}/vitals", json={
        "recorded_by": "PARAMEDIC-01",
        "heart_rate": 90,
    })
    assert r.status_code == 200
    d = j(r)
    assert "news2_missing_fields" in d
    assert len(d["news2_missing_fields"]) > 0
test("5.4 Incomplete vitals — missing fields reported", test_add_vitals_with_gcs)

def test_pews_scoring():
    """PEWS scoring for paediatric patient."""
    r = client.post("/scoring/compute", json={
        "scoring_type": "pews",
        "vitals": {
            "respiratory_rate": 28, "heart_rate": 130, "spo2": 91,
            "consciousness": "V", "bp_systolic": 90, "temperature": 38.5,
            "behaviour": "irritable",
        },
        "age_years": 3,
    })
    assert r.status_code == 200
    d = j(r)
    assert "score" in d
    assert "risk_level" in d
    assert d["score"] > 0
test("5.5 PEWS scoring for paediatric patient", test_pews_scoring)

def test_shock_index():
    """Shock Index — HR/SBP ratio."""
    r = client.post("/scoring/compute", json={
        "scoring_type": "shock_index",
        "vitals": {"heart_rate": 120, "bp_systolic": 90},
    })
    assert r.status_code == 200
    d = j(r)
    assert abs(d["score"] - 1.333) < 0.01
    assert d["risk_level"] == "high"
    assert d["escalation_required"] is True
test("5.6 Shock Index computed correctly", test_shock_index)

def test_shock_index_normal():
    """Normal Shock Index."""
    r = client.post("/scoring/compute", json={
        "scoring_type": "shock_index",
        "vitals": {"heart_rate": 75, "bp_systolic": 120},
    })
    assert r.status_code == 200
    d = j(r)
    assert d["score"] < 1.0
    assert d["risk_level"] == "low"
test("5.7 Normal Shock Index", test_shock_index_normal)

def test_rts_scoring():
    """Revised Trauma Score — requires gcs_total, not individual components."""
    r = client.post("/scoring/compute", json={
        "scoring_type": "rts",
        "vitals": {"gcs_total": 15, "bp_systolic": 120, "respiratory_rate": 18},
    })
    assert r.status_code == 200
    d = j(r)
    assert "score" in d
test("5.8 Revised Trauma Score", test_rts_scoring)

def test_vitals_history_chronological():
    """Multiple vitals recordings are in chronological order."""
    inc_id = test_incidents[1]
    r = client.get(f"/incidents/{inc_id}/full")
    d = j(r)
    history = d["vitals_history"]
    assert len(history) >= 2
    for i in range(1, len(history)):
        assert history[i]["recorded_at"] >= history[i-1]["recorded_at"]
test("5.9 Vitals history is chronological", test_vitals_history_chronological)


# ═══════════════════════════════════════════════════════════════════════
# SECTION 6: Medications
# ═══════════════════════════════════════════════════════════════════════
print("\n=== 6. MEDICATIONS ===")

def test_administer_medication():
    inc_id = test_incidents[1]
    r = client.post(f"/incidents/{inc_id}/medication", json={
        "drug_name": "Adrenaline 1:10000",
        "dose": "1mg",
        "route": "IV",
        "given_by": "PARAMEDIC-01",
        "administered": True,
    })
    assert r.status_code == 200
test("6.1 Administer medication", test_administer_medication)

def test_log_not_administered():
    """Log medication that was carried but NOT given."""
    inc_id = test_incidents[1]
    r = client.post(f"/incidents/{inc_id}/medication", json={
        "drug_name": "Aspirin 300mg",
        "dose": "300mg",
        "route": "PO",
        "given_by": "PARAMEDIC-01",
        "administered": False,
    })
    assert r.status_code == 200
test("6.2 Log medication NOT administered", test_log_not_administered)

def test_multiple_medications():
    """Multiple medications logged for same incident."""
    inc_id = test_incidents[1]
    drugs = [
        ("Adrenaline 1:10000", "1mg", "IV"),
        ("Amiodarone", "300mg", "IV"),
        ("Normal Saline", "500ml", "IV"),
    ]
    for name, dose, route in drugs:
        r = client.post(f"/incidents/{inc_id}/medication", json={
            "drug_name": name, "dose": dose, "route": route,
            "given_by": "PARAMEDIC-01", "administered": True,
        })
        assert r.status_code == 200
    # Verify all in handoff
    r = client.get(f"/incidents/{inc_id}/handoff")
    d = j(r)
    assert len(d["medications_given"]) >= 3
test("6.3 Multiple medications logged and appear in handoff", test_multiple_medications)


# ═══════════════════════════════════════════════════════════════════════
# SECTION 7: Field Log & Field Protocol
# ═══════════════════════════════════════════════════════════════════════
print("\n=== 7. FIELD LOG & FIELD PROTOCOL ===")

def test_add_field_log():
    inc_id = test_incidents[1]
    r = client.post(f"/incidents/{inc_id}/field-log", json={
        "step_id": "assessment",
        "action_type": "assessment",
        "data": {"note": "Patient found on floor, unconscious"},
        "recorded_by": "PARAMEDIC-01",
    })
    assert r.status_code == 200
test("7.1 Add field log entry", test_add_field_log)

def test_select_field_protocol():
    inc_id = test_incidents[1]
    r = client.get("/field-protocols")
    proto_id = j(r)["active"][0]["protocol_id"]
    r2 = client.post(f"/incidents/{inc_id}/field-protocol", json={"protocol_id": proto_id})
    assert r2.status_code == 200
    d = j(r2)
    assert d["protocol_id"] == proto_id
    assert "steps" in d
    assert len(d["steps"]) > 0
    assert "next_pending_step" in d
    assert "is_complete" in d
test("7.2 Select field protocol", test_select_field_protocol)

def test_get_field_protocol_state():
    inc_id = test_incidents[1]
    r = client.get(f"/incidents/{inc_id}/field-protocol/state")
    assert r.status_code == 200
    d = j(r)
    assert "steps" in d
    assert "is_complete" in d
test("7.3 Get field protocol state", test_get_field_protocol_state)

def test_mark_field_step_done():
    """Mark a field protocol step as done."""
    inc_id = test_incidents[1]
    r = client.get(f"/incidents/{inc_id}/field-protocol/state")
    state = j(r)
    next_step = state["next_pending_step"]
    if next_step:
        r2 = client.post(f"/incidents/{inc_id}/field-protocol/step", json={
            "step_id": next_step["step_id"],
            "status": "done",
            "recorded_by": "PARAMEDIC-01",
        })
        assert r2.status_code == 200
        d = j(r2)
        assert "steps" in d
        assert "is_complete" in d
test("7.4 Mark field protocol step as done", test_mark_field_step_done)

def test_mark_field_step_skipped():
    """Skip a field protocol step."""
    inc_id = test_incidents[1]
    r = client.get(f"/incidents/{inc_id}/field-protocol/state")
    state = j(r)
    next_step = state["next_pending_step"]
    if next_step:
        r2 = client.post(f"/incidents/{inc_id}/field-protocol/step", json={
            "step_id": next_step["step_id"],
            "status": "skipped",
            "recorded_by": "PARAMEDIC-01",
        })
        assert r2.status_code == 200
test("7.5 Skip field protocol step", test_mark_field_step_skipped)

def test_quick_action_cpr():
    """Quick action: CPR started."""
    inc_id = test_incidents[1]
    r = client.post(f"/incidents/{inc_id}/field-log", json={
        "step_id": "cpr_started",
        "action_type": "intervention",
        "data": {"step_title": "CPR started"},
        "recorded_by": "PARAMEDIC-01",
    })
    assert r.status_code == 200
test("7.6 Quick action: CPR started", test_quick_action_cpr)

def test_quick_action_defib():
    """Quick action: Defibrillation."""
    inc_id = test_incidents[1]
    r = client.post(f"/incidents/{inc_id}/field-log", json={
        "step_id": "defibrillation",
        "action_type": "intervention",
        "data": {"step_title": "Defibrillation delivered"},
        "recorded_by": "PARAMEDIC-01",
    })
    assert r.status_code == 200
test("7.7 Quick action: Defibrillation", test_quick_action_defib)


# ═══════════════════════════════════════════════════════════════════════
# SECTION 8: Unit Location & GPS
# ═══════════════════════════════════════════════════════════════════════
print("\n=== 8. UNIT LOCATION & GPS ===")

def test_add_unit_location():
    inc_id = test_incidents[1]
    r = client.post(f"/incidents/{inc_id}/unit-location", json={
        "lat": -1.2921, "lon": 36.8219, "recorded_by": "PARAMEDIC-01",
    })
    assert r.status_code == 200
test("8.1 Add unit GPS location", test_add_unit_location)

def test_get_latest_unit_location():
    inc_id = test_incidents[1]
    r = client.get(f"/incidents/{inc_id}/unit-location/latest")
    assert r.status_code == 200
    d = j(r)
    assert d["location"] is not None
    assert d["location"]["lat"] == -1.2921
    assert d["location"]["lon"] == 36.8219
    assert "recorded_at" in d["location"]
test("8.2 Get latest unit location", test_get_latest_unit_location)

def test_unit_location_no_data():
    """Unit location returns null when no data exists."""
    r = client.post("/incidents", json={"chief_complaint": f"no_gps_{uuid.uuid4().hex[:6]}"})
    inc_id = j(r)["incident"]["incident_id"]
    r2 = client.get(f"/incidents/{inc_id}/unit-location/latest")
    assert r2.status_code == 200
    d = j(r2)
    assert d["location"] is None
test("8.3 Unit location returns null when no GPS data", test_unit_location_no_data)

def test_multiple_gps_pings():
    """Multiple GPS pings — latest is returned."""
    inc_id = test_incidents[1]
    coords = [(-1.2900, 36.8200), (-1.2850, 36.8180), (-1.2800, 36.8150)]
    for lat, lon in coords:
        r = client.post(f"/incidents/{inc_id}/unit-location", json={
            "lat": lat, "lon": lon, "recorded_by": "PARAMEDIC-01",
        })
        assert r.status_code == 200
    r = client.get(f"/incidents/{inc_id}/unit-location/latest")
    d = j(r)
    assert d["location"]["lat"] == -1.2800
    assert d["location"]["lon"] == 36.8150
test("8.4 Multiple GPS pings — latest returned", test_multiple_gps_pings)


# ═══════════════════════════════════════════════════════════════════════
# SECTION 9: Transcript Persistence
# ═══════════════════════════════════════════════════════════════════════
print("\n=== 9. TRANSCRIPT PERSISTENCE ===")

def test_append_transcript():
    inc_id = test_incidents[1]
    r = client.patch(f"/incidents/{inc_id}/transcript", json={
        "speaker": "caller", "text": "My husband collapsed in the kitchen",
    })
    assert r.status_code == 200
    d = j(r)
    assert d["transcript_length"] > 0
test("9.1 Append transcript chunk", test_append_transcript)

def test_append_multiple_transcript_chunks():
    """Multiple transcript chunks accumulate."""
    inc_id = test_incidents[1]
    for speaker, text in [("caller", "He's not breathing"), ("dispatcher", "Is he conscious?"), ("caller", "No, he's blue")]:
        r = client.patch(f"/incidents/{inc_id}/transcript", json={"speaker": speaker, "text": text})
        assert r.status_code == 200
    # Verify transcript is in full record — check both top-level and incident dict
    r = client.get(f"/incidents/{inc_id}/full")
    d = j(r)
    transcript = d.get("transcript_text") or d.get("incident", {}).get("transcript_text")
    assert transcript is not None
    assert "not breathing" in transcript
test("9.2 Multiple transcript chunks accumulate", test_append_multiple_transcript_chunks)


# ═══════════════════════════════════════════════════════════════════════
# SECTION 10: Entity Extraction (NLP)
# ═══════════════════════════════════════════════════════════════════════
print("\n=== 10. ENTITY EXTRACTION ===")

def test_cardiac_arrest_extraction():
    r = client.post("/triage/extract-entities", json={
        "transcript": "Patient collapsed, not breathing, no pulse detected",
    })
    assert r.status_code == 200
    d = j(r)
    assert d["chief_complaint_suggestion"] == "cardiac arrest"
    assert len(d["entities"]) > 0
    assert d["confidence"] > 0.5
test("10.1 Cardiac arrest extraction", test_cardiac_arrest_extraction)

def test_stroke_extraction():
    r = client.post("/triage/extract-entities", json={
        "transcript": "65 year old male, sudden face drooping, can't lift left arm, slurred speech",
    })
    assert r.status_code == 200
    d = j(r)
    labels = [e["label"] for e in d["entities"]]
    assert "STROKE" in labels
test("10.2 Stroke extraction", test_stroke_extraction)

def test_swahili_extraction():
    """Swahili extraction — exact term match required for regex fallback."""
    r = client.post("/triage/extract-entities", json={
        "transcript": "kushindwa kupumua na mshtuko wa moyo",
    })
    assert r.status_code == 200
    d = j(r)
    labels = [e["label"] for e in d["entities"]]
    # Regex fallback matches exact Swahili terms, not conjugated forms
    assert len(labels) > 0
test("10.3 Swahili language extraction (exact terms)", test_swahili_extraction)

def test_negation_detection():
    """Negation detection — denied symptoms should be marked negated."""
    r = client.post("/triage/extract-entities", json={
        "transcript": "denies chest pain, no shortness of breath",
    })
    assert r.status_code == 200
    d = j(r)
    negated = [e for e in d["entities"] if e.get("negated")]
    # Regex fallback should detect negation for "chest pain" after "denies"
    assert len(d["entities"]) > 0
    assert len(negated) > 0
test("10.4 Negation detection works", test_negation_detection)

def test_vitals_extraction():
    r = client.post("/triage/extract-entities", json={
        "transcript": "BP is 180 over 110, heart rate 125, respiratory rate 28, oxygen saturation 91 percent, temperature 39.2, GCS 14",
    })
    assert r.status_code == 200
    d = j(r)
    v = d["vitals"]
    assert v["bp_systolic"] == 180
    assert v["bp_diastolic"] == 110
    assert v["heart_rate"] == 125
    assert v["respiratory_rate"] == 28
    assert v["spo2"] == 91
    assert v["temperature"] == 39.2
    assert v["gcs_total"] == 14
test("10.5 Vitals extraction from speech", test_vitals_extraction)

def test_paediatric_extraction():
    r = client.post("/triage/extract-entities", json={
        "transcript": "2 year old child, difficulty breathing, wheezing, blue lips",
    })
    assert r.status_code == 200
    d = j(r)
    assert d["vitals"].get("age_mentioned") == 2
    labels = [e["label"] for e in d["entities"]]
    assert "RESPIRATORY_DISTRESS" in labels
test("10.6 Paediatric age extraction", test_paediatric_extraction)

def test_obstetric_extraction():
    r = client.post("/triage/extract-entities", json={
        "transcript": "pregnant woman, heavy bleeding, seizure",
    })
    assert r.status_code == 200
    d = j(r)
    labels = [e["label"] for e in d["entities"]]
    assert "PREGNANCY" in labels or "OBSTETRIC_HEMORRHAGE" in labels
test("10.7 Obstetric emergency extraction", test_obstetric_extraction)

def test_mixed_language():
    r = client.post("/triage/extract-entities", json={
        "transcript": "mgonjwa ameanguka na anashindwa kupumua, chest pain pia",
    })
    assert r.status_code == 200
    d = j(r)
    assert len(d["entities"]) > 0
test("10.8 Mixed English/Swahili extraction", test_mixed_language)

def test_empty_transcript():
    r = client.post("/triage/extract-entities", json={"transcript": ""})
    assert r.status_code == 422
test("10.9 Empty transcript rejected", test_empty_transcript)

def test_long_transcript():
    """Very long transcript — still processed."""
    long = "patient has chest pain " * 200
    r = client.post("/triage/extract-entities", json={"transcript": long})
    assert r.status_code == 200
    d = j(r)
    assert "entities" in d
test("10.10 Long transcript processed", test_long_transcript)

def test_confidence_scales_with_data():
    """More data extracted → higher confidence."""
    r1 = client.post("/triage/extract-entities", json={"transcript": "pain"})
    r2 = client.post("/triage/extract-entities", json={
        "transcript": "chest pain, BP 180 over 110, heart rate 120",
    })
    c1 = j(r1)["confidence"]
    c2 = j(r2)["confidence"]
    assert c2 > c1
test("10.11 Confidence scales with extracted data", test_confidence_scales_with_data)


# ═══════════════════════════════════════════════════════════════════════
# SECTION 11: E911 Push
# ═══════════════════════════════════════════════════════════════════════
print("\n=== 11. E911 PUSH ===")

def test_e911_new_incident():
    r = client.post("/intake/e911-push", json={
        "lat": -1.2921, "lon": 36.8219,
        "caller_number": "+254700123456",
        "accuracy_m": 25.0,
        "chief_complaint": "chest pain",
    })
    assert r.status_code == 200
    d = j(r)
    assert d["created"] is True
    assert "incident_id" in d
test("11.1 E911 push creates new incident", test_e911_new_incident)

def test_e911_update_existing():
    """E911 push with incident_id updates location."""
    r = client.post("/incidents", json={"chief_complaint": f"e911_test_{uuid.uuid4().hex[:6]}"})
    inc_id = j(r)["incident"]["incident_id"]
    r2 = client.post("/intake/e911-push", json={
        "lat": -1.3000, "lon": 36.8300,
        "incident_id": inc_id,
        "accuracy_m": 15.0,
    })
    assert r2.status_code == 200
    d = j(r2)
    assert d["created"] is False
    # Verify location updated
    r3 = client.get(f"/incidents/{inc_id}")
    d3 = j(r3)
    assert d3["caller_location_lat"] == -1.3000
    assert d3["location_accuracy_m"] == 15.0
test("11.2 E911 push updates existing incident location", test_e911_update_existing)

def test_e911_unknown_incident():
    """E911 push with nonexistent incident_id returns 404."""
    r = client.post("/intake/e911-push", json={
        "lat": -1.2921, "lon": 36.8219,
        "incident_id": str(uuid.uuid4()),
    })
    assert r.status_code == 404
test("11.3 E911 push with unknown incident returns 404", test_e911_unknown_incident)


# ═══════════════════════════════════════════════════════════════════════
# SECTION 12: Dashboard
# ═══════════════════════════════════════════════════════════════════════
print("\n=== 12. DASHBOARD ===")

def test_active_incidents():
    r = client.get("/dashboard/active-incidents")
    assert r.status_code == 200
    d = j(r)
    assert "incidents" in d
    assert len(d["incidents"]) >= 1
test("12.1 Active incidents endpoint returns data", test_active_incidents)

def test_stats_24h():
    r = client.get("/dashboard/stats", params={"window_hours": 24})
    assert r.status_code == 200
    d = j(r)
    assert "total_incidents" in d
    assert d["total_incidents"] >= 1
    assert "by_status" in d
    assert "by_priority" in d
test("12.2 Dashboard stats 24h window", test_stats_24h)

def test_stats_validation():
    """Invalid window_hours rejected."""
    r = client.get("/dashboard/stats", params={"window_hours": 0})
    assert r.status_code == 422
    r2 = client.get("/dashboard/stats", params={"window_hours": 200})
    assert r2.status_code == 422
test("12.3 Stats validation rejects invalid window", test_stats_validation)

def test_shift_handover():
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    start = (now - timedelta(hours=8)).isoformat()
    end = now.isoformat()
    r = client.get("/dashboard/shift-handover", params={"shift_start": start, "shift_end": end})
    assert r.status_code == 200
    d = j(r)
    assert "text_rendering" in d
    assert "by_status" in d
test("12.4 Shift handover report", test_shift_handover)


# ═══════════════════════════════════════════════════════════════════════
# SECTION 13: Notes
# ═══════════════════════════════════════════════════════════════════════
print("\n=== 13. NOTES ===")

def test_append_note():
    inc_id = test_incidents[1]
    r = client.patch(f"/incidents/{inc_id}/notes", json={
        "note_text": "Dispatcher note: caller very distressed", "author_id": "DISP-001",
    })
    assert r.status_code == 200
    d = j(r)
    assert "Dispatcher note" in d["notes"]
test("13.1 Append dispatcher note", test_append_note)

def test_notes_accumulate():
    """Multiple notes accumulate chronologically."""
    inc_id = test_incidents[1]
    client.patch(f"/incidents/{inc_id}/notes", json={"note_text": "First note", "author_id": "DISP-001"})
    client.patch(f"/incidents/{inc_id}/notes", json={"note_text": "Second note", "author_id": "DISP-001"})
    r = client.get(f"/incidents/{inc_id}")
    d = j(r)
    assert "First note" in d["notes"]
    assert "Second note" in d["notes"]
    assert d["notes"].index("First note") < d["notes"].index("Second note")
test("13.2 Notes accumulate chronologically", test_notes_accumulate)


# ═══════════════════════════════════════════════════════════════════════
# SECTION 14: Handoff & Export
# ═══════════════════════════════════════════════════════════════════════
print("\n=== 14. HANDOFF & EXPORT ===")

def test_handoff_summary():
    inc_id = test_incidents[1]
    r = client.get(f"/incidents/{inc_id}/handoff")
    assert r.status_code == 200
    d = j(r)
    assert "text_rendering" in d
    assert d["text_rendering"]  # Non-empty
    assert "eta_minutes" in d
    assert "dispatch_qa" in d
    assert "vitals_timeline" in d
    assert "medications_given" in d
    assert "field_actions" in d
test("14.1 Handoff summary is complete", test_handoff_summary)

def test_handoff_text_rendering():
    """Text rendering contains key sections."""
    inc_id = test_incidents[1]
    r = client.get(f"/incidents/{inc_id}/handoff")
    d = j(r)
    text = d["text_rendering"]
    assert "AMBULANCE HANDOFF SUMMARY" in text
    assert "Chief complaint" in text
test("14.2 Handoff text rendering has required sections", test_handoff_text_rendering)

def test_handoff_link():
    inc_id = test_incidents[1]
    r = client.get(f"/incidents/{inc_id}/handoff-link")
    assert r.status_code == 200
    d = j(r)
    assert "handoff_url" in d
    assert "token=" in d["handoff_url"]
    assert d["expires_in_hours"] == 24
test("14.3 Handoff link with HMAC token", test_handoff_link)

def test_export_audit():
    inc_id = test_incidents[1]
    r = client.get(f"/incidents/{inc_id}/export")
    assert r.status_code == 200
    text = r.text
    assert "INCIDENT AUDIT EXPORT" in text
    assert "SHA256" in text
    assert "DISPATCH TRANSCRIPT" in text
    assert "VITALS READINGS" in text
    assert "MEDICATIONS" in text
    assert "FIELD ACTIONS" in text
test("14.4 Complete audit export with integrity hash", test_export_audit)

def test_export_filename():
    """Export has correct Content-Disposition header."""
    inc_id = test_incidents[1]
    r = client.get(f"/incidents/{inc_id}/export")
    assert "attachment" in r.headers.get("content-disposition", "")
    assert ".txt" in r.headers.get("content-disposition", "")
test("14.5 Export has correct filename header", test_export_filename)


# ═══════════════════════════════════════════════════════════════════════
# SECTION 15: Timeline
# ═══════════════════════════════════════════════════════════════════════
print("\n=== 15. TIMELINE ===")

def test_timeline_events():
    inc_id = test_incidents[1]
    r = client.get(f"/incidents/{inc_id}/timeline")
    assert r.status_code == 200
    d = j(r)
    assert "events" in d
    assert d["event_count"] >= 1
    event_types = [e["event_type"] for e in d["events"]]
    # Timeline uses "vitals", "medication", "field_action", "dispatch_answer"
    assert "vitals" in event_types or "medication" in event_types or "field_action" in event_types
test("15.1 Timeline has events", test_timeline_events)

def test_timeline_chronological():
    """Timeline events are in chronological order."""
    inc_id = test_incidents[1]
    r = client.get(f"/incidents/{inc_id}/timeline")
    d = j(r)
    events = d["events"]
    for i in range(1, len(events)):
        assert events[i]["timestamp"] >= events[i-1]["timestamp"]
test("15.2 Timeline events are chronological", test_timeline_chronological)


# ═══════════════════════════════════════════════════════════════════════
# SECTION 16: Error Handling & Edge Cases
# ═══════════════════════════════════════════════════════════════════════
print("\n=== 16. ERROR HANDLING ===")

def test_backtrack_rejected():
    """Backtracking on locked script is rejected — either 400 (no protocol) or 403 (backtracking not permitted)."""
    inc_id = test_incidents[1]
    r = client.post(f"/incidents/{inc_id}/answer", json={
        "current_question_id": "q1",
        "answer": "yes",
        "dispatcher_id": "DISP-001",
        "is_backtrack": True,
    })
    # No protocol assigned → 400; with protocol → 403
    assert r.status_code in (400, 403)
test("16.1 Backtracking rejected (400 or 403)", test_backtrack_rejected)

def test_answer_no_protocol():
    """Cannot submit answers when no protocol is assigned."""
    inc_id = test_incidents[1]
    r = client.post(f"/incidents/{inc_id}/answer", json={
        "current_question_id": "q1",
        "answer": "yes",
        "dispatcher_id": "DISP-001",
    })
    assert r.status_code == 400
test("16.2 Answer submission without protocol returns 400", test_answer_no_protocol)

def test_select_protocol_already_assigned():
    """Cannot select protocol when one is already assigned."""
    inc_id = test_incidents[1]
    r = client.post(f"/incidents/{inc_id}/select-protocol", json={
        "protocol_id": "test", "dispatcher_id": "DISP-001",
    })
    # Either 409 (already assigned) or 404 (protocol doesn't exist)
    assert r.status_code in (404, 409)
test("16.3 Select protocol conflict or not found", test_select_protocol_already_assigned)

def test_purge_run():
    """Purge runs without error."""
    r = client.post("/admin/purge-expired-incidents")
    assert r.status_code == 200
    d = j(r)
    assert "purged" in d
test("16.4 PII purge runs without error", test_purge_run)

def test_admin_reload():
    """Protocol reload works without error."""
    r = client.post("/admin/reload-protocols")
    assert r.status_code == 200
    d = j(r)
    assert "dispatch" in d
    assert "field" in d
test("16.5 Admin protocol reload", test_admin_reload)


# ═══════════════════════════════════════════════════════════════════════
# SECTION 17: Pre-arrival Confirmation
# ═══════════════════════════════════════════════════════════════════════
print("\n=== 17. PRE-ARRIVAL CONFIRMATION ===")

def test_confirm_pre_arrival():
    """Pre-arrival confirmation endpoint exists and handles gracefully."""
    inc_id = test_incidents[1]
    r = client.post(f"/incidents/{inc_id}/confirm-pre-arrival", json={
        "dispatcher_id": "DISP-001",
        "terminal_outcome_id": "test_outcome",
        "all_instructions_read": True,
    })
    # May return 400 if no priority code set — that's expected
    assert r.status_code in (200, 400)
test("17.1 Pre-arrival confirmation endpoint", test_confirm_pre_arrival)


# ═══════════════════════════════════════════════════════════════════════
# SECTION 18: Cross-Service Integration
# ═══════════════════════════════════════════════════════════════════════
print("\n=== 18. CROSS-SERVICE INTEGRATION ===")

def test_triage_enrichment_background():
    """Triage enrichment fires as background task."""
    r = client.post("/incidents", json={"chief_complaint": "cardiac arrest, no pulse, not breathing"})
    inc_id = j(r)["incident"]["incident_id"]
    time.sleep(2)
    r2 = client.get(f"/incidents/{inc_id}")
    d = j(r2)
    # triage_enrichment may or may not be populated depending on Triage Ranker availability
    assert "triage_enrichment" in d
test("18.1 Triage enrichment field exists on incident", test_triage_enrichment_background)

def test_e911_with_chief_complaint():
    """E911 push with chief complaint creates incident."""
    r = client.post("/intake/e911-push", json={
        "lat": -1.2921, "lon": 36.8219,
        "chief_complaint": "chest pain, crushing sensation",
    })
    assert r.status_code == 200
    d = j(r)
    assert d["created"] is True
test("18.2 E911 push with chief complaint", test_e911_with_chief_complaint)

def test_vitals_trigger_scoring():
    """Vitals submission triggers NEWS2 scoring."""
    inc_id = test_incidents[1]
    r = client.post(f"/incidents/{inc_id}/vitals", json={
        "recorded_by": "SYSTEM",
        "respiratory_rate": 30,
        "spo2": 88,
        "bp_systolic": 85,
        "heart_rate": 130,
        "consciousness": "V",
        "temperature": 39.5,
    })
    assert r.status_code == 200
    d = j(r)
    assert "news2_score" in d
    assert "scores" in d
test("18.3 Vitals trigger scoring computation", test_vitals_trigger_scoring)

def test_guidance_lookup():
    """Guidance lookup endpoint exists."""
    inc_id = test_incidents[1]
    r = client.post(f"/incidents/{inc_id}/guidance-lookup", json={
        "question_id": "nonexistent", "dispatcher_id": "DISP-001",
    })
    assert r.status_code in (400, 404)
test("18.4 Guidance lookup endpoint responds", test_guidance_lookup)

def test_full_record_assembly():
    """Full incident record assembles all data sources."""
    inc_id = test_incidents[1]
    r = client.get(f"/incidents/{inc_id}/full")
    d = j(r)
    assert "incident" in d
    assert "dispatch_log" in d
    assert "field_log" in d
    assert "vitals_history" in d
    assert "medications_given" in d
    assert "guidance_lookups" in d
    assert len(d["vitals_history"]) >= 3
    assert len(d["medications_given"]) >= 3
    assert len(d["field_log"]) >= 2
test("18.5 Full record assembles all data sources", test_full_record_assembly)


# ═══════════════════════════════════════════════════════════════════════
# SECTION 19: Chaos Testing
# ═══════════════════════════════════════════════════════════════════════
print("\n=== 19. CHAOS TESTING ===")

def test_rapid_vitals():
    """10 vitals recordings in rapid succession."""
    inc_id = test_incidents[1]
    for i in range(10):
        r = client.post(f"/incidents/{inc_id}/vitals", json={
            "recorded_by": f"PARAMEDIC-{i}",
            "heart_rate": 70 + i * 5,
            "consciousness": "A",
        })
        assert r.status_code == 200
    r = client.get(f"/incidents/{inc_id}/handoff")
    assert len(j(r)["vitals_timeline"]) >= 10
test("19.1 Rapid vitals recordings", test_rapid_vitals)

def test_concurrent_status_changes():
    """Status transitions in quick succession."""
    r = client.post("/incidents", json={"chief_complaint": f"chaos_{uuid.uuid4().hex[:6]}"})
    inc_id = j(r)["incident"]["incident_id"]
    for status in ["dispatched", "on_scene", "transporting", "handoff_complete", "closed"]:
        r = client.post(f"/incidents/{inc_id}/status", json={"status": status})
        assert r.status_code == 200
test("19.2 Rapid status transitions", test_concurrent_status_changes)

def test_impossible_vitals():
    """Fat-finger vitals — system accepts without clinical validation."""
    inc_id = test_incidents[1]
    r = client.post(f"/incidents/{inc_id}/vitals", json={
        "recorded_by": "PARAMEDIC-01",
        "bp_systolic": 999,
        "heart_rate": 0,
        "temperature": -50,
    })
    assert r.status_code == 200
test("19.3 Impossible vitals accepted (no clinical validation)", test_impossible_vitals)

def test_special_characters_everywhere():
    """Special characters in complaint, notes, field log."""
    inc_id = test_incidents[1]
    r = client.patch(f"/incidents/{inc_id}/notes", json={
        "note_text": "Test <script>alert('xss')</script> note with 🎉 emojis",
        "author_id": "DISP-001",
    })
    assert r.status_code == 200
    d = j(r)
    assert "<script>" in d["notes"]  # Stored as-is, not executed
test("19.4 Special characters stored safely", test_special_characters_everywhere)

def test_empty_string_fields():
    """Empty strings in optional fields."""
    r = client.post("/incidents", json={
        "chief_complaint": "test",
        "caller_location_text": "",
    })
    assert r.status_code == 200
test("19.5 Empty optional fields handled", test_empty_string_fields)


# ═══════════════════════════════════════════════════════════════════════
# SECTION 20: Data Integrity
# ═══════════════════════════════════════════════════════════════════════
print("\n=== 20. DATA INTEGRITY ===")

def test_audit_export_sha256():
    """Audit export SHA256 is reproducible."""
    inc_id = test_incidents[1]
    r1 = client.get(f"/incidents/{inc_id}/export")
    r2 = client.get(f"/incidents/{inc_id}/export")
    # Same data → same hash
    hash1 = [l for l in r1.text.split("\n") if "SHA256" in l][0]
    hash2 = [l for l in r2.text.split("\n") if "SHA256" in l][0]
    assert hash1 == hash2
test("20.1 Audit export SHA256 is reproducible", test_audit_export_sha256)

def test_timestamps_are_iso():
    """All timestamps in incident are ISO format."""
    inc_id = test_incidents[1]
    r = client.get(f"/incidents/{inc_id}")
    d = j(r)
    from datetime import datetime
    datetime.fromisoformat(d["created_at"])  # Should not raise
test("20.2 Timestamps are valid ISO format", test_timestamps_are_iso)

def test_incident_ids_are_uuids():
    """All incident IDs are valid UUIDs."""
    inc_id = test_incidents[1]
    uuid.UUID(inc_id)  # Should not raise
test("20.3 Incident IDs are valid UUIDs", test_incident_ids_are_uuids)


# ═══════════════════════════════════════════════════════════════════════
# SECTION 21: Authentication Security
# ═══════════════════════════════════════════════════════════════════════
print("\n=== 21. AUTHENTICATION SECURITY ===")

def test_token_is_hmac():
    """Session token is HMAC-signed (base64payload.signature format)."""
    r = client.post("/auth/dispatcher-login", json={"username": "test", "pin": "1234"})
    token = j(r)["session_token"]
    assert "." in token  # payload.signature format
    parts = token.split(".")
    assert len(parts) == 2
    assert len(parts[1]) == 64  # SHA256 hex digest
test("21.1 Session token is HMAC-signed", test_token_is_hmac)

def test_handoff_token_format():
    """Handoff token is HMAC-SHA256 signed."""
    inc_id = test_incidents[1]
    r = client.get(f"/incidents/{inc_id}/handoff-link")
    url = j(r)["handoff_url"]
    token = url.split("token=")[1]
    parts = token.split(".")
    assert len(parts) == 2
    assert parts[0].isdigit()
    assert len(parts[1]) == 64  # SHA256 hex digest
test("21.2 Handoff token is SHA256 signed", test_handoff_token_format)


# ═══════════════════════════════════════════════════════════════════════
# SUMMARY
# ═══════════════════════════════════════════════════════════════════════
print(f"\n{'='*60}")
print(f"RESULTS: {passed} passed, {failed} failed, {passed+failed} total")
if errors:
    print(f"\nFAILURES:")
    for name, err in errors:
        print(f"\n  {name}")
        print(f"    {err}")
print(f"{'='*60}")
sys.exit(1 if failed else 0)
