"""
app/main.py

FastAPI app entrypoint.

Scope: app shell, health check, metrics, protocol registry introspection,
the full incident lifecycle (create incident, get entry question, submit
answers, reach terminal outcome, log vitals/field actions, retrieve
assembled incident), Mode 2 guidance lookup (bounded, governed, logged
separately from the dispatch transcript — see docs/GOVERNANCE.md), unit/
facility routing against the two external services (degrading explicitly
to a manual-action message rather than failing silently when those
services are unconfigured or unreachable), the field-side protocol
runner (Phase 4 — checklist selection, step marking, state reconstruction
from incident_field_log; deliberately not governance-gated the way Mode 1
is — see app/protocols/field_runner.py), Phase 5 handoff summary
(GET /incidents/{id}/handoff — deterministic, no LLM, sourced entirely
from get_incident_full), and Phase 6 dashboards (GET /dashboard/*).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Security
from fastapi.security.api_key import APIKeyHeader
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from starlette.responses import HTMLResponse, PlainTextResponse
from starlette.staticfiles import StaticFiles

from .config import (
    get_admin_api_key,
    get_allowed_origins,
    get_answer_correction_window_seconds,
    get_prehospital_formulary,
    get_rate_limit_chat_per_minute,
    get_rate_limit_default_per_minute,
    is_database_configured,
    is_formulary_configured,
    validate_startup_config,
)
from .external.triage_ranker import TriageRankerClient
from .db import check_database, close_engine, init_engine
from .external.emergency_dispatch import EmergencyDispatchClient
from .external.facility_registry import FacilityRegistryClient
from .observability import MetricsMiddleware, RateLimitMiddleware, metrics_text
from .protocols.field_registry import field_registry
from .protocols.field_runner import FieldRunState, rebuild_from_field_log
from .protocols.registry import registry
from .protocols.runner import (
    OutOfScriptAnswerError,
    can_backtrack,
    get_entry_question,
    submit_answer,
)
from .handoff import build_handoff_summary, render_audit_text
from .handoff_link import generate_handoff_token, verify_handoff_token
from . import repositories as repo
from .models import IncidentStatus
from .repositories import InvalidStatusTransitionError

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── Admin API key dependency ───────────────────────────────────────────────

_admin_key_header = APIKeyHeader(name="X-Admin-Key", auto_error=False)


async def _require_admin_key(key: str | None = Security(_admin_key_header)) -> None:
    """
    Validates the X-Admin-Key header for admin endpoints.
    If ADMIN_API_KEY is not configured (empty string), the check is
    bypassed — this is the development default. In production,
    validate_startup_config() blocks startup if ADMIN_API_KEY is unset.
    """
    configured = get_admin_api_key()
    if not configured:
        # Development mode — no key configured, allow all requests.
        return
    if not key or key != configured:
        raise HTTPException(
            status_code=403,
            detail={"error": "forbidden", "message": "Valid X-Admin-Key header required."},
        )


_facility_client = FacilityRegistryClient()
_dispatch_client = EmergencyDispatchClient()
_triage_client = TriageRankerClient()


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_startup_config()
    await init_engine()
    registry.load_all()
    rejected = registry.list_rejected()
    if rejected:
        logger.warning(
            "%d protocol file(s) rejected at startup — see GET /protocols. "
            "Rejected protocols are NOT loaded and are NOT selectable.",
            len(rejected),
        )
    field_registry.load_all()
    field_rejected = field_registry.list_rejected()
    if field_rejected:
        logger.warning(
            "%d field protocol file(s) rejected at startup — see GET "
            "/field-protocols. Rejected field protocols are NOT loaded.",
            len(field_rejected),
        )
    logger.info(
        "Ambulance CDSS started. Active dispatch protocols: %d, active field "
        "protocols: %d",
        len(registry.list_active()),
        len(field_registry.list_active()),
    )
    yield
    await close_engine()


app = FastAPI(title="Ambulance CDSS", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_allowed_origins(),
    allow_methods=["GET", "POST", "PATCH"],
    allow_headers=["Content-Type", "X-Admin-Key"],
)
app.add_middleware(
    RateLimitMiddleware,
    limited_paths={
        "/incidents": get_rate_limit_chat_per_minute(),
        "": get_rate_limit_default_per_minute(),
    },
)
app.add_middleware(MetricsMiddleware)


# ─────────────────────────────────────────────────────────────────────────────
# Health & observability
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    if not is_database_configured():
        return {
            "status": "degraded",
            "database": "not_configured",
            "active_protocols": len(registry.list_active()),
            "rejected_protocols": len(registry.list_rejected()),
            "backtracking_permitted": can_backtrack(),
        }
    db_ok = await check_database()
    active_protocols = registry.list_active()
    return {
        "status": "ok" if db_ok and active_protocols else "degraded",
        "database": "ok" if db_ok else "error",
        "active_protocols": len(active_protocols),
        "rejected_protocols": len(registry.list_rejected()),
        "backtracking_permitted": can_backtrack(),
    }


@app.get("/metrics", response_class=PlainTextResponse)
async def metrics():
    return metrics_text()


# ─────────────────────────────────────────────────────────────────────────────
# Protocol registry introspection
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/protocols")
async def list_protocols():
    return {"active": registry.list_active(), "rejected": registry.list_rejected()}


@app.get("/field-protocols")
async def list_field_protocols():
    return {
        "active": field_registry.list_active(),
        "rejected": field_registry.list_rejected(),
    }


@app.get("/formulary")
async def get_formulary():
    """
    DEPRECATED — Phase 0.5 was resolved as unconditional logging with no
    formulary gate. POST /incidents/{id}/medication no longer rejects
    drug names against this list. Retained only so any client still
    polling this endpoint gets a clear deprecated response instead of a
    404, and can stop treating its result as a constraint on what may be
    logged.
    """
    return {
        "deprecated": True,
        "message": (
            "Medication logging is unconditional as of Phase 0.5 — there "
            "is no formulary allowlist. This endpoint no longer gates "
            "POST /incidents/{id}/medication."
        ),
        "configured": is_formulary_configured(),
        "drugs": get_prehospital_formulary(),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Incident lifecycle — request/response models
# ─────────────────────────────────────────────────────────────────────────────

class CreateIncidentRequest(BaseModel):
    chief_complaint: str = Field(min_length=1)
    caller_location_lat: float | None = None
    caller_location_lon: float | None = None
    caller_location_text: str | None = None


class SubmitAnswerRequest(BaseModel):
    current_question_id: str
    answer: str
    dispatcher_id: str = Field(min_length=1)
    # Caller declares intent to backtrack. The server is the sole authority
    # on whether this is permitted — see can_backtrack() in
    # app/protocols/runner.py and docs/GOVERNANCE.md. Resolved: disallowed
    # on locked (Mode 1) dispatch scripts; the 403 path in
    # submit_incident_answer() below is therefore always taken when this
    # is True. Retained as an explicit field (rather than always-rejected
    # silently) so the UI can surface a clear error to the dispatcher
    # rather than having the submission appear to succeed.
    is_backtrack: bool = False


class AddVitalsRequest(BaseModel):
    recorded_by: str = Field(min_length=1)
    respiratory_rate: int | None = None
    spo2: int | None = None
    spo2_scale: int | None = None
    supplemental_o2: bool | None = None
    bp_systolic: int | None = None
    bp_diastolic: int | None = None
    heart_rate: int | None = None
    consciousness: str | None = None
    temperature: float | None = None
    gcs_eye: int | None = None
    gcs_verbal: int | None = None
    gcs_motor: int | None = None


class AddFieldLogRequest(BaseModel):
    step_id: str = Field(min_length=1)
    action_type: str = Field(min_length=1)
    data: dict = Field(default_factory=dict)
    recorded_by: str = Field(min_length=1)


class AddMedicationRequest(BaseModel):
    drug_name: str = Field(min_length=1)
    dose: str = Field(min_length=1)
    route: str = Field(min_length=1)
    given_by: str = Field(min_length=1)
    # Resolved per Phase 0.5: logging is unconditional and does not
    # depend on the item being administered. This flag records whether
    # it actually was, rather than gating whether the row gets written.
    administered: bool = True


class GuidanceLookupRequest(BaseModel):
    question_id: str = Field(min_length=1)
    dispatcher_id: str = Field(min_length=1)


class DispatchUnitRequest(BaseModel):
    lat: float | None = None
    lon: float | None = None


class RouteFacilityRequest(BaseModel):
    lat: float
    lon: float
    required_services: list[str] | None = None
    radius_km: float = 50.0


class ReportAdmissionRequest(BaseModel):
    facility_id: str = Field(min_length=1)
    summary: str = Field(min_length=1)


class SelectFieldProtocolRequest(BaseModel):
    protocol_id: str = Field(min_length=1)


class MarkFieldStepRequest(BaseModel):
    step_id: str = Field(min_length=1)
    status: str = Field(min_length=1)  # done | skipped | not_applicable
    recorded_by: str = Field(min_length=1)
    data: dict = Field(default_factory=dict)


class UpdateIncidentStatusRequest(BaseModel):
    status: str = Field(min_length=1)
    # Optional timestamps — caller supplies these from device clock when the
    # event occurred, rather than trusting server-receive time for accuracy.
    # Omitting them means the server timestamp for the status-update call
    # is used instead via default_factory=_now inside the endpoint.
    event_timestamp: datetime | None = None


class AppendNoteRequest(BaseModel):
    note_text: str = Field(min_length=1)
    author_id: str = Field(min_length=1)


class ConfirmPreArrivalRequest(BaseModel):
    dispatcher_id: str = Field(min_length=1)
    terminal_outcome_id: str = Field(min_length=1)
    all_instructions_read: bool = True


class CorrectAnswerRequest(BaseModel):
    corrected_answer: str = Field(min_length=1)
    dispatcher_id: str = Field(min_length=1)


class FromCaptureRequest(BaseModel):
    """Phase 4.1 — accepts CapturePayload from shared contracts.
    Maps structured fields to create_incident internally.
    """
    dispatchId: str = Field(min_length=1)
    patientInfo: dict = Field(default_factory=dict)
    incidentInfo: dict = Field(default_factory=dict)
    metadata: dict = Field(default_factory=dict)


class UnitLocationRequest(BaseModel):
    lat: float
    lon: float
    recorded_by: str = Field(min_length=1)
    timestamp: datetime | None = None


# ─────────────────────────────────────────────────────────────────────────────
# Phase 4 — enriched incident creation (POST /incidents/from-capture)
# ─────────────────────────────────────────────────────────────────────────────

# Consciousness-to-GCS mapping (approximation, documented with limitations).
# These are not clinical assessments — they are dispatch-caller approximations.
_CONSCIOUSNESS_GCS_MAP = {
    "unconscious": 3,
    "responds to pain": 7,
    "pain": 7,
    "responds to voice": 9,
    "voice": 9,
    "confused": 13,
    "alert": 15,
}


@app.post("/incidents/from-capture")
async def create_incident_from_capture(request: FromCaptureRequest):
    """
    Phase 4.1 — accepts a structured CapturePayload from the dispatcher
    UI or web listener, maps its fields to create_incident internals,
    and delegates to the same create_incident logic. No duplicate code.

    Returns the standard create_incident response plus capture_correlation_id
    echoing the dispatchId for event-log correlation.
    """
    incident_info = request.incidentInfo or {}
    patient_info = request.patientInfo or {}

    # Map structured payload to create_incident parameters
    chief_complaint = (
        incident_info.get("description", "")
        or request.dispatchId
    )

    # Extract location from incidentInfo.location
    location = incident_info.get("location") or {}
    caller_lat = None
    caller_lon = None
    caller_text = None
    if isinstance(location, dict):
        caller_text = location.get("address")
    elif isinstance(location, str):
        caller_text = location

    # Create incident using the same logic as POST /incidents
    incident = await repo.create_incident(
        chief_complaint=chief_complaint,
        caller_location_text=caller_text,
    )

    # Match protocol
    match = registry.match_by_chief_complaint(chief_complaint)

    # Build triage enrichment hints from CapturePayload vitals
    gcs_score = None
    acvpu = patient_info.get("consciousness")
    if acvpu:
        gcs_score = _CONSCIOUSNESS_GCS_MAP.get(acvpu.lower().strip())

    # Fire triage enrichment as background task with CapturePayload-derived hints
    incident_id = incident["incident_id"]

    async def _run_triage_enrichment_capture() -> None:
        try:
            enrichment = await _triage_client.enrich(
                incident_desc=chief_complaint,
                gcs_score=gcs_score,
                acvpu=acvpu,
            )
            if enrichment is not None:
                enrichment_dict = {
                    "triage_level": enrichment.triage_level,
                    "esi_level": enrichment.esi_level,
                    "top_diagnosis": enrichment.top_diagnosis,
                    "icd10_code": enrichment.icd10_code,
                    "snomed_code": enrichment.snomed_code,
                    "shock_index": enrichment.shock_index,
                    "degraded_mode": enrichment.degraded_mode,
                }
                await repo.set_triage_enrichment(incident_id, enrichment_dict)
        except Exception as exc:
            logger.warning("Triage enrichment (capture) failed for %s: %s", incident_id, exc)

    task = asyncio.create_task(_run_triage_enrichment_capture())
    task.add_done_callback(
        lambda t: logger.error(
            "Triage enrichment (capture) task exception: %s", t.exception()
        ) if t.exception() else None
    )

    if match is None:
        return {
            "incident": incident,
            "protocol_matched": False,
            "capture_correlation_id": request.dispatchId,
            "message": "No locked protocol matches this chief complaint. "
            "Manual protocol selection required.",
        }

    protocol = match.protocol
    snapshot = {
        "protocol_id": protocol.protocol_id,
        "version": protocol.version,
        "approved_by": protocol.approved_by,
        "approved_date": protocol.approved_date,
    }
    await repo.set_dispatch_protocol(
        incident["incident_id"], protocol.protocol_id, protocol.version, snapshot
    )

    entry_question = get_entry_question(protocol)
    requires_manual_verification = match.confidence < 1.0 or len(match.alternatives) > 0
    return {
        "incident": incident,
        "protocol_matched": True,
        "protocol_id": protocol.protocol_id,
        "protocol_version": protocol.version,
        "current_question": _question_to_dict(entry_question),
        "confidence": match.confidence,
        "matched_triggers": match.matched_triggers,
        "requires_manual_verification": requires_manual_verification,
        "capture_correlation_id": request.dispatchId,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Incident lifecycle — endpoints
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/incidents")
async def create_incident(request: CreateIncidentRequest):
    """
    Create an incident and, if a matching locked protocol is found by
    chief complaint, snapshot it onto the incident and return the entry
    question. If no protocol matches, the incident is still created
    (dispatcher must reassess / select manually) — this mirrors the
    "outcome_route_to_other_protocol" terminal pattern used inside
    protocols themselves: never silently guess, always surface clearly.
    """
    incident = await repo.create_incident(
        chief_complaint=request.chief_complaint,
        caller_location_lat=request.caller_location_lat,
        caller_location_lon=request.caller_location_lon,
        caller_location_text=request.caller_location_text,
    )

    match = registry.match_by_chief_complaint(request.chief_complaint)
    if match is None:
        return {
            "incident": incident,
            "protocol_matched": False,
            "message": "No locked protocol matches this chief complaint. "
            "Manual protocol selection required.",
        }

    protocol = match.protocol
    snapshot = {
        "protocol_id": protocol.protocol_id,
        "version": protocol.version,
        "approved_by": protocol.approved_by,
        "approved_date": protocol.approved_date,
    }
    await repo.set_dispatch_protocol(
        incident["incident_id"], protocol.protocol_id, protocol.version, snapshot
    )

    entry_question = get_entry_question(protocol)
    alternatives = [
        {
            "protocol_id": alt.protocol.protocol_id,
            "confidence": alt.confidence,
            "matched_triggers": alt.matched_triggers,
        }
        for alt in match.alternatives
    ]
    requires_manual_verification = match.confidence < 1.0 or len(alternatives) > 0
    resp = {
        "incident": incident,
        "protocol_matched": True,
        "protocol_id": protocol.protocol_id,
        "protocol_version": protocol.version,
        "current_question": _question_to_dict(entry_question),
        "confidence": match.confidence,
        "matched_triggers": match.matched_triggers,
        "alternatives": alternatives,
        "requires_manual_verification": requires_manual_verification,
    }

    # Phase 2.9 — fire triage enrichment as background task.
    # Incident endpoint returns immediately; enrichment resolves later.
    incident_id = incident["incident_id"]

    async def _run_triage_enrichment() -> None:
        """Background task: call Triage Ranker, write result to incident."""
        try:
            enrichment = await _triage_client.enrich(
                incident_desc=request.chief_complaint,
            )
            if enrichment is not None:
                enrichment_dict = {
                    "triage_level": enrichment.triage_level,
                    "esi_level": enrichment.esi_level,
                    "top_diagnosis": enrichment.top_diagnosis,
                    "icd10_code": enrichment.icd10_code,
                    "snomed_code": enrichment.snomed_code,
                    "shock_index": enrichment.shock_index,
                    "degraded_mode": enrichment.degraded_mode,
                }
                await repo.set_triage_enrichment(incident_id, enrichment_dict)
        except Exception as exc:
            # Never let background task failure propagate to caller
            logger.warning("Triage enrichment failed for %s: %s", incident_id, exc)

    task = asyncio.create_task(_run_triage_enrichment())
    task.add_done_callback(
        lambda t: logger.error(
            "Triage enrichment task exception: %s", t.exception()
        ) if t.exception() else None
    )

    return resp


@app.post("/incidents/{incident_id}/answer")
async def submit_incident_answer(incident_id: str, request: SubmitAnswerRequest):
    """
    Submit an answer to the current locked-script question.

    On OutOfScriptAnswerError: returns 422 with the valid answer set —
    this is the loud, immediate, fully-logged rejection described in
    app/protocols/runner.py. It is not caught and defaulted anywhere in
    this call chain.
    """
    incident = await repo.get_incident(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    if not incident["dispatch_protocol_id"]:
        raise HTTPException(
            status_code=400,
            detail="Incident has no protocol assigned. Cannot submit answers.",
        )

    protocol = registry.get(incident["dispatch_protocol_id"])
    if protocol is None:
        raise HTTPException(
            status_code=500,
            detail=(
                f"Protocol {incident['dispatch_protocol_id']!r} referenced by "
                "this incident is not in the active registry. This should not "
                "happen for an in-progress incident — investigate immediately."
            ),
        )

    # Improvement 3.2 — version mismatch detection: compare the live
    # protocol version against the snapshot taken at incident creation.
    version_mismatch_warning = None
    snapshot = incident.get("dispatch_protocol_snapshot") or {}
    snapshot_version = snapshot.get("version")
    if snapshot_version and snapshot_version != protocol.version:
        version_mismatch_warning = {
            "version_mismatch": True,
            "snapshot_version": snapshot_version,
            "live_version": protocol.version,
        }
        logger.warning(
            "Protocol version mismatch: incident %s started on v%s but live "
            "registry is v%s",
            incident_id,
            snapshot_version,
            protocol.version,
        )

    question = protocol.questions.get(request.current_question_id)
    if question is None:
        raise HTTPException(
            status_code=404,
            detail=f"Question {request.current_question_id!r} does not exist in this protocol.",
        )

    if request.is_backtrack and not can_backtrack():
        # Loud rejection per docs/GOVERNANCE.md — backtracking policy is an
        # open decision and currently always denied. This must never be
        # silently downgraded to a normal forward answer; the caller asked
        # for something the system is not authorised to do.
        raise HTTPException(
            status_code=403,
            detail={
                "error": "backtracking_not_permitted",
                "message": (
                    "Backtracking on a live locked-script call is not "
                    "currently permitted. See docs/GOVERNANCE.md."
                ),
            },
        )

    try:
        result = submit_answer(protocol, request.current_question_id, request.answer)
    except OutOfScriptAnswerError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "out_of_script_answer",
                "message": str(exc),
                "valid_answers": exc.valid_answers,
            },
        ) from exc

    await repo.append_dispatch_answer(
        incident_id=incident_id,
        question_id=request.current_question_id,
        question_text=question.text,
        answer=request.answer,
        protocol_version=protocol.version,
        is_backtrack=request.is_backtrack,
    )

    if result.terminal_outcome is not None:
        await repo.set_dispatch_outcome(
            incident_id,
            priority_code=result.terminal_outcome.priority_code,
            recommended_unit_type=result.terminal_outcome.recommended_unit_type,
        )
        resp: dict = {
            "terminal": True,
            "outcome": _terminal_to_dict(result.terminal_outcome),
        }
        if version_mismatch_warning:
            resp["warnings"] = version_mismatch_warning
        return resp

    resp = {
        "terminal": False,
        "current_question": _question_to_dict(result.next_question),
    }
    if version_mismatch_warning:
        resp["warnings"] = version_mismatch_warning
    return resp


@app.get("/incidents/{incident_id}")
async def get_incident(incident_id: str):
    incident = await repo.get_incident(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    return incident


@app.patch("/incidents/{incident_id}/answer/{log_id}")
async def correct_answer(
    incident_id: str,
    log_id: str,
    request: CorrectAnswerRequest,
):
    """
    Improvement 4.2 — correct a dispatch answer within a configurable
    time window (default 60s). Writes a new dispatch log row with
    is_backtrack=True, marks the original as superseded, and re-runs
    the locked runner with the corrected answer.
    """
    incident = await repo.get_incident(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    if not incident["dispatch_protocol_id"]:
        raise HTTPException(
            status_code=400,
            detail="Incident has no protocol assigned.",
        )

    log_entry = await repo.get_dispatch_log_entry(log_id)
    if log_entry is None:
        raise HTTPException(status_code=404, detail="Dispatch log entry not found")
    if log_entry["incident_id"] != incident_id:
        raise HTTPException(
            status_code=400,
            detail="Dispatch log entry does not belong to this incident.",
        )

    # Check correction window
    log_time = datetime.fromisoformat(log_entry["timestamp"])
    window_seconds = get_answer_correction_window_seconds()
    if (_now() - log_time).total_seconds() > window_seconds:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "correction_window_expired",
                "message": (
                    f"Answer correction window ({window_seconds}s) has expired. "
                    "The answer can no longer be corrected."
                ),
            },
        )

    protocol = registry.get(incident["dispatch_protocol_id"])
    if protocol is None:
        raise HTTPException(
            status_code=500,
            detail="Protocol not in active registry.",
        )

    question = protocol.questions.get(log_entry["question_id"])
    if question is None:
        raise HTTPException(
            status_code=500,
            detail=f"Question {log_entry['question_id']!r} not found in protocol.",
        )

    # Run the corrected answer through the locked runner
    try:
        result = submit_answer(protocol, log_entry["question_id"], request.corrected_answer)
    except OutOfScriptAnswerError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "out_of_script_answer",
                "message": str(exc),
                "valid_answers": exc.valid_answers,
            },
        ) from exc

    # Write the new answer row
    new_row = await repo.append_dispatch_answer(
        incident_id=incident_id,
        question_id=log_entry["question_id"],
        question_text=question.text,
        answer=request.corrected_answer,
        protocol_version=protocol.version,
        is_backtrack=True,
    )

    # Mark the original as superseded
    await repo.correct_dispatch_answer(log_id, request.corrected_answer, uuid.UUID(new_row["id"]))

    if result.terminal_outcome is not None:
        await repo.set_dispatch_outcome(
            incident_id,
            priority_code=result.terminal_outcome.priority_code,
            recommended_unit_type=result.terminal_outcome.recommended_unit_type,
        )
        return {
            "corrected": True,
            "superseded_log_id": log_id,
            "new_log_id": new_row["id"],
            "terminal": True,
            "outcome": _terminal_to_dict(result.terminal_outcome),
        }

    return {
        "corrected": True,
        "superseded_log_id": log_id,
        "new_log_id": new_row["id"],
        "terminal": False,
        "current_question": _question_to_dict(result.next_question),
    }


@app.get("/incidents")
async def list_incidents(
    status: str | None = Query(None, description="Filter by incident status"),
    priority_code: str | None = Query(None, description="Filter by priority code"),
    assigned_unit_id: str | None = Query(None, description="Filter by assigned unit"),
    created_after: str | None = Query(None, description="ISO datetime — incidents created after this"),
    created_before: str | None = Query(None, description="ISO datetime — incidents created before this"),
    chief_complaint_contains: str | None = Query(None, min_length=2, description="Case-insensitive substring match against chief complaint"),
    limit: int = Query(50, ge=1, le=200, description="Max results to return"),
    offset: int = Query(0, ge=0, description="Number of results to skip"),
):
    """
    Search and list incidents. Supports filtering by status, priority code,
    assigned unit, creation time window, and chief complaint substring.
    Returns paginated results ordered by created_at DESC (most recent first).
    Purged incidents (pii_purged_at set) are excluded from results.
    """
    created_after_dt = None
    created_before_dt = None
    if created_after:
        try:
            created_after_dt = datetime.fromisoformat(created_after)
        except ValueError:
            raise HTTPException(
                status_code=422, detail="created_after must be a valid ISO datetime"
            )
    if created_before:
        try:
            created_before_dt = datetime.fromisoformat(created_before)
        except ValueError:
            raise HTTPException(
                status_code=422, detail="created_before must be a valid ISO datetime"
            )
    if created_after_dt and created_before_dt and created_after_dt > created_before_dt:
        raise HTTPException(
            status_code=422, detail="created_after must not be after created_before"
        )

    try:
        incidents = await repo.list_incidents(
            status=status,
            priority_code=priority_code,
            assigned_unit_id=assigned_unit_id,
            created_after=created_after_dt,
            created_before=created_before_dt,
            chief_complaint_contains=chief_complaint_contains,
            limit=limit,
            offset=offset,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    return {"incidents": incidents, "count": len(incidents), "limit": limit, "offset": offset}


@app.get("/incidents/{incident_id}/full")
async def get_incident_full(incident_id: str):
    """Exercises the Phase 1.8 exit criterion directly via HTTP."""
    full = await repo.get_incident_full(incident_id)
    if full is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    return full


@app.get("/incidents/{incident_id}/timeline")
async def get_incident_timeline(incident_id: str):
    """
    Improvement 3 — returns a single chronologically-ordered list spanning
    all event types (dispatch answers, field actions, vitals, medications,
    guidance lookups). Each row has {"timestamp", "event_type", "source", "data"}.
    """
    timeline = await repo.get_incident_timeline(incident_id)
    if timeline is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    return timeline


@app.post("/incidents/{incident_id}/status")
async def update_incident_status(incident_id: str, request: UpdateIncidentStatusRequest):
    """
    Field-unit status transitions: on_scene, transporting, handoff_complete,
    closed. Also accepted by the dispatcher: dispatched (but the
    /dispatch-unit endpoint sets this automatically when unit assignment
    succeeds, so the dispatcher rarely needs to call this directly).

    Only forward transitions are accepted — a closed incident cannot
    be reopened, and status cannot be set to 'received' via this endpoint
    (that only happens at creation). The status field in the request must
    be one of the valid IncidentStatus enum values.
    """
    incident = await repo.get_incident(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")

    try:
        new_status = IncidentStatus(request.status)
    except ValueError:
        valid = [s.value for s in IncidentStatus]
        raise HTTPException(
            status_code=422,
            detail=f"Invalid status {request.status!r}. Must be one of: {valid}",
        )

    if new_status == IncidentStatus.RECEIVED:
        raise HTTPException(
            status_code=422,
            detail="Status 'received' is set automatically at incident creation and "
            "cannot be set via this endpoint.",
        )

    # Map status to its corresponding timestamp field
    _STATUS_TIMESTAMP_FIELD = {
        IncidentStatus.DISPATCHED: "dispatched_at",
        IncidentStatus.ON_SCENE: "on_scene_at",
        IncidentStatus.TRANSPORTING: "transporting_at",
        IncidentStatus.HANDOFF_COMPLETE: "handoff_complete_at",
        IncidentStatus.CLOSED: "closed_at",
    }
    ts_field = _STATUS_TIMESTAMP_FIELD.get(new_status)
    event_ts = request.event_timestamp or _now()
    kwargs = {ts_field: event_ts} if ts_field else {}

    try:
        await repo.update_incident_status(incident_id, status=new_status, **kwargs)
    except InvalidStatusTransitionError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "invalid_status_transition",
                "current": exc.current_status.value,
                "requested": exc.requested_status.value,
                "allowed": [s.value for s in exc.allowed_statuses],
            },
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return {
        "incident_id": incident_id,
        "status": new_status.value,
        "timestamp": event_ts.isoformat(),
    }


@app.get("/incidents/{incident_id}/handoff-link")
async def get_handoff_link(incident_id: str):
    """
    Returns a time-limited, HMAC-signed URL that the dispatcher can send
    to the receiving hospital via any channel (SMS, WhatsApp, radio, phone).
    The ER doctor opens this URL to see the handoff page.
    """
    incident = await repo.get_incident(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")

    token = generate_handoff_token(incident_id)
    # Determine the base URL from the request context. In development
    # this defaults to localhost:8000. The /receiving/ path is served
    # by the static file mount below.
    base_url = f"http://localhost:8000/receiving/{incident_id}"
    handoff_url = f"{base_url}?token={token}"

    return {
        "incident_id": incident_id,
        "handoff_url": handoff_url,
        "expires_in_hours": 24,
        "instructions": (
            "Send this URL to the receiving hospital via any channel. "
            "The ER doctor opens it to see the full handoff summary."
        ),
    }


@app.get("/receiving/{incident_id}", response_class=HTMLResponse)
async def receiving_handoff_page(incident_id: str, token: str):
    """
    Serves the receiving hospital handoff HTML page. Validates the
    time-limited token before rendering. The page itself fetches the
    handoff JSON via the API and renders it client-side.

    CSS and JS are inlined into the HTML so the page is self-contained
    and does not depend on static file serving from relative paths.
    """
    if not verify_handoff_token(incident_id, token):
        raise HTTPException(
            status_code=403,
            detail="Invalid or expired handoff link. Request a new one from the dispatcher.",
        )
    receiving_dir = Path(__file__).resolve().parents[1] / "receiving-ui"
    html_path = receiving_dir / "index.html"
    if not html_path.exists():
        raise HTTPException(status_code=500, detail="Receiving UI not deployed.")
    html = html_path.read_text(encoding="utf-8")

    # Read CSS and JS files and inline them into the HTML
    css_path = receiving_dir / "style.css"
    js_path = receiving_dir / "app.js"
    if css_path.exists():
        css_content = css_path.read_text(encoding="utf-8")
        html = html.replace(
            '<link rel="stylesheet" href="style.css">',
            f'<style>{css_content}</style>',
        )
    if js_path.exists():
        js_content = js_path.read_text(encoding="utf-8")
        html = html.replace(
            '<script src="app.js"></script>',
            f'<script>{js_content}</script>',
        )

    # Inject the incident_id and token so the page can fetch data
    html = html.replace("__INCIDENT_ID__", incident_id)
    html = html.replace("__TOKEN__", token)
    return HTMLResponse(
        content=html,
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
        },
    )


@app.get("/incidents/{incident_id}/handoff")
async def get_incident_handoff(incident_id: str):
    """
    Phase 5 — returns the deterministic handoff summary assembled from
    get_incident_full(). No LLM, no inference. Everything in the response
    is a direct field from the incident record or a fixed-format rendering
    of an existing append-only log row. See app/handoff.py module docstring
    for what this deliberately does NOT include.
    """
    summary = await build_handoff_summary(incident_id)
    if summary is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    return {
        "incident_id": summary.incident_id,
        "status": summary.status,
        "chief_complaint": summary.chief_complaint,
        "priority_code": summary.priority_code,
        "recommended_unit_type": summary.recommended_unit_type,
        "assigned_unit_id": summary.assigned_unit_id,
        "dispatch_protocol_id": summary.dispatch_protocol_id,
        "dispatch_protocol_version": summary.dispatch_protocol_version,
        "field_protocol_id": summary.field_protocol_id,
        "field_protocol_version": summary.field_protocol_version,
        "routed_facility_id": summary.routed_facility_id,
        "routed_facility_name": summary.routed_facility_name,
        "dispatch_qa": summary.dispatch_qa,
        "field_actions": summary.field_actions,
        "vitals_timeline": summary.vitals_timeline,
        "medications_given": summary.medications_given,
        "guidance_lookups_used": summary.guidance_lookups_used,
        "latest_vitals": summary.latest_vitals,
        "highest_news2": summary.highest_news2,
        "lowest_gcs": summary.lowest_gcs,
        "text_rendering": summary.text_rendering,
    }


@app.get("/incidents/{incident_id}/export")
async def export_incident(incident_id: str):
    """
    Improvement 5 — returns a plain-text medico-legal audit export of the
    incident. Downloads as a file attachment with Content-Disposition header.
    """
    full = await repo.get_incident_full(incident_id)
    if full is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    text = render_audit_text(full)
    filename = f"incident_{incident_id}_audit.txt"
    return PlainTextResponse(
        content=text,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.patch("/incidents/{incident_id}/notes")
async def append_incident_note(incident_id: str, request: AppendNoteRequest):
    """
    Improvement 5 — append-only dispatcher annotation. Adds a timestamped
    free-text note to Incident.notes. Never overwrites — each PATCH appends
    a new line. The notes field accumulates chronologically across calls.
    """
    incident = await repo.get_incident(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    try:
        updated = await repo.append_incident_note(
            incident_id=incident_id,
            note_text=request.note_text,
            author_id=request.author_id,
            timestamp=_now(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return updated


@app.post("/incidents/{incident_id}/confirm-pre-arrival")
async def confirm_pre_arrival_instructions(
    incident_id: str, request: ConfirmPreArrivalRequest
):
    """
    Improvement 3.5 — logs a pre-arrival instruction read-back confirmation
    to the field log. Appends an 'incident_field_log' row with
    action_type='pre_arrival_confirmation'. The dispatcher UI calls this
    after reading instructions to the caller.
    """
    incident = await repo.get_incident(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    if not incident["priority_code"]:
        raise HTTPException(
            status_code=400,
            detail="Incident has not reached a terminal outcome yet. Nothing to confirm.",
        )

    log_row = await repo.append_field_log(
        incident_id,
        step_id="pre_arrival_confirmation",
        action_type="pre_arrival_confirmation",
        data={
            "terminal_outcome_id": request.terminal_outcome_id,
            "all_instructions_read": request.all_instructions_read,
            "confirmed_by": request.dispatcher_id,
        },
        recorded_by=request.dispatcher_id,
    )
    return log_row


# ─────────────────────────────────────────────────────────────────────────────
# Phase 6 — dashboards
#
# Read-only views over the incidents table. No new write paths. Both
# degrade to empty results rather than erroring if the table is empty
# (e.g. a freshly provisioned instance or a test environment).
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/dashboard/active-incidents")
async def dashboard_active_incidents(limit: int = 100):
    """
    All non-closed incidents ordered by priority severity (P1 first)
    then age (oldest first within the same priority group). Intended for
    a control-room display refreshed on a poll interval by the dispatcher
    or supervisor UI.
    """
    if limit < 1 or limit > 500:
        raise HTTPException(
            status_code=422, detail="limit must be between 1 and 500"
        )
    return {"incidents": await repo.get_active_incidents(limit=limit)}


@app.get("/dashboard/stats")
async def dashboard_stats(window_hours: int = 24):
    """
    Incident counts by status and priority_code over a rolling window.
    window_hours defaults to 24; max 168 (7 days) to keep the query
    bounded on a busy system.
    """
    if window_hours < 1 or window_hours > 168:
        raise HTTPException(
            status_code=422, detail="window_hours must be between 1 and 168"
        )
    return await repo.get_dashboard_stats(window_hours=window_hours)


@app.get("/dashboard/shift-handover")
async def shift_handover(
    shift_start: str = Query(..., description="ISO datetime — shift start"),
    shift_end: str = Query(..., description="ISO datetime — shift end"),
):
    """
    Improvement 4.1 — structured shift handover report. Returns counts
    by status/priority, active incidents at shift end, and the top 3
    highest-priority resolved incidents with timeline durations.
    Also returns a plain-text rendering alongside the JSON.
    """
    try:
        start_dt = datetime.fromisoformat(shift_start)
    except ValueError:
        raise HTTPException(
            status_code=422, detail="shift_start must be a valid ISO datetime"
        )
    try:
        end_dt = datetime.fromisoformat(shift_end)
    except ValueError:
        raise HTTPException(
            status_code=422, detail="shift_end must be a valid ISO datetime"
        )
    if start_dt >= end_dt:
        raise HTTPException(
            status_code=422, detail="shift_start must be before shift_end"
        )

    handover = await repo.get_shift_handover(start_dt, end_dt)
    handover["text_rendering"] = repo.render_shift_handover_text(handover)
    return handover


# ─────────────────────────────────────────────────────────────────────────────
# Admin — operational maintenance endpoints
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/admin/purge-expired-incidents", dependencies=[Security(_require_admin_key)])
async def purge_expired_incidents():
    """
    Triggers the Phase 1.9 retention purge: nullifies caller_location PII
    fields on incidents closed longer than INCIDENT_RETENTION_DAYS ago
    (resolved: 30 days) and stamps pii_purged_at. Safe to call repeatedly —
    already-purged incidents are skipped via the pii_purged_at IS NULL filter.

    Intended to be called from an external cron / scheduler hitting this
    endpoint. Not wired to a scheduler in-process — no background task
    dependency added to keep the app footprint minimal.
    """
    result = await repo.purge_expired_incidents()
    return result


@app.post("/admin/reload-protocols", dependencies=[Security(_require_admin_key)])
async def reload_protocols():
    """
    Hot-reload both dispatch and field protocol registries without a
    server restart. Calls load_all() on each registry in sequence. A
    broken protocol file must not crash the reload or leave the registry
    in a partially-cleared state — load_all() clears then reloads, and
    any broken file is captured in list_rejected().

    TODO: gate this endpoint on an admin key or network policy before
    production deployment.
    """
    try:
        registry.load_all()
    except Exception as exc:
        logger.error("Dispatch protocol reload failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Dispatch protocol reload failed: {exc}")

    try:
        field_registry.load_all()
    except Exception as exc:
        logger.error("Field protocol reload failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Field protocol reload failed: {exc}")

    logger.info("Protocol registry reloaded by request")

    return {
        "dispatch": {
            "active": registry.list_active(),
            "rejected": registry.list_rejected(),
        },
        "field": {
            "active": field_registry.list_active(),
            "rejected": field_registry.list_rejected(),
        },
    }


@app.get("/admin/protocol-status", dependencies=[Security(_require_admin_key)])
async def protocol_status():
    """
    Returns active and rejected protocols for both dispatch and field
    registries, including rejection reasons. Useful for confirming
    that real sign-off has been recorded in the protocol files and
    the approved protocols are actually selectable.
    """
    return {
        "dispatch": {
            "active": registry.list_active(),
            "rejected": registry.list_rejected(),
        },
        "field": {
            "active": field_registry.list_active(),
            "rejected": field_registry.list_rejected(),
        },
    }


@app.post("/incidents/{incident_id}/vitals")
async def add_incident_vitals(incident_id: str, request: AddVitalsRequest):
    incident = await repo.get_incident(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    vitals = request.model_dump(exclude={"recorded_by"})
    return await repo.add_vitals(incident_id, request.recorded_by, vitals)


@app.post("/incidents/{incident_id}/field-log")
async def add_incident_field_log(incident_id: str, request: AddFieldLogRequest):
    incident = await repo.get_incident(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    return await repo.append_field_log(
        incident_id,
        step_id=request.step_id,
        action_type=request.action_type,
        data=request.data,
        recorded_by=request.recorded_by,
    )


@app.post("/incidents/{incident_id}/medication")
async def add_incident_medication(incident_id: str, request: AddMedicationRequest):
    """
    Records a drug or item a unit carried, considered, or administered.

    Resolved per Phase 0.5: logging is unconditional — every relevant
    item should be logged regardless of whether it was actually given.
    There is deliberately no allowlist/formulary gate here; an earlier
    version of this endpoint rejected drug names outside a configured
    formulary, which was the wrong model for what was actually wanted.
    `administered` on the request records whether the item was given;
    it does not affect whether the row is written.
    """
    incident = await repo.get_incident(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")

    return await repo.add_medication_given(
        incident_id,
        drug_name=request.drug_name.strip(),
        dose=request.dose.strip(),
        route=request.route.strip(),
        given_by=request.given_by.strip(),
        administered=request.administered,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Field-side protocol runner (Phase 4)
#
# See app/protocols/field_runner.py module docstring for the full
# rationale on why this is not a copy of the Mode 1 locked runner: no
# hard-fail on skip/reorder, no branch graph, no terminal outcome. The
# durable record remains incident_field_log (already written by
# /field-log above, untouched by this section) — these endpoints are an
# orientation/checklist convenience layered on top of it, not a new
# source of truth.
# ─────────────────────────────────────────────────────────────────────────────

async def _build_field_run_state(incident_id: str, protocol_id: str) -> FieldRunState:
    protocol = field_registry.get(protocol_id)
    if protocol is None:
        raise HTTPException(
            status_code=404,
            detail=f"Field protocol {protocol_id!r} is not in the active registry.",
        )
    field_log = await repo.get_field_log(incident_id)
    return rebuild_from_field_log(protocol, field_log)


@app.post("/incidents/{incident_id}/field-protocol")
async def select_field_protocol(incident_id: str, request: SelectFieldProtocolRequest):
    incident = await repo.get_incident(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")

    protocol = field_registry.get(request.protocol_id)
    if protocol is None:
        raise HTTPException(
            status_code=404,
            detail=f"Field protocol {request.protocol_id!r} is not in the active registry.",
        )

    await repo.set_field_protocol(incident_id, protocol.protocol_id, protocol.version)

    state = await _build_field_run_state(incident_id, protocol.protocol_id)
    return {
        "protocol_id": protocol.protocol_id,
        "protocol_version": protocol.version,
        "disease_or_presentation": protocol.disease_or_presentation,
        "steps": state.summary(),
        "next_pending_step": _field_step_to_dict(state.next_pending_step()),
        "is_complete": state.is_complete(),
    }


@app.get("/incidents/{incident_id}/field-protocol/state")
async def get_field_protocol_state(incident_id: str):
    incident = await repo.get_incident(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    if not incident["field_protocol_id"]:
        raise HTTPException(
            status_code=400,
            detail="No field protocol selected for this incident yet.",
        )

    state = await _build_field_run_state(incident_id, incident["field_protocol_id"])
    return {
        "protocol_id": incident["field_protocol_id"],
        "protocol_version": incident["field_protocol_version"],
        "steps": state.summary(),
        "next_pending_step": _field_step_to_dict(state.next_pending_step()),
        "is_complete": state.is_complete(),
    }


@app.post("/incidents/{incident_id}/field-protocol/step")
async def mark_field_protocol_step(incident_id: str, request: MarkFieldStepRequest):
    """
    Marks a checklist step's status AND writes the corresponding
    incident_field_log row in the same call — the field UI does not need
    to call /field-log separately for protocol-driven steps. Manual,
    protocol-independent field log entries (radio updates, free-text
    notes, etc.) still go through POST /incidents/{id}/field-log directly,
    unchanged.
    """
    incident = await repo.get_incident(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    if not incident["field_protocol_id"]:
        raise HTTPException(
            status_code=400,
            detail="No field protocol selected for this incident yet.",
        )

    protocol = field_registry.get(incident["field_protocol_id"])
    if protocol is None:
        raise HTTPException(
            status_code=500,
            detail=(
                f"Field protocol {incident['field_protocol_id']!r} referenced by "
                "this incident is not in the active registry."
            ),
        )

    matching_step = next(
        (s for s in protocol.steps if s.step_id == request.step_id), None
    )
    if matching_step is None:
        raise HTTPException(
            status_code=404,
            detail=f"Step {request.step_id!r} does not exist in field protocol "
            f"{protocol.protocol_id!r}.",
        )

    if request.status not in ("done", "skipped", "not_applicable"):
        raise HTTPException(
            status_code=422,
            detail="status must be one of: done, skipped, not_applicable",
        )

    log_data = {**request.data, "step_status": request.status, "step_title": matching_step.title}
    await repo.append_field_log(
        incident_id,
        step_id=request.step_id,
        action_type=matching_step.action_type,
        data=log_data,
        recorded_by=request.recorded_by,
    )

    state = await _build_field_run_state(incident_id, protocol.protocol_id)
    return {
        "steps": state.summary(),
        "next_pending_step": _field_step_to_dict(state.next_pending_step()),
        "is_complete": state.is_complete(),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Improvement 4.3 — Responder location updates
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/incidents/{incident_id}/unit-location")
async def add_unit_location(incident_id: str, request: UnitLocationRequest):
    """
    Stores a field-unit GPS ping. The latest location is used by
    route_facility to find the nearest hospital from the unit's
    current position rather than the caller's intake coordinates.
    """
    incident = await repo.get_incident(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    loc = await repo.add_unit_location(
        incident_id=incident_id,
        lat=request.lat,
        lon=request.lon,
        recorded_by=request.recorded_by,
        timestamp=request.timestamp or _now(),
    )
    return loc


@app.get("/incidents/{incident_id}/unit-location/latest")
async def get_latest_unit_location(incident_id: str):
    """Returns the most recent GPS ping for the field unit on this incident."""
    incident = await repo.get_incident(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    loc = await repo.get_latest_unit_location(incident_id)
    if loc is None:
        return {"location": None, "message": "No location recorded for this incident."}
    return {"location": loc}


# ─────────────────────────────────────────────────────────────────────────────
# Mode 2 — guidance lookup (see docs/GOVERNANCE.md)
#
# Strictly bounded: only callable for a question with allow_guidance_lookup
# true on the protocol the incident is actually running, returns the fixed
# author-written guidance_note (never a search/LLM result — see
# app/protocols/schema.py ProtocolQuestion.guidance_note docstring), and is
# logged to guidance_lookup_log — a table separate from
# incident_dispatch_log so Mode 1 and Mode 2 are independently
# reconstructable and never conflated, per governance.
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/incidents/{incident_id}/guidance-lookup")
async def guidance_lookup(incident_id: str, request: GuidanceLookupRequest):
    incident = await repo.get_incident(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    if not incident["dispatch_protocol_id"]:
        raise HTTPException(
            status_code=400,
            detail="Incident has no protocol assigned. Cannot perform guidance lookup.",
        )

    protocol = registry.get(incident["dispatch_protocol_id"])
    if protocol is None:
        raise HTTPException(
            status_code=500,
            detail=(
                f"Protocol {incident['dispatch_protocol_id']!r} referenced by "
                "this incident is not in the active registry."
            ),
        )

    question = protocol.questions.get(request.question_id)
    if question is None:
        raise HTTPException(
            status_code=404,
            detail=f"Question {request.question_id!r} does not exist in this protocol.",
        )

    if not question.allow_guidance_lookup:
        # Loud rejection, not a silent empty result — guidance lookup at a
        # non-gated question is a governance violation, never a no-op.
        raise HTTPException(
            status_code=403,
            detail={
                "error": "guidance_lookup_not_permitted",
                "message": (
                    f"Question {request.question_id!r} does not permit guidance "
                    "lookup. This action cannot alter the locked script."
                ),
            },
        )

    result_summary = question.guidance_note or "No guidance note authored for this question."

    await repo.log_guidance_lookup(
        incident_id=incident_id,
        query_text=question.text,
        result_summary=result_summary,
        dispatcher_id=request.dispatcher_id,
        question_id=request.question_id,
    )

    return {
        "question_id": request.question_id,
        "guidance_note": result_summary,
        "informational_only": True,
        "note": (
            "This is Mode 2 supplementary guidance. It has not altered and "
            "cannot alter the priority code, unit type, or branch of the "
            "locked script."
        ),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Unit dispatch / facility routing (Phase 3)
#
# Both external calls degrade to None/empty per
# app/external/*.py — never raise into the request, and the response
# always tells the dispatcher explicitly whether assignment succeeded or
# manual action is required. See each client module's docstring for the
# interim-contract caveat (Phase 0.3/0.4 — open decisions).
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/incidents/{incident_id}/dispatch-unit")
async def dispatch_unit(incident_id: str, request: DispatchUnitRequest):
    incident = await repo.get_incident(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    if not incident["priority_code"] or not incident["recommended_unit_type"]:
        raise HTTPException(
            status_code=400,
            detail="Incident has no dispatch outcome yet — complete the locked "
            "script to a terminal outcome before requesting unit assignment.",
        )

    lat = request.lat if request.lat is not None else incident["caller_location_lat"]
    lon = request.lon if request.lon is not None else incident["caller_location_lon"]

    result = await _dispatch_client.dispatch(
        incident_id=incident_id,
        priority_code=incident["priority_code"],
        recommended_unit_type=incident["recommended_unit_type"],
        lat=lat,
        lon=lon,
    )

    if result is None:
        # Degraded mode: dispatch service unavailable. Generate a
        # synthetic unit ID so the dispatcher can still notify the
        # field unit and the status can advance to DISPATCHED.
        synthetic_unit_id = f"MANUAL-{incident_id[:8]}"
        await repo.set_assigned_unit(incident_id, synthetic_unit_id)
        await repo.update_incident_status(
            incident_id, status=IncidentStatus.DISPATCHED, dispatched_at=_now()
        )
        return {
            "assigned": True,
            "dispatch_id": None,
            "assigned_unit_id": synthetic_unit_id,
            "eta_minutes": None,
            "status": "dispatched_manual",
            "field_url": f"http://localhost:8081/?incident_id={incident_id}&unit_id={synthetic_unit_id}",
            "message": "Dispatch service unavailable. Unit assigned manually. "
            "Send the field URL to the paramedic.",
        }

    await repo.set_assigned_unit(incident_id, result.assigned_unit_id)
    await repo.update_incident_status(
        incident_id, status=IncidentStatus.DISPATCHED, dispatched_at=_now()
    )
    if result.eta_minutes is not None:
        await repo.set_dispatch_eta(incident_id, result.eta_minutes)

    return {
        "assigned": True,
        "dispatch_id": result.dispatch_id,
        "assigned_unit_id": result.assigned_unit_id,
        "eta_minutes": result.eta_minutes,
        "status": result.status,
        "field_url": f"http://localhost:8081/?incident_id={incident_id}&unit_id={result.assigned_unit_id}",
    }


@app.post("/incidents/{incident_id}/route-facility")
async def route_facility(incident_id: str, request: RouteFacilityRequest):
    incident = await repo.get_incident(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")

    # Improvement 4.3 — prefer the latest unit location as search origin
    # if one exists, over the caller-provided lat/lon.
    lat = request.lat
    lon = request.lon
    unit_loc = await repo.get_latest_unit_location(incident_id)
    if unit_loc is not None:
        lat = unit_loc["lat"]
        lon = unit_loc["lon"]

    facilities = await _facility_client.find_nearest(
        lat=lat,
        lon=lon,
        required_services=request.required_services,
        radius_km=request.radius_km,
    )

    if not facilities:
        return {
            "facilities": [],
            "message": "Facility registry unavailable, unconfigured, or returned "
            "no matches. Fall back to locally known facilities — this is NOT "
            "confirmation that no facilities exist nearby.",
        }

    return {
        "facilities": [
            {
                "facility_id": f.facility_id,
                "name": f.name,
                "lat": f.lat,
                "lon": f.lon,
                "distance_km": f.distance_km,
                "services": f.services,
                "capacity_status": f.capacity_status,
            }
            for f in facilities
        ]
    }


@app.post("/incidents/{incident_id}/report-admission")
async def report_admission(incident_id: str, request: ReportAdmissionRequest):
    incident = await repo.get_incident(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    if not incident["priority_code"]:
        raise HTTPException(
            status_code=400,
            detail="Incident has no priority code yet — cannot report admission.",
        )

    result = await _dispatch_client.report_admission(
        incident_id=incident_id,
        facility_id=request.facility_id,
        priority_code=incident["priority_code"],
        summary=request.summary,
    )

    # Facility name is not returned by the report_admission contract;
    # record the incident's routed facility by ID only. A separate facility
    # lookup (route_facility) is the place a name would come from.
    await repo.set_routed_facility(
        incident_id, request.facility_id, request.facility_id
    )

    if result is None:
        return {
            "acknowledged": False,
            "message": "Emergency dispatch service unavailable or unconfigured. "
            "Manual admission notification required.",
        }

    return {
        "acknowledged": result.acknowledged,
        "admission_id": result.admission_id,
        "facility_confirmation_id": result.facility_confirmation_id,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Serialisation helpers
# ─────────────────────────────────────────────────────────────────────────────

def _question_to_dict(question) -> dict:
    return {
        "question_id": question.question_id,
        "text": question.text,
        "answer_type": question.answer_type,
        "options": question.options,
        "valid_answers": list(question.branch_map.keys()),
        "allow_guidance_lookup": question.allow_guidance_lookup,
    }


def _terminal_to_dict(outcome) -> dict:
    return {
        "priority_code": outcome.priority_code,
        "recommended_unit_type": outcome.recommended_unit_type,
        "pre_arrival_instructions": outcome.pre_arrival_instructions,
    }


def _field_step_to_dict(step) -> dict | None:
    if step is None:
        return None
    return {
        "step_id": step.step_id,
        "title": step.title,
        "action_type": step.action_type,
        "description": step.description,
        "guideline_ref": step.guideline_ref,
    }
