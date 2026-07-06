"""app/main.py.

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
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security.api_key import APIKeyHeader
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
import json as _json
import re as _re

from starlette.responses import HTMLResponse, PlainTextResponse, StreamingResponse

from . import repositories as repo
from .cache import cache_get, cache_set, cache_delete, cache_health
from .config import (
    get_admin_api_key,
    get_allowed_origins,
    get_answer_correction_window_seconds,
    get_dispatcher_credentials,
    get_field_ui_base_url,
    get_handoff_base_url,
    get_handoff_signing_key,
    get_incident_retention_days,
    get_prehospital_formulary,
    get_purge_schedule_enabled,
    get_rate_limit_chat_per_minute,
    get_rate_limit_default_per_minute,
    get_session_token_expiry_hours,
    is_database_configured,
    is_formulary_configured,
    is_production,
    validate_startup_config,
)
from .db import check_database, close_engine, init_engine
from .external.emergency_dispatch import EmergencyDispatchClient
from .external.facility_registry import FacilityRegistryClient
from .external.triage_ranker import TriageRankerClient
from .external.hazard_zones import DEFAULT_HAZARD_ZONES, check_route_hazards
from .auth import generate_session_token, get_session_role, verify_credentials, verify_session_token
from .handoff import build_handoff_summary, render_audit_text
from .handoff_link import generate_handoff_token, verify_handoff_token
from sqlalchemy import update as sa_update

from .models import Incident as _IncidentModel, IncidentStatus
from .observability import MetricsMiddleware, RateLimitMiddleware, metrics_text
from .protocols.field_registry import field_registry
from .protocols.field_runner import FieldRunState, rebuild_from_field_log
from .protocols.registry import registry
from .protocols.schema import DispatchProtocol
from .protocols import semantic_matcher
from .protocols.protocol_rag import protocol_rag
from .external.llm_client import LLMClient
from .protocols.runner import (
    OutOfScriptAnswerError,
    can_backtrack,
    get_entry_question,
    submit_answer,
)
from .repositories import InvalidStatusTransitionError
from .scoring.scorers import (
    ScoringError,
    compute_news2,
    compute_pews,
    compute_revised_trauma_score,
    compute_shock_index,
)

# Structured logging to file
import os as _os
from logging.handlers import RotatingFileHandler

_log_dir = _os.path.join(_os.path.dirname(_os.path.dirname(__file__)), 'logs')
_os.makedirs(_log_dir, exist_ok=True)
_log_file = _os.path.join(_log_dir, 'ambulance_cdss.log')

_file_handler = RotatingFileHandler(_log_file, maxBytes=5*1024*1024, backupCount=5)
_file_handler.setLevel(logging.DEBUG)
_file_handler.setFormatter(logging.Formatter(
    '%(asctime)s | %(levelname)-8s | %(name)s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
))

_console_handler = logging.StreamHandler()
_console_handler.setLevel(logging.INFO)
_console_handler.setFormatter(logging.Formatter(
    '%(asctime)s | %(levelname)-8s | %(message)s',
    datefmt='%H:%M:%S'
))

logging.root.setLevel(logging.DEBUG)
logging.root.addHandler(_file_handler)
logging.root.addHandler(_console_handler)

logger = logging.getLogger(__name__)


def _validate_uuid(incident_id: str) -> str:
    """Validate that incident_id is a proper UUID. Returns the ID if valid,
    raises 422 if not. Prevents 500 errors from malformed path parameters.
    """
    try:
        uuid.UUID(incident_id)
        return incident_id
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid incident ID format: {incident_id!r}",
        )


def _now() -> datetime:
    return datetime.now(UTC)


async def log_audit(action: str, actor_id: str = None, incident_id: str = None, details: dict = None):
    try:
        await repo.insert_audit_event(action, actor_id, incident_id, details)
    except Exception as exc:
        logger.warning('Audit log write failed: %s', exc)


# ── Admin API key dependency ───────────────────────────────────────────────

_admin_key_header = APIKeyHeader(name="X-Admin-Key", auto_error=False)


async def _require_admin_key(key: str | None = Security(_admin_key_header)) -> None:
    """Validates the X-Admin-Key header for admin endpoints.
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


_auth_header = APIKeyHeader(name="Authorization", auto_error=False)


def _require_role(allowed_roles: list[str]):
    """Dependency factory that checks the session token's role against allowed_roles.
    Extracts the Bearer token from the Authorization header and verifies the role.
    In development mode (no credentials configured), bypasses role check.
    """
    async def _check(auth: str | None = Security(_auth_header)):
        from .config import get_dispatcher_credentials
        if not get_dispatcher_credentials():
            return
        if not auth:
            raise HTTPException(
                status_code=401,
                detail={"error": "unauthorized", "message": "Authorization header required."},
            )
        token = auth.removeprefix("Bearer ").strip()
        session = verify_session_token(token)
        if session is None:
            raise HTTPException(
                status_code=401,
                detail={"error": "invalid_token", "message": "Invalid or expired session token."},
            )
        if session.role not in allowed_roles:
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "insufficient_role",
                    "message": f"Required role: {allowed_roles}. Your role: {session.role}.",
                },
            )
    return _check


_facility_client = FacilityRegistryClient()
_dispatch_client = EmergencyDispatchClient()
_triage_client = TriageRankerClient()
_llm_client = LLMClient()


_purge_task: asyncio.Task | None = None


async def _purge_scheduler_loop() -> None:
    """Background loop that periodically purges expired PII from closed
    incidents. Runs every 6 hours when PURGE_SCHEDULE_ENABLED=true and
    a database is configured.
    """
    PURGE_INTERVAL_SECONDS = 6 * 3600  # 6 hours
    while True:
        await asyncio.sleep(PURGE_INTERVAL_SECONDS)
        try:
            result = await repo.purge_expired_incidents()
            purged = result.get("purged", 0)
            if purged > 0:
                logger.info("PII purge scheduler: %d incident(s) purged", purged)
        except Exception as exc:
            logger.warning("PII purge scheduler error: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _purge_task

    validate_startup_config()
    await init_engine()

    # ── Gap 11: Protocol governance acknowledgment ──────────────────────────
    # All 8 dispatch protocols are currently blocked by governance
    # (approved_by='Dev Setup'). This is BY DESIGN — no doctor has signed
    # off yet. The system operates in DEGRADED MODE: no locked protocols
    # are active, the dispatcher must assess manually, and the system
    # functions as a documentation/triage-assist tool rather than a
    # clinical decision support system. Once a medical director signs
    # off, protocols will become active and the system will transition
    # to full governance-gated operation.
    # ────────────────────────────────────────────────────────────────────────

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
        "Ambulance CDSS started. Active dispatch protocols: %d, active field protocols: %d",
        len(registry.list_active()),
        len(field_registry.list_active()),
    )

    # Build semantic matcher index for TF-IDF protocol matching
    registry.build_semantic_index()
    if semantic_matcher.is_available():
        logger.info("Semantic protocol matcher ready (sklearn available)")
    else:
        logger.info("Semantic matcher unavailable (sklearn not installed)")
    logger.info("Protocol RAG ready: %s", "active" if protocol_rag.is_available() else "inactive")

    # Log LLM client status
    if _llm_client.is_configured:
        logger.info("LLM fallback configured at %s", _llm_client.api_url)
    else:
        logger.info("LLM fallback not configured — NLP uses regex/MedSpaCy only")

    # Epic 7.2: Start PII purge scheduler if enabled and DB is configured
    if get_purge_schedule_enabled() and is_database_configured():
        _purge_task = asyncio.create_task(_purge_scheduler_loop())
        logger.info("PII purge scheduler started (every 6 hours)")

    yield

    # Shutdown: cancel purge scheduler
    if _purge_task is not None:
        _purge_task.cancel()
        try:
            await _purge_task
        except asyncio.CancelledError:
            pass

    await close_engine()


app = FastAPI(title="Ambulance CDSS", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_allowed_origins(),
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "X-Admin-Key", "Authorization", "X-Dispatcher-ID"],
)
app.add_middleware(
    RateLimitMiddleware,
    limited_paths={
        "/incidents": get_rate_limit_chat_per_minute(),
        "": get_rate_limit_default_per_minute(),
    },
)
app.add_middleware(MetricsMiddleware)


# ── Global exception handler ──────────────────────────────────────────

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error('Unhandled exception: %s %s: %s', request.method, request.url.path, exc, exc_info=True)
    return JSONResponse(
        status_code=500,
        content={'error': 'internal_server_error', 'message': 'An unexpected error occurred. Please try again.'}
    )


# ── Startup time for uptime tracking ──────────────────────────────────
_start_time = datetime.now(UTC)


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
    cached = cache_get("protocols")
    if cached is not None:
        return cached
    result = {"active": registry.list_active(), "rejected": registry.list_rejected()}
    cache_set("protocols", result, ttl_seconds=60)
    return result


@app.get("/protocols/match")
async def match_protocols(q: str = Query(..., min_length=1, description="Search query")):
    """Semantic protocol matching endpoint. Returns protocols ranked by
    TF-IDF cosine similarity to the query. Falls back to trigger-word
    matching when semantic matching is unavailable.
    """
    # Primary: semantic matching
    if semantic_matcher.is_available():
        results = semantic_matcher.find_best_match(q, top_k=5)
    else:
        results = []

    # Secondary: also run trigger-word matching for comparison
    trigger_match = registry.match_by_chief_complaint(q)
    trigger_result = None
    if trigger_match is not None:
        trigger_result = {
            "protocol_id": trigger_match.protocol.protocol_id,
            "confidence": trigger_match.confidence,
            "matched_triggers": trigger_match.matched_triggers,
        }

    return {
        "query": q,
        "semantic_available": semantic_matcher.is_available(),
        "semantic_matches": results,
        "trigger_match": trigger_result,
    }


@app.get("/protocols/search")
async def search_protocols(q: str = Query(..., min_length=1, description="Search query")):
    """Hybrid RAG protocol search endpoint. Combines keyword matching,
    BM25 lexical retrieval, and TF-IDF cosine similarity for robust
    protocol matching.
    """
    rag_results = registry.match_by_rag(q, top_k=5)

    # Also run trigger-word matching for comparison
    trigger_match = registry.match_by_chief_complaint(q)
    trigger_result = None
    if trigger_match is not None:
        trigger_result = {
            "protocol_id": trigger_match.protocol.protocol_id,
            "confidence": trigger_match.confidence,
            "matched_triggers": trigger_match.matched_triggers,
        }

    return {
        "query": q,
        "rag_results": rag_results,
        "trigger_match": trigger_result,
    }


@app.get("/field-protocols")
async def list_field_protocols():
    cached = cache_get("field_protocols")
    if cached is not None:
        return cached
    result = {
        "active": field_registry.list_active(),
        "rejected": field_registry.list_rejected(),
    }
    cache_set("field_protocols", result, ttl_seconds=60)
    return result


@app.get("/formulary")
async def get_formulary():
    """DEPRECATED — Phase 0.5 was resolved as unconditional logging with no
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
    next_of_kin_name: str | None = None
    next_of_kin_phone: str | None = None
    next_of_kin_relationship: str | None = None


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
    # Epic 7.6: age_years for pediatric scoring (PEWS)
    age_years: float | None = None
    is_pediatric: bool | None = None


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
    county: str | None = None
    required_level: int | None = None


class DiversionRequest(BaseModel):
    is_diverted: bool
    reason: str | None = None
    estimated_resume: str | None = None


class StockUpdateRequest(BaseModel):
    items: dict[str, bool] = Field(default_factory=dict)


class ReportAdmissionRequest(BaseModel):
    facility_id: str = Field(min_length=1)
    summary: str = Field(min_length=1)


class NotifyNextOfKinRequest(BaseModel):
    pass  # No body needed — uses incident data


class SelectDispatchProtocolRequest(BaseModel):
    protocol_id: str = Field(min_length=1)
    dispatcher_id: str = Field(min_length=1)


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
    author_role: str = "dispatcher"
    note_type: str = "dispatcher_note"


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


class CorrectionRequest(BaseModel):
    """Gap 9 — NLP confidence threshold + feedback correction model."""
    field: str = Field(min_length=1)
    original_value: str = Field(min_length=1)
    corrected_value: str = Field(min_length=1)
    dispatcher_id: str = Field(min_length=1)


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
    """Phase 4.1 — accepts a structured CapturePayload from the dispatcher
    UI or web listener, maps its fields to create_incident internals,
    and delegates to the same create_incident logic. No duplicate code.

    Returns the standard create_incident response plus capture_correlation_id
    echoing the dispatchId for event-log correlation.
    """
    incident_info = request.incidentInfo or {}
    patient_info = request.patientInfo or {}

    # Map structured payload to create_incident parameters
    chief_complaint = incident_info.get("description", "") or request.dispatchId

    # Extract location from incidentInfo.location
    location = incident_info.get("location") or {}
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
        lambda t: logger.error("Triage enrichment (capture) task exception: %s", t.exception())
        if t.exception()
        else None
    )

    if match is None:
        cache_delete("active_incidents")
        cache_delete("dashboard_stats:*")
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
    """Create an incident and, if a matching locked protocol is found by
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
        next_of_kin_name=request.next_of_kin_name,
        next_of_kin_phone=request.next_of_kin_phone,
        next_of_kin_relationship=request.next_of_kin_relationship,
    )

    match = registry.match_by_chief_complaint(request.chief_complaint)
    if match is None:
        # Provide hybrid RAG suggestions when trigger-word matching fails
        rag_suggestions: list[dict] = []
        rag_results = registry.match_by_rag(request.chief_complaint, top_k=3)
        for r in rag_results:
            proto = registry.get(r["protocol_id"])
            if proto is not None:
                rag_suggestions.append({
                    "protocol_id": r["protocol_id"],
                    "score": r["score"],
                    "methods": r.get("methods", []),
                    "description": r.get("description", ""),
                    "review_note": "RAG match — review before selecting",
                })

        cache_delete("active_incidents")
        cache_delete("dashboard_stats:*")
        resp: dict = {
            "incident": incident,
            "protocol_matched": False,
            "message": "No locked protocol matches this chief complaint. "
            "Manual protocol selection required.",
        }
        if rag_suggestions:
            resp["rag_suggestions"] = rag_suggestions
        return resp

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
    cache_delete("active_incidents")
    cache_delete("dashboard_stats:*")
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
        lambda t: logger.error("Triage enrichment task exception: %s", t.exception())
        if t.exception()
        else None
    )

    return resp


@app.post("/incidents/{incident_id}/answer", dependencies=[Security(_require_role(["dispatcher"]))])
async def submit_incident_answer(incident_id: str, request: SubmitAnswerRequest):
    """Submit an answer to the current locked-script question.

    On OutOfScriptAnswerError: returns 422 with the valid answer set —
    this is the loud, immediate, fully-logged rejection described in
    app/protocols/runner.py. It is not caught and defaulted anywhere in
    this call chain.
    """
    _validate_uuid(incident_id)
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
            "Protocol version mismatch: incident %s started on v%s but live registry is v%s",
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
    cache_delete(f"incident:{incident_id}")
    cache_delete(f"incident_full:{incident_id}")
    cache_delete("active_incidents")
    cache_delete("dashboard_stats:*")
    await log_audit("submit_answer", request.dispatcher_id, incident_id, {"question_id": request.current_question_id, "answer": request.answer})
    _notify_sse(incident_id, "dispatch_answer", {"incident_id": incident_id, "question_id": request.current_question_id})

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
    _validate_uuid(incident_id)
    cache_key = f"incident:{incident_id}"
    cached = cache_get(cache_key)
    if cached is not None:
        return cached
    incident = await repo.get_incident(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    cache_set(cache_key, incident, ttl_seconds=10)
    return incident


@app.patch("/incidents/{incident_id}/answer/{log_id}")
async def correct_answer(
    incident_id: str,
    log_id: str,
    request: CorrectAnswerRequest,
):
    """Improvement 4.2 — correct a dispatch answer within a configurable
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
    cache_delete(f"incident:{incident_id}")
    cache_delete(f"incident_full:{incident_id}")
    await log_audit("correction", request.dispatcher_id, incident_id, {"log_id": log_id, "corrected_answer": request.corrected_answer})

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
    created_after: str | None = Query(
        None, description="ISO datetime — incidents created after this"
    ),
    created_before: str | None = Query(
        None, description="ISO datetime — incidents created before this"
    ),
    chief_complaint_contains: str | None = Query(
        None, min_length=2, description="Case-insensitive substring match against chief complaint"
    ),
    limit: int = Query(50, ge=1, le=200, description="Max results to return"),
    offset: int = Query(0, ge=0, description="Number of results to skip"),
):
    """Search and list incidents. Supports filtering by status, priority code,
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
    _validate_uuid(incident_id)
    cache_key = f"incident_full:{incident_id}"
    cached = cache_get(cache_key)
    if cached is not None:
        return cached
    full = await repo.get_incident_full(incident_id)
    if full is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    cache_set(cache_key, full, ttl_seconds=15)
    return full


@app.get("/incidents/{incident_id}/timeline")
async def get_incident_timeline(incident_id: str):
    """Improvement 3 — returns a single chronologically-ordered list spanning
    all event types (dispatch answers, field actions, vitals, medications,
    guidance lookups). Each row has {"timestamp", "event_type", "source", "data"}.
    """
    _validate_uuid(incident_id)
    timeline = await repo.get_incident_timeline(incident_id)
    if timeline is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    return timeline


@app.post("/incidents/{incident_id}/status")
async def update_incident_status(incident_id: str, request: UpdateIncidentStatusRequest):
    """Field-unit status transitions: on_scene, transporting, handoff_complete,
    closed. Also accepted by the dispatcher: dispatched (but the
    /dispatch-unit endpoint sets this automatically when unit assignment
    succeeds, so the dispatcher rarely needs to call this directly).

    Only forward transitions are accepted — a closed incident cannot
    be reopened, and status cannot be set to 'received' via this endpoint
    (that only happens at creation). The status field in the request must
    be one of the valid IncidentStatus enum values.
    """
    _validate_uuid(incident_id)
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

    cache_delete(f"incident:{incident_id}")
    cache_delete(f"incident_full:{incident_id}")
    cache_delete("active_incidents")
    cache_delete("dashboard_stats:*")
    await log_audit("status_change", None, incident_id, {"new_status": new_status.value})

    return {
        "incident_id": incident_id,
        "status": new_status.value,
        "timestamp": event_ts.isoformat(),
    }


@app.get("/incidents/{incident_id}/handoff-link")
async def get_handoff_link(incident_id: str):
    """Returns a time-limited, HMAC-signed URL that the dispatcher can send
    to the receiving hospital via any channel (SMS, WhatsApp, radio, phone).
    The ER doctor opens this URL to see the handoff page.
    """
    _validate_uuid(incident_id)
    incident = await repo.get_incident(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")

    token = generate_handoff_token(incident_id)
    # Determine the base URL from config. In development this defaults
    # to localhost:8000. The /receiving/ path is served by the static
    # file mount below.
    base_url = f"{get_handoff_base_url()}/receiving/{incident_id}"
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
    """Serves the receiving hospital handoff HTML page. Validates the
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
            f"<style>{css_content}</style>",
        )
    if js_path.exists():
        js_content = js_path.read_text(encoding="utf-8")
        html = html.replace(
            '<script src="app.js"></script>',
            f"<script>{js_content}</script>",
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
    """Phase 5 — returns the deterministic handoff summary assembled from
    get_incident_full(). No LLM, no inference. Everything in the response
    is a direct field from the incident record or a fixed-format rendering
    of an existing append-only log row. See app/handoff.py module docstring
    for what this deliberately does NOT include.
    """
    _validate_uuid(incident_id)
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
        "eta_minutes": summary.eta_minutes,
        "dispatch_qa": summary.dispatch_qa,
        "field_actions": summary.field_actions,
        "vitals_timeline": summary.vitals_timeline,
        "medications_given": summary.medications_given,
        "guidance_lookups_used": summary.guidance_lookups_used,
        "latest_vitals": summary.latest_vitals,
        "highest_news2": summary.highest_news2,
        "lowest_gcs": summary.lowest_gcs,
        "casualties": summary.casualties,
        "is_multi_casualty": summary.is_multi_casualty,
        "text_rendering": summary.text_rendering,
    }


@app.get("/incidents/{incident_id}/export")
async def export_incident(incident_id: str):
    """Improvement 5 — returns a plain-text medico-legal audit export of the
    incident. Downloads as a file attachment with Content-Disposition header.
    """
    _validate_uuid(incident_id)
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
    """Structured note append. Stores in incident_notes table with author, role,
    type, and audit timestamps. Also appends to the legacy text blob for
    backwards compatibility. Broadcasts SSE note_added event.
    """
    _validate_uuid(incident_id)
    incident = await repo.get_incident(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")

    # Write to structured notes table
    try:
        note = await repo.add_note(
            incident_id=incident_id,
            note_text=request.note_text,
            author_id=request.author_id,
            author_role=request.author_role,
            note_type=request.note_type,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # Also append to legacy text blob for backwards compatibility
    try:
        await repo.append_incident_note(
            incident_id=incident_id,
            note_text=request.note_text,
            author_id=request.author_id,
            timestamp=_now(),
        )
    except ValueError:
        pass  # Legacy append is best-effort

    # Broadcast SSE note_added event
    _notify_sse(incident_id, "note_added", {
        "incident_id": incident_id,
        "note": note,
        "timestamp": _now().isoformat(),
        "author_id": request.author_id,
        "author_role": request.author_role,
    })

    return note


@app.get("/incidents/{incident_id}/notes")
async def get_incident_notes(incident_id: str):
    """Returns all structured notes for an incident, ordered by created_at ascending.
    Includes author_id, author_role, note_type, and timestamps.
    """
    _validate_uuid(incident_id)
    incident = await repo.get_incident(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    notes = await repo.get_notes(incident_id)
    return {"incident_id": incident_id, "notes": notes, "count": len(notes)}


@app.post("/incidents/{incident_id}/correction")
async def record_correction(incident_id: str, request: CorrectionRequest):
    """Gap 9 — NLP confidence threshold + feedback. Records a dispatcher
    correction to NLP-extracted fields. Writes to both structured notes
    table and legacy text blob for audit trail purposes.
    """
    _validate_uuid(incident_id)
    incident = await repo.get_incident(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")

    correction_note = f"[CORRECTION] {request.field}: {request.original_value} -> {request.corrected_value} by {request.dispatcher_id}"

    # Write to structured notes table
    try:
        await repo.add_note(
            incident_id=incident_id,
            note_text=correction_note,
            author_id=request.dispatcher_id,
            author_role="dispatcher",
            note_type="correction",
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # Also append to legacy text blob
    try:
        await repo.append_incident_note(
            incident_id=incident_id,
            note_text=correction_note,
            author_id=request.dispatcher_id,
            timestamp=_now(),
        )
    except ValueError:
        pass

    cache_delete(f"incident:{incident_id}")
    cache_delete(f"incident_full:{incident_id}")
    await log_audit("correction", request.dispatcher_id, incident_id, {"field": request.field})
    return {"status": "recorded"}


@app.post("/incidents/{incident_id}/confirm-pre-arrival")
async def confirm_pre_arrival_instructions(incident_id: str, request: ConfirmPreArrivalRequest):
    """Improvement 3.5 — logs a pre-arrival instruction read-back confirmation
    to the field log. Appends an 'incident_field_log' row with
    action_type='pre_arrival_confirmation'. The dispatcher UI calls this
    after reading instructions to the caller.
    """
    _validate_uuid(incident_id)
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
    """All non-closed incidents ordered by priority severity (P1 first)
    then age (oldest first within the same priority group). Intended for
    a control-room display refreshed on a poll interval by the dispatcher
    or supervisor UI.
    """
    if limit < 1 or limit > 500:
        raise HTTPException(status_code=422, detail="limit must be between 1 and 500")
    cached = cache_get("active_incidents")
    if cached is not None:
        return cached
    result = {"incidents": await repo.get_active_incidents(limit=limit)}
    cache_set("active_incidents", result, ttl_seconds=10)
    return result


@app.get("/dashboard/stats")
async def dashboard_stats(window_hours: int = 24):
    """Incident counts by status and priority_code over a rolling window.
    window_hours defaults to 24; max 168 (7 days) to keep the query
    bounded on a busy system.
    """
    if window_hours < 1 or window_hours > 168:
        raise HTTPException(status_code=422, detail="window_hours must be between 1 and 168")
    cache_key = f"dashboard_stats:{window_hours}"
    cached = cache_get(cache_key)
    if cached is not None:
        return cached
    result = await repo.get_dashboard_stats(window_hours=window_hours)
    cache_set(cache_key, result, ttl_seconds=30)
    return result


@app.get("/dashboard/shift-handover")
async def shift_handover(
    shift_start: str = Query(..., description="ISO datetime — shift start"),
    shift_end: str = Query(..., description="ISO datetime — shift end"),
):
    """Improvement 4.1 — structured shift handover report. Returns counts
    by status/priority, active incidents at shift end, and the top 3
    highest-priority resolved incidents with timeline durations.
    Also returns a plain-text rendering alongside the JSON.
    """
    try:
        start_dt = datetime.fromisoformat(shift_start)
    except ValueError:
        raise HTTPException(status_code=422, detail="shift_start must be a valid ISO datetime")
    try:
        end_dt = datetime.fromisoformat(shift_end)
    except ValueError:
        raise HTTPException(status_code=422, detail="shift_end must be a valid ISO datetime")
    if start_dt >= end_dt:
        raise HTTPException(status_code=422, detail="shift_start must be before shift_end")

    handover = await repo.get_shift_handover(start_dt, end_dt)
    handover["text_rendering"] = repo.render_shift_handover_text(handover)
    return handover


# ─────────────────────────────────────────────────────────────────────────────
# Admin — operational maintenance endpoints
# ─────────────────────────────────────────────────────────────────────────────


@app.post("/admin/purge-expired-incidents", dependencies=[Security(_require_admin_key)])
async def purge_expired_incidents():
    """Triggers the Phase 1.9 retention purge: nullifies caller_location PII
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
    """Hot-reload both dispatch and field protocol registries without a
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
    cache_delete("protocols")
    cache_delete("field_protocols")

    # Rebuild semantic index after reload
    registry.build_semantic_index()
    if semantic_matcher.is_available():
        logger.info("Semantic matcher index rebuilt")
    logger.info("Protocol RAG index rebuilt: %s", "active" if protocol_rag.is_available() else "inactive")

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
    """Returns active and rejected protocols for both dispatch and field
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


@app.get("/admin/protocol-audit", dependencies=[Security(_require_admin_key)])
async def protocol_audit():
    """Implements Epic 8.1 — returns each protocol's governance fields
    (approved_by, approved_date) so the medical director can see
    which protocols need real sign-off. Protocols with placeholder
    values ("Dev Setup", "TBD", etc.) are flagged as pending.
    """
    blocked = DispatchProtocol._BLOCKED_GOVERNANCE_VALUES
    dispatch_protos = []
    for p in registry.list_active():
        approved_lower = p.get("approved_by", "").strip().lower()
        dispatch_protos.append({
            "protocol_id": p["protocol_id"],
            "version": p["version"],
            "approved_by": p["approved_by"],
            "approved_date": p["approved_date"],
            "status": "active",
        })
    for r in registry.list_rejected():
        dispatch_protos.append({
            "file": r["file"],
            "status": "rejected",
            "reason": r["reason"],
        })
    return {
        "dispatch_protocols": dispatch_protos,
        "field_protocols": field_registry.list_active(),
        "blocked_governance_values": sorted(blocked),
    }


@app.get("/admin/governance-status", dependencies=[Security(_require_admin_key)])
async def governance_status():
    """Gap 11 — Protocol governance acknowledgment. Returns a clear JSON
    summary of the governance state, explaining that the system is
    operating in degraded mode because no doctor has signed off on
    the dispatch protocols.
    """
    blocked = DispatchProtocol._BLOCKED_GOVERNANCE_VALUES
    active = registry.list_active()

    # Check if any protocols have real sign-off (not blocked values)
    has_real_signoff = False
    protocols_pending_signoff = []
    for p in active:
        approved_by = (p.get("approved_by") or "").strip()
        if approved_by.lower() not in {v.lower() for v in blocked} and approved_by:
            has_real_signoff = True
        else:
            protocols_pending_signoff.append({
                "protocol_id": p.get("protocol_id"),
                "approved_by": approved_by,
                "status": "pending_signoff",
            })

    return {
        "governance_status": "degraded" if not has_real_signoff else "active",
        "mode": "no_locked_protocols" if not has_real_signoff else "governance_gated",
        "description": (
            "System operating in DEGRADED MODE. No doctor has signed off on "
            "dispatch protocols. Dispatchers must assess manually."
            if not has_real_signoff
            else "System operating in full governance-gated mode with signed-off protocols."
        ),
        "total_dispatch_protocols": len(active),
        "protocols_with_real_signoff": has_real_signoff,
        "protocols_pending_signoff": protocols_pending_signoff,
        "blocked_governance_values": sorted(blocked),
        "action_required": (
            "Medical director must review and sign off on dispatch protocols "
            "before the system can operate in full governance-gated mode."
            if not has_real_signoff
            else None
        ),
    }


@app.post("/incidents/{incident_id}/vitals")
async def add_incident_vitals(incident_id: str, request: AddVitalsRequest):
    _validate_uuid(incident_id)
    incident = await repo.get_incident(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    vitals = request.model_dump(exclude={"recorded_by"})
    result = await repo.add_vitals(incident_id, request.recorded_by, vitals)
    cache_delete(f"incident:{incident_id}")
    cache_delete(f"incident_full:{incident_id}")
    await log_audit("add_vitals", request.recorded_by, incident_id)
    _notify_sse(incident_id, "vitals_added", {"incident_id": incident_id, "recorded_by": request.recorded_by})

    # Epic 7.6: compute scores if age_years or is_pediatric provided
    scores: dict = {}
    if request.age_years is not None or request.is_pediatric:
        age = request.age_years if request.age_years is not None else (5.0 if request.is_pediatric else None)
        if age is not None:
            try:
                pews_result = compute_pews(vitals, age)
                scores["pews"] = {"score": pews_result.score, "risk_level": pews_result.risk_level}
            except ScoringError:
                pass
    shock_vals = {k: vitals.get(k) for k in ("heart_rate", "bp_systolic") if vitals.get(k) is not None}
    if len(shock_vals) == 2:
        try:
            si_result = compute_shock_index(shock_vals)
            scores["shock_index"] = {"score": si_result.score, "risk_level": si_result.risk_level}
        except ScoringError:
            pass

    result["scores"] = scores

    # Gap 6 — Deterioration detection during transport
    # Fetch last 3 vitals records and compare NEWS2 scores
    recent_vitals = await repo.get_vitals_history(incident_id)
    if len(recent_vitals) >= 2:
        # Get the last 3 records (including the one just added)
        last_three = recent_vitals[-3:]
        latest_news2 = result.get("news2_score")
        if latest_news2 is not None:
            # Compare with the previous reading (second to last)
            previous_news2 = last_three[-2].get("news2_score") if len(last_three) >= 2 else None
            if previous_news2 is not None:
                delta = latest_news2 - previous_news2
                if delta >= 3:
                    result["deterioration_alert"] = {
                        "triggered": True,
                        "alert_type": "rapid_deterioration",
                        "message": f"NEWS2 score increased by {delta} points (from {previous_news2} to {latest_news2}). Patient condition may be rapidly deteriorating.",
                        "delta": delta,
                        "prior_score": previous_news2,
                        "current_score": latest_news2,
                    }
                else:
                    result["deterioration_alert"] = {
                        "triggered": False,
                        "delta": delta,
                        "prior_score": previous_news2,
                        "current_score": latest_news2,
                    }

            # Gap 6 — clinical risk alert for NEWS2 >= 7
            if latest_news2 >= 7:
                result["clinical_risk_alert"] = {
                    "triggered": True,
                    "alert_type": "high_clinical_risk",
                    "message": f"NEWS2 score is {latest_news2} (high clinical risk). Urgent clinical review recommended.",
                    "score": latest_news2,
                }
            else:
                result["clinical_risk_alert"] = {
                    "triggered": False,
                    "score": latest_news2,
                }

    return result


@app.post("/incidents/{incident_id}/field-log", dependencies=[Security(_require_role(["field"]))])
async def add_incident_field_log(incident_id: str, request: AddFieldLogRequest):
    _validate_uuid(incident_id)
    incident = await repo.get_incident(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    result = await repo.append_field_log(
        incident_id,
        step_id=request.step_id,
        action_type=request.action_type,
        data=request.data,
        recorded_by=request.recorded_by,
    )

    # Also write to structured notes table for cross-visibility
    note_text = request.data.get("note") or request.action_type
    try:
        await repo.add_note(
            incident_id=incident_id,
            note_text=note_text,
            author_id=request.recorded_by,
            author_role="field",
            note_type="field_log",
        )
    except ValueError:
        pass  # Best-effort: don't fail the field log write if notes write fails

    cache_delete(f"incident:{incident_id}")
    cache_delete(f"incident_full:{incident_id}")
    _notify_sse(incident_id, "field_log_added", {"incident_id": incident_id, "step_id": request.step_id})
    return result


@app.post("/incidents/{incident_id}/medication")
async def add_incident_medication(incident_id: str, request: AddMedicationRequest):
    """Records a drug or item a unit carried, considered, or administered.

    Resolved per Phase 0.5: logging is unconditional — every relevant
    item should be logged regardless of whether it was actually given.
    There is deliberately no allowlist/formulary gate here; an earlier
    version of this endpoint rejected drug names outside a configured
    formulary, which was the wrong model for what was actually wanted.
    `administered` on the request records whether the item was given;
    it does not affect whether the row is written.
    """
    _validate_uuid(incident_id)
    incident = await repo.get_incident(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")

    result = await repo.add_medication_given(
        incident_id,
        drug_name=request.drug_name.strip(),
        dose=request.dose.strip(),
        route=request.route.strip(),
        given_by=request.given_by.strip(),
        administered=request.administered,
    )
    cache_delete(f"incident:{incident_id}")
    cache_delete(f"incident_full:{incident_id}")
    await log_audit("add_medication", request.given_by, incident_id, {"drug_name": request.drug_name})
    _notify_sse(incident_id, "medication_added", {"incident_id": incident_id, "drug_name": request.drug_name})
    return result


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


@app.post("/incidents/{incident_id}/select-protocol")
async def select_dispatch_protocol(incident_id: str, request: SelectDispatchProtocolRequest):
    """Implements Epic 7.3 — manual dispatch protocol assignment.

    When no protocol matches the chief complaint at incident creation,
    the dispatcher can manually select one. This endpoint snapshots the
    selected protocol onto the incident, exactly as create_incident does
    when a match is found. If the incident already has a protocol assigned,
    returns 409 Conflict — never silently overwrite a mid-call protocol.
    """
    _validate_uuid(incident_id)
    incident = await repo.get_incident(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")

    if incident["dispatch_protocol_id"]:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "protocol_already_assigned",
                "message": (
                    f"Incident already has protocol {incident['dispatch_protocol_id']!r} "
                    "assigned. Cannot overwrite a mid-call protocol."
                ),
            },
        )

    protocol = registry.get(request.protocol_id)
    if protocol is None:
        raise HTTPException(
            status_code=404,
            detail=f"Protocol {request.protocol_id!r} is not in the active registry.",
        )

    snapshot = {
        "protocol_id": protocol.protocol_id,
        "version": protocol.version,
        "approved_by": protocol.approved_by,
        "approved_date": protocol.approved_date,
    }
    await repo.set_dispatch_protocol(
        incident_id, protocol.protocol_id, protocol.version, snapshot
    )

    entry_question = get_entry_question(protocol)
    cache_delete(f"incident:{incident_id}")
    cache_delete(f"incident_full:{incident_id}")
    return {
        "protocol_id": protocol.protocol_id,
        "protocol_version": protocol.version,
        "current_question": _question_to_dict(entry_question),
    }


@app.post("/incidents/{incident_id}/field-protocol", dependencies=[Security(_require_role(["field"]))])
async def select_field_protocol(incident_id: str, request: SelectFieldProtocolRequest):
    _validate_uuid(incident_id)
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
    _validate_uuid(incident_id)
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


@app.post("/incidents/{incident_id}/field-protocol/step", dependencies=[Security(_require_role(["field"]))])
async def mark_field_protocol_step(incident_id: str, request: MarkFieldStepRequest):
    """Marks a checklist step's status AND writes the corresponding
    incident_field_log row in the same call — the field UI does not need
    to call /field-log separately for protocol-driven steps. Manual,
    protocol-independent field log entries (radio updates, free-text
    notes, etc.) still go through POST /incidents/{id}/field-log directly,
    unchanged.
    """
    _validate_uuid(incident_id)
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

    matching_step = next((s for s in protocol.steps if s.step_id == request.step_id), None)
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
    """Stores a field-unit GPS ping. The latest location is used by
    route_facility to find the nearest hospital from the unit's
    current position rather than the caller's intake coordinates.
    """
    _validate_uuid(incident_id)
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
    _notify_sse(incident_id, "unit_location_updated", {"incident_id": incident_id, "lat": request.lat, "lon": request.lon})
    return loc


@app.get("/incidents/{incident_id}/unit-location/latest")
async def get_latest_unit_location(incident_id: str):
    """Returns the most recent GPS ping for the field unit on this incident."""
    _validate_uuid(incident_id)
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
    _validate_uuid(incident_id)
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
    _validate_uuid(incident_id)
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
            "field_url": f"{get_field_ui_base_url()}/?incident_id={incident_id}&unit_id={synthetic_unit_id}",
            "message": "Dispatch service unavailable. Unit assigned manually. "
            "Send the field URL to the paramedic.",
        }

    await repo.set_assigned_unit(incident_id, result.assigned_unit_id)
    await repo.update_incident_status(
        incident_id, status=IncidentStatus.DISPATCHED, dispatched_at=_now()
    )
    if result.eta_minutes is not None:
        await repo.set_dispatch_eta(incident_id, result.eta_minutes)

    # EPIC 3.2: suggest a matching field protocol based on priority_code
    _PRIORITY_TO_FIELD_PROTOCOL = {
        "P1_CARDIAC_ARREST": "field_cardiac_arrest_v1",
        "P1_RESPIRATORY_FAILURE": "field_respiratory_distress_v1",
        "P1_AIRWAY_COMPROMISE": "field_respiratory_distress_v1",
        "P2_UNCONSCIOUS_BREATHING": "field_unresponsive_breathing_v1",
        "P2_RESPIRATORY_DISTRESS": "field_respiratory_distress_v1",
        "P1_OBSTETRIC_EMERGENCY": "field_obstetric_emergency_v1",
    }
    suggested_field = _PRIORITY_TO_FIELD_PROTOCOL.get(incident["priority_code"])

    return {
        "assigned": True,
        "dispatch_id": result.dispatch_id,
        "assigned_unit_id": result.assigned_unit_id,
        "eta_minutes": result.eta_minutes,
        "status": result.status,
        "field_url": f"{get_field_ui_base_url()}/?incident_id={incident_id}&unit_id={result.assigned_unit_id}",
        "suggested_field_protocol_id": suggested_field,
    }


def _determine_required_level(triage: dict | None) -> int | None:
    """Determine minimum KEPH facility level from triage enrichment."""
    if not triage:
        return None
    triage_level = (triage.get("triage_level") or "").upper()
    top_dx = (triage.get("top_diagnosis") or "").lower()
    if triage_level == "P1":
        return 4  # P1 critical: Level 4+ with ICU/surgery
    if triage_level == "P2":
        return 3  # P2 emergency: Level 3+ with emergency department
    if triage_level == "P3":
        return 2  # P3 urgent: Level 2+ with basic emergency
    return None


def _determine_required_services(triage: dict | None, existing: list[str]) -> list[str]:
    """Determine required services from triage enrichment if not already specified."""
    if not existing and triage:
        top_dx = (triage.get("top_diagnosis") or "").lower()
        triage_level = (triage.get("triage_level") or "").upper()
        if triage_level == "P1" and any(kw in top_dx for kw in ("cardiac", "heart", "mi")):
            return ["icu", "cardiac_cath"]
        elif triage_level == "P1" and any(kw in top_dx for kw in ("trauma", "hemorrhage", "bleed")):
            return ["surgery", "blood_bank"]
        elif triage_level == "P1":
            return ["icu", "surgery"]
        elif triage_level == "P2":
            return ["emergency"]
    return existing


def _build_recommendation_reason(
    facility: FacilityResult,
    required_services: list[str],
    triage: dict | None,
) -> str:
    """Build a human-readable recommendation reason for the top facility."""
    parts = []
    if facility.level is not None:
        parts.append(f"Level {facility.level} facility")
    if facility.county:
        parts.append(f"in {facility.county}")
    if required_services:
        matched = [s for s in required_services if s in facility.services]
        if matched:
            parts.append(f"has {', '.join(matched)}")
    if facility.is_diverted:
        parts.append(f"DIVERTED: {facility.diversion_reason}")
    if triage and triage.get("top_diagnosis"):
        parts.append(f"matches triage: {triage['top_diagnosis']}")
    if facility.distance_km is not None:
        parts.append(f"{facility.distance_km:.1f} km away")
    return "; ".join(parts) if parts else "Nearest available facility"


@app.post("/incidents/{incident_id}/route-facility")
async def route_facility(incident_id: str, request: RouteFacilityRequest):
    _validate_uuid(incident_id)
    incident = await repo.get_incident(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")

    # Improvement 4.3 — prefer the latest unit location as search origin
    lat = request.lat
    lon = request.lon
    unit_loc = await repo.get_latest_unit_location(incident_id)
    if unit_loc is not None:
        lat = unit_loc["lat"]
        lon = unit_loc["lon"]

    triage = incident.get("triage_enrichment")

    # County referral: use request county or derive from triage
    county = request.county

    # KEPH level routing: determine required level from triage or request
    required_level = request.required_level or _determine_required_level(triage)

    # Required services from triage enrichment or request
    required_services = _determine_required_services(triage, request.required_services or [])

    facilities = await _facility_client.find_nearest(
        lat=lat,
        lon=lon,
        required_services=required_services,
        radius_km=request.radius_km,
        county=county,
        check_diversion=True,
        min_level=required_level,
    )

    # Check Redis for additional diversion status (local overrides)
    active_facilities = []
    for f in facilities:
        diversion_data = cache_get(f"diversion:{f.facility_id}")
        if diversion_data and diversion_data.get("is_diverted"):
            logger.info("Facility %s is diverted: %s", f.facility_id, diversion_data.get("reason"))
            continue
        active_facilities.append(f)
    facilities = active_facilities

    if not facilities:
        return {
            "facilities": [],
            "required_level": required_level,
            "message": "Facility registry unavailable, unconfigured, or returned "
            "no matches. Fall back to locally known facilities — this is NOT "
            "confirmation that no facilities exist nearby.",
        }

    # Build recommendation for top facility
    recommendation_reason = _build_recommendation_reason(facilities[0], required_services, triage)

    facility_list = []
    for i, f in enumerate(facilities):
        # Get stock data from Redis for each facility
        stock_data = cache_get(f"stock:{f.facility_id}") or f.critical_stock or {}

        facility_list.append({
            "facility_id": f.facility_id,
            "name": f.name,
            "lat": f.lat,
            "lon": f.lon,
            "distance_km": f.distance_km,
            "services": f.services,
            "capacity_status": f.capacity_status,
            "level": f.level,
            "county": f.county,
            "is_diverted": False,
            "critical_stock": stock_data,
            "is_recommended": i == 0,
            "recommendation_reason": recommendation_reason if i == 0 else None,
        })

    # Gap 4 — auto-notify receiving facility via report_admission (fire-and-forget)
    async def _auto_notify_facility() -> None:
        try:
            summary_parts = [f"Chief complaint: {incident.get('chief_complaint', 'unknown')}"]
            if incident.get("priority_code"):
                summary_parts.append(f"Priority: {incident['priority_code']}")
            if triage and triage.get("top_diagnosis"):
                summary_parts.append(f"Diagnosis: {triage['top_diagnosis']}")
            await _dispatch_client.report_admission(
                incident_id=incident_id,
                facility_id=facilities[0].facility_id,
                priority_code=incident.get("priority_code", "unknown"),
                summary="; ".join(summary_parts),
            )
        except Exception as exc:
            logger.warning("Auto-notify facility failed for %s: %s", incident_id, exc)

    task = asyncio.create_task(_auto_notify_facility())
    task.add_done_callback(
        lambda t: logger.error("Auto-notify facility task exception: %s", t.exception())
        if t.exception()
        else None
    )

    # Store routed facility info
    await repo.set_routed_facility(incident_id, facilities[0].facility_id, facilities[0].name)

    cache_delete(f"incident:{incident_id}")
    cache_delete(f"incident_full:{incident_id}")
    await log_audit("route_facility", None, incident_id, {"facility_id": facilities[0].facility_id if facilities else None})

    # Check for hazard zones along the route
    hazard_warnings = check_route_hazards(lat, lon, _hazard_zones)

    return {
        "facilities": facility_list,
        "required_level": required_level,
        "required_services": required_services,
        "hazard_warnings": hazard_warnings,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Facility diversion status (Item 5)
# ─────────────────────────────────────────────────────────────────────────────


@app.post("/facilities/{facility_id}/diversion")
async def update_diversion(facility_id: str, request: DiversionRequest):
    """Update a facility's diversion status. Stored in Redis with 1-hour TTL.
    When a facility is diverted, route-facility will exclude it from results.
    """
    diversion_data = {
        "is_diverted": request.is_diverted,
        "reason": request.reason,
        "estimated_resume": request.estimated_resume,
        "updated_at": _now().isoformat(),
    }
    cache_set(f"diversion:{facility_id}", diversion_data, ttl_seconds=3600)
    await log_audit("diversion_update", None, None, {
        "facility_id": facility_id,
        "is_diverted": request.is_diverted,
        "reason": request.reason,
    })
    return {"status": "updated", "facility_id": facility_id, "diversion": diversion_data}


@app.get("/facilities/{facility_id}/diversion")
async def get_diversion(facility_id: str):
    """Get a facility's current diversion status from Redis."""
    data = cache_get(f"diversion:{facility_id}")
    if data is None:
        return {"facility_id": facility_id, "is_diverted": False, "reason": None}
    return {"facility_id": facility_id, **data}


# ─────────────────────────────────────────────────────────────────────────────
# Facility stock availability (Item 8)
# ─────────────────────────────────────────────────────────────────────────────


@app.get("/facilities/{facility_id}/stock")
async def get_facility_stock(facility_id: str):
    """Returns current stock status for a facility from Redis, or fallback data."""
    stock = cache_get(f"stock:{facility_id}")
    if stock is not None:
        return {"facility_id": facility_id, "items": stock, "source": "redis"}

    # Try fallback facilities
    from .external.fallback_facilities import FALLBACK_FACILITIES
    for f in FALLBACK_FACILITIES:
        if f["facility_id"] == facility_id:
            return {"facility_id": facility_id, "items": f.get("critical_stock", {}), "source": "fallback"}

    return {"facility_id": facility_id, "items": {}, "source": "unknown"}


@app.post("/facilities/{facility_id}/stock")
async def update_facility_stock(facility_id: str, request: StockUpdateRequest):
    """Update a facility's stock availability. Stored in Redis with 1-hour TTL."""
    cache_set(f"stock:{facility_id}", request.items, ttl_seconds=3600)
    await log_audit("stock_update", None, None, {
        "facility_id": facility_id,
        "items": request.items,
    })
    return {"status": "updated", "facility_id": facility_id, "items": request.items}


# ─────────────────────────────────────────────────────────────────────────────
# Hazard zone registry (Item 3)
# ─────────────────────────────────────────────────────────────────────────────

# In-memory hazard zone store, seeded from defaults. Can be updated via POST/DELETE.
_hazard_zones: list[dict] = list(DEFAULT_HAZARD_ZONES)


class HazardZoneRequest(BaseModel):
    zone_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = ""
    lat_min: float
    lat_max: float
    lon_min: float
    lon_max: float
    severity: str = "medium"
    active_hours: str = "all"
    days: str = "all"
    source: str = "manual"


@app.get("/hazard-zones")
async def list_hazard_zones():
    """Returns all active hazard zones."""
    return {"zones": _hazard_zones, "count": len(_hazard_zones)}


@app.post("/hazard-zones")
async def upsert_hazard_zone(request: HazardZoneRequest):
    """Add or update a hazard zone. If a zone with the same zone_id exists,
    it is replaced. Dispatchers use this to maintain road condition overlays.
    """
    zone_data = request.model_dump()
    # Remove existing zone with same ID if present
    global _hazard_zones
    _hazard_zones = [z for z in _hazard_zones if z["zone_id"] != request.zone_id]
    _hazard_zones.append(zone_data)
    await log_audit("hazard_zone_upsert", None, None, {"zone_id": request.zone_id})
    return {"status": "updated", "zone": zone_data, "total_zones": len(_hazard_zones)}


@app.delete("/hazard-zones/{zone_id}")
async def delete_hazard_zone(zone_id: str):
    """Remove a hazard zone by its ID."""
    global _hazard_zones
    before = len(_hazard_zones)
    _hazard_zones = [z for z in _hazard_zones if z["zone_id"] != zone_id]
    if len(_hazard_zones) == before:
        raise HTTPException(status_code=404, detail=f"Hazard zone {zone_id!r} not found")
    await log_audit("hazard_zone_delete", None, None, {"zone_id": zone_id})
    return {"status": "deleted", "zone_id": zone_id, "total_zones": len(_hazard_zones)}


@app.post("/incidents/{incident_id}/report-admission")
async def report_admission(incident_id: str, request: ReportAdmissionRequest):
    _validate_uuid(incident_id)
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
    await repo.set_routed_facility(incident_id, request.facility_id, request.facility_id)

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


@app.post("/incidents/{incident_id}/notify-next-of-kin")
async def notify_next_of_kin(incident_id: str):
    _validate_uuid(incident_id)
    incident = await repo.get_incident(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")

    nok_phone = incident.get('next_of_kin_phone')
    nok_name = incident.get('next_of_kin_name')

    if not nok_phone:
        raise HTTPException(status_code=400, detail="No next-of-kin phone number on record")

    facility = incident.get('routed_facility_name') or 'hospital'
    ref = incident_id[:8]
    message = f"Your family member ({nok_name or 'relative'}) has been taken to {facility}. Reference: {ref}. Contact the dispatch center for updates."

    # TODO: Integrate Africa's Talking SMS API when configured
    # For now, log the notification intent
    logger.info("Next-of-kin notification: %s -> %s: %s", ref, nok_phone, message)
    await log_audit('notify_next_of_kin', incident_id=incident_id, details={'phone': nok_phone, 'name': nok_name})

    return {
        "status": "sent",
        "message": message,
        "phone": nok_phone,
        "recipient_name": nok_name,
        "note": "SMS delivery requires Africa's Talking API configuration",
    }


# ─────────────────────────────────────────────────────────────────────────────
# Multi-casualty incident support (Item 6)
# ─────────────────────────────────────────────────────────────────────────────


class AddCasualtyRequest(BaseModel):
    chief_complaint: str | None = None
    triage_score: str | None = None
    age_estimate: int | None = None
    gender: str | None = None
    vitals_summary: dict | None = None
    status: str = "pending"


class UpdateCasualtyRequest(BaseModel):
    chief_complaint: str | None = None
    triage_score: str | None = None
    age_estimate: int | None = None
    gender: str | None = None
    vitals_summary: dict | None = None
    status: str | None = None


@app.post("/incidents/{incident_id}/casualties")
async def add_casualty(incident_id: str, request: AddCasualtyRequest):
    """Add a casualty slot to a multi-casualty incident."""
    _validate_uuid(incident_id)
    incident = await repo.get_incident(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    result = await repo.add_casualty(
        incident_id=incident_id,
        chief_complaint=request.chief_complaint,
        triage_score=request.triage_score,
        age_estimate=request.age_estimate,
        gender=request.gender,
        vitals_summary=request.vitals_summary,
        status=request.status,
    )
    cache_delete(f"incident:{incident_id}")
    cache_delete(f"incident_full:{incident_id}")
    await log_audit("add_casualty", None, incident_id, {"casualty_id": result["id"]})
    return result


@app.get("/incidents/{incident_id}/casualties")
async def list_casualties(incident_id: str):
    """List all casualties for a multi-casualty incident."""
    _validate_uuid(incident_id)
    incident = await repo.get_incident(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    casualties = await repo.list_casualties(incident_id)
    return {
        "incident_id": incident_id,
        "casualties": casualties,
        "count": len(casualties),
        "is_multi_casualty": len(casualties) > 1,
    }


@app.patch("/incidents/{incident_id}/casualties/{casualty_id}")
async def update_casualty(incident_id: str, casualty_id: int, request: UpdateCasualtyRequest):
    """Update a casualty's triage score, vitals, or status."""
    _validate_uuid(incident_id)
    incident = await repo.get_incident(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    update_fields = {k: v for k, v in request.model_dump().items() if v is not None}
    if not update_fields:
        raise HTTPException(status_code=422, detail="No fields to update")
    result = await repo.update_casualty(incident_id, casualty_id, **update_fields)
    if result is None:
        raise HTTPException(status_code=404, detail="Casualty not found")
    cache_delete(f"incident:{incident_id}")
    cache_delete(f"incident_full:{incident_id}")
    await log_audit("update_casualty", None, incident_id, {"casualty_id": casualty_id})
    return result


@app.delete("/incidents/{incident_id}/casualties/{casualty_id}")
async def delete_casualty(incident_id: str, casualty_id: int):
    """Remove a casualty from a multi-casualty incident."""
    _validate_uuid(incident_id)
    incident = await repo.get_incident(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    deleted = await repo.delete_casualty(incident_id, casualty_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Casualty not found")
    cache_delete(f"incident:{incident_id}")
    cache_delete(f"incident_full:{incident_id}")
    await log_audit("delete_casualty", None, incident_id, {"casualty_id": casualty_id})
    return {"status": "deleted", "casualty_id": casualty_id}


# ─────────────────────────────────────────────────────────────────────────────
# Incident pattern reporting (Item 9)
# ─────────────────────────────────────────────────────────────────────────────


@app.get("/reports/weekly")
async def weekly_report(
    county: str | None = Query(None, description="Filter by county (substring match on location text)"),
    start_date: str | None = Query(None, description="ISO date — report start (default: 7 days ago)"),
    end_date: str | None = Query(None, description="ISO date — report end (default: now)"),
):
    """Returns aggregate incident statistics for a date range, useful for
    weekly pattern analysis and resource planning.
    """
    start_dt = None
    end_dt = None
    if start_date:
        try:
            start_dt = datetime.fromisoformat(start_date)
        except ValueError:
            raise HTTPException(status_code=422, detail="start_date must be a valid ISO date")
    if end_date:
        try:
            end_dt = datetime.fromisoformat(end_date)
        except ValueError:
            raise HTTPException(status_code=422, detail="end_date must be a valid ISO date")
    if start_dt and end_dt and start_dt > end_dt:
        raise HTTPException(status_code=422, detail="start_date must not be after end_date")

    return await repo.get_weekly_report(county=county, start_date=start_dt, end_date=end_dt)


@app.get("/reports/weekly/text")
async def weekly_report_text(
    county: str | None = Query(None, description="Filter by county"),
    start_date: str | None = Query(None, description="ISO date — report start"),
    end_date: str | None = Query(None, description="ISO date — report end"),
):
    """Returns a plain-text weekly report suitable for email or PDF generation."""
    start_dt = None
    end_dt = None
    if start_date:
        try:
            start_dt = datetime.fromisoformat(start_date)
        except ValueError:
            raise HTTPException(status_code=422, detail="start_date must be a valid ISO date")
    if end_date:
        try:
            end_dt = datetime.fromisoformat(end_date)
        except ValueError:
            raise HTTPException(status_code=422, detail="end_date must be a valid ISO date")
    if start_dt and end_dt and start_dt > end_dt:
        raise HTTPException(status_code=422, detail="start_date must not be after end_date")

    report = await repo.get_weekly_report(county=county, start_date=start_dt, end_date=end_dt)
    text = _render_weekly_report_text(report)
    return PlainTextResponse(content=text)


def _render_weekly_report_text(report: dict) -> str:
    lines = []
    lines.append("=" * 60)
    lines.append("WEEKLY INCIDENT PATTERN REPORT")
    lines.append("=" * 60)
    lines.append(f"Period: {report['period']['start']} to {report['period']['end']}")
    lines.append(f"Total incidents: {report['total_incidents']}")
    lines.append(f"Average response time: {report['avg_response_time_minutes'] or 'N/A'} minutes")
    lines.append("")

    lines.append("INCIDENTS BY SUB-COUNTY")
    for area, count in sorted(report["by_sub_county"].items(), key=lambda x: x[1], reverse=True):
        lines.append(f"  {area}: {count}")
    lines.append("")

    lines.append("INCIDENTS BY CHIEF COMPLAINT")
    for complaint, count in sorted(report["by_complaint"].items(), key=lambda x: x[1], reverse=True):
        lines.append(f"  {complaint}: {count}")
    lines.append("")

    lines.append("INCIDENTS BY HOUR")
    for hour, count in sorted(report["by_hour"].items()):
        if count > 0:
            lines.append(f"  {hour}:00 — {count}")
    lines.append("")

    lines.append(f"Busiest hours: {', '.join(report['busiest_hours']) or 'N/A'}")
    lines.append("")

    lines.append("TOP PRESENTATIONS")
    for p in report["top_presentations"]:
        lines.append(f"  {p['complaint']}: {p['count']} ({p['pct']}%)")
    lines.append("")
    lines.append("=" * 60)
    return "\n".join(lines)


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


# ─────────────────────────────────────────────────────────────────────────────
# Epic 6 — Authentication endpoints
# ─────────────────────────────────────────────────────────────────────────────




class DispatcherLoginRequest(BaseModel):
    username: str = Field(min_length=1)
    pin: str = Field(min_length=4)
    role: str | None = None


@app.post("/auth/dispatcher-login")
async def dispatcher_login(request: DispatcherLoginRequest):
    """Epic 6.1 — dispatcher login. Returns an HMAC-signed session token.
    In development mode, any credentials are accepted.
    In production, credentials are validated against DISPATCHER_CREDENTIALS.
    """
    result = verify_credentials(request.username, request.pin)
    if result is None:
        raise HTTPException(
            status_code=401,
            detail={"error": "invalid_credentials", "message": "Invalid username or PIN."},
        )
    # Use provided role, or fall back to credential-stored role, or default to dispatcher
    role = request.role or result.get("role", "dispatcher")
    token = generate_session_token(result["username"], role)
    await log_audit("login", result["username"], None, {"role": role})
    return {
        "dispatcher_id": result["username"],
        "role": role,
        "session_token": token,
        "expires_in_hours": get_session_token_expiry_hours(),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Epic 1.5 — E911 / AML webhook receiver
# ─────────────────────────────────────────────────────────────────────────────


class E911PushRequest(BaseModel):
    caller_number: str | None = None
    lat: float
    lon: float
    accuracy_m: float | None = None
    incident_id: str | None = None
    chief_complaint: str | None = None


@app.post("/intake/e911-push")
async def e911_push(request: E911PushRequest):
    """Epic 1.5 — accepts an E911 or AML location push payload.
    If incident_id is provided, updates that incident's location.
    If absent, creates a pre-populated intake record.
    """
    if request.incident_id:
        incident = await repo.get_incident(request.incident_id)
        if incident is None:
            raise HTTPException(status_code=404, detail="Incident not found")
        async with repo.get_session() as session:
            await session.execute(
                sa_update(_IncidentModel)
                .where(_IncidentModel.incident_id == uuid.UUID(request.incident_id))
                .values(
                    caller_location_lat=request.lat,
                    caller_location_lon=request.lon,
                    location_accuracy_m=request.accuracy_m,
                )
            )
            await session.commit()
        return {"incident_id": request.incident_id, "created": False}
    else:
        caller_text = f"E911 push: {request.lat}, {request.lon}"
        cc = request.chief_complaint or "E911 automated push — pending dispatcher review"
        incident = await repo.create_incident(
            chief_complaint=cc,
            caller_location_lat=request.lat,
            caller_location_lon=request.lon,
            caller_location_text=caller_text,
        )
        # Attempt protocol matching (Epic 1.5 requirement)
        match = registry.match_by_chief_complaint(cc)
        incident_id = incident["incident_id"]
        if match is not None:
            protocol = match.protocol
            snapshot = {
                "protocol_id": protocol.protocol_id,
                "version": protocol.version,
                "approved_by": protocol.approved_by,
                "approved_date": protocol.approved_date,
            }
            await repo.set_dispatch_protocol(incident_id, protocol.protocol_id, protocol.version, snapshot)
        # Store accuracy_m on the incident
        async with repo.get_session() as session:
            await session.execute(
                sa_update(_IncidentModel)
                .where(_IncidentModel.incident_id == uuid.UUID(incident["incident_id"]))
                .values(location_accuracy_m=request.accuracy_m)
            )
            await session.commit()
        return {"incident_id": incident["incident_id"], "created": True, "protocol_matched": match is not None}


# ─────────────────────────────────────────────────────────────────────────────
# Epic 1.2 — NLP Entity Extraction (MedSpaCy + regex fallback)
# ─────────────────────────────────────────────────────────────────────────────

from .nlp_extractor import extract_clinical_entities as _extract_entities


class ExtractEntitiesRequest(BaseModel):
    transcript: str = Field(min_length=1, max_length=5000)
    incident_id: str | None = None


@app.post("/triage/extract-entities")
async def extract_entities(request: ExtractEntitiesRequest):
    """Epic 1.2 — clinical NLP entity extraction.

    Uses MedSpaCy (TargetMatcher + ConText) when available, with regex
    as a complementary layer for structured values (BP, HR, RR, GCS)
    and as a fallback when the model is not loaded.

    Returns: location_text, chief_complaint_suggestion, vitals, entities,
    confidence. Never returns 500 — always degrades gracefully.
    """
    # PHI handling: never log the transcript text itself
    result = _extract_entities(request.transcript)

    # Gap 9 — NLP confidence threshold check
    confidence = result["confidence"]
    auto_populate_safe = confidence >= 0.4

    return {
        "location_text": result["location_text"],
        "lat": None,
        "lon": None,
        "chief_complaint_suggestion": result["chief_complaint_suggestion"],
        "vitals": result["vitals"],
        "entities": result["entities"],
        "confidence": result["confidence"],
        "degraded_mode": result["degraded_mode"],
        "auto_populate_safe": auto_populate_safe,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Epic 1.4 — Call Transcription Persistence
# ─────────────────────────────────────────────────────────────────────────────


class AppendTranscriptRequest(BaseModel):
    speaker: str = Field(min_length=1)  # "dispatcher" | "caller"
    text: str = Field(min_length=1)


@app.patch("/incidents/{incident_id}/transcript")
async def append_transcript(incident_id: str, request: AppendTranscriptRequest):
    """Epic 1.4 — append-only transcript persistence. Each call appends a
    timestamped chunk with speaker label.
    """
    _validate_uuid(incident_id)
    incident = await repo.get_incident(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    timestamp = _now().isoformat()
    chunk = f"[{timestamp}] {request.speaker}: {request.text}"
    async with repo.get_session() as session:
        inc = await session.get(_IncidentModel, uuid.UUID(incident_id))
        if inc.transcript_text:
            inc.transcript_text = inc.transcript_text + "\n" + chunk
        else:
            inc.transcript_text = chunk
        await session.commit()
        await session.refresh(inc)
    return {"incident_id": incident_id, "transcript_length": len(inc.transcript_text or "")}


# ─────────────────────────────────────────────────────────────────────────────
# Epic 7.6 — Scoring endpoints (PEWS, RTS, Shock Index)
# ─────────────────────────────────────────────────────────────────────────────


class ComputeScoringRequest(BaseModel):
    scoring_type: str = Field(min_length=1)  # "pews" | "rts" | "shock_index"
    vitals: dict = Field(default_factory=dict)
    age_years: float | None = None


@app.post("/scoring/compute")
async def compute_scoring(request: ComputeScoringRequest):
    """Epic 7.6 — compute PEWS, RTS, or Shock Index from vitals."""
    try:
        if request.scoring_type == "pews":
            if request.age_years is None:
                raise HTTPException(status_code=422, detail="age_years required for PEWS")
            result = compute_pews(request.vitals, request.age_years)
        elif request.scoring_type == "rts":
            result = compute_revised_trauma_score(request.vitals)
        elif request.scoring_type == "shock_index":
            result = compute_shock_index(request.vitals)
        else:
            raise HTTPException(
                status_code=422,
                detail=f"Unknown scoring_type: {request.scoring_type}. Must be pews, rts, or shock_index.",
            )
    except ScoringError as exc:
        raise HTTPException(status_code=422, detail={"error": "scoring_error", "message": str(exc), "missing_fields": exc.missing_fields})

    return {
        "scoring_type": request.scoring_type,
        "score": result.score,
        "risk_level": result.risk_level,
        "escalation_required": result.escalation_required,
        "component_scores": result.component_scores,
        "trigger": result.trigger,
        "source_guideline": result.source_guideline,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Epic 7.2 — PII purge status & scheduler wiring
# ─────────────────────────────────────────────────────────────────────────────


@app.get("/admin/purge-status", dependencies=[Security(_require_admin_key)])
async def purge_status():
    """Epic 7.2 — returns last purge run info."""
    return {
        "retention_days": get_incident_retention_days(),
        "scheduler_enabled": is_database_configured(),
    }


@app.get('/admin/logs')
async def get_logs(lines: int = 100):
    """Returns the last N lines from the application log file."""
    try:
        with open(_log_file, 'r') as f:
            all_lines = f.readlines()
        return {'lines': all_lines[-lines:], 'total': len(all_lines), 'file': _log_file}
    except FileNotFoundError:
        return {'lines': [], 'total': 0, 'file': _log_file}


# ─────────────────────────────────────────────────────────────────────────────
# Admin — cache health, system status, audit log, protocol detail, facilities
# ─────────────────────────────────────────────────────────────────────────────


@app.get("/admin/cache-health", dependencies=[Security(_require_admin_key)])
async def admin_cache_health():
    return cache_health()


@app.get("/admin/system-status", dependencies=[Security(_require_admin_key)])
async def admin_system_status():
    db_pool = await repo.get_db_pool_stats()
    db_ok = await check_database() if is_database_configured() else False
    redis_health = cache_health()
    incident_counts = await repo.get_incident_counts_by_status() if db_ok else {}

    active_protocols = registry.list_active()
    rejected_protocols = registry.list_rejected()
    active_field = field_registry.list_active()
    rejected_field = field_registry.list_rejected()

    uptime_seconds = (datetime.now(UTC) - _start_time).total_seconds()

    mem_info = {}
    try:
        import resource
        usage = resource.getrusage(resource.RUSAGE_SELF)
        mem_info = {"rss_mb": round(usage.ru_maxrss / 1024, 1)}
    except Exception:
        pass

    return {
        "database": {
            "status": "ok" if db_ok else "error",
            "pool": db_pool,
        },
        "redis": redis_health,
        "protocols": {
            "dispatch_active": len(active_protocols),
            "dispatch_rejected": len(rejected_protocols),
            "field_active": len(active_field),
            "field_rejected": len(rejected_field),
        },
        "incidents_by_status": incident_counts,
        "total_incidents": sum(incident_counts.values()),
        "memory": mem_info,
        "uptime_seconds": round(uptime_seconds, 1),
        "uptime_human": _format_uptime(uptime_seconds),
    }


def _format_uptime(seconds: float) -> str:
    days = int(seconds // 86400)
    hours = int((seconds % 86400) // 3600)
    minutes = int((seconds % 3600) // 60)
    if days > 0:
        return f"{days}d {hours}h {minutes}m"
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


@app.get("/admin/audit-log", dependencies=[Security(_require_admin_key)])
async def admin_audit_log(
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    incident_id: str | None = Query(None),
):
    if incident_id:
        _validate_uuid(incident_id)
    events = await repo.get_audit_events(limit=limit, offset=offset, incident_id=incident_id)
    return {"events": events, "count": len(events), "limit": limit, "offset": offset}


@app.get("/admin/protocols/detail", dependencies=[Security(_require_admin_key)])
async def admin_protocols_detail():
    active = registry.list_active()
    rejected = registry.list_rejected()
    field_active = field_registry.list_active()
    field_rejected = field_registry.list_rejected()

    dispatch_details = []
    for p in active:
        proto = registry.get(p["protocol_id"])
        if proto:
            dispatch_details.append({
                "protocol_id": proto.protocol_id,
                "version": proto.version,
                "approved_by": proto.approved_by,
                "approved_date": proto.approved_date,
                "entry_question": _question_to_dict(get_entry_question(proto)) if proto.questions else None,
                "question_count": len(proto.questions),
                "questions": [
                    {"question_id": qid, "text": q.text, "answer_type": q.answer_type}
                    for qid, q in proto.questions.items()
                ],
            })

    return {
        "dispatch": {"active": dispatch_details, "rejected": rejected},
        "field": {"active": field_active, "rejected": field_rejected},
    }


@app.get("/admin/facilities", dependencies=[Security(_require_admin_key)])
async def admin_facilities():
    try:
        facilities = await _facility_client.find_nearest(lat=0, lon=0, radius_km=99999)
        return {
            "status": "connected" if facilities is not None else "unavailable",
            "facilities": [
                {"facility_id": f.facility_id, "name": f.name, "services": f.services}
                for f in (facilities or [])
            ],
            "count": len(facilities) if facilities else 0,
        }
    except Exception as exc:
        return {"status": "error", "error": str(exc), "facilities": [], "count": 0}


# ─────────────────────────────────────────────────────────────────────────────
# Epic 4.1 — SSE endpoint (Server-Sent Events)
# ─────────────────────────────────────────────────────────────────────────────

# In-memory SSE queues per incident (ephemeral, lost on restart)
_sse_queues: dict[str, list[asyncio.Queue]] = {}


def _notify_sse(incident_id: str, event_type: str, data: dict) -> None:
    """Push an event to all connected SSE clients for an incident."""
    queues = _sse_queues.get(incident_id, [])
    message = f"event: {event_type}\ndata: {_json.dumps(data)}\n\n"
    dead: list[asyncio.Queue] = []
    for q in queues:
        try:
            q.put_nowait(message)
        except asyncio.QueueFull:
            dead.append(q)
    for d in dead:
        queues.remove(d)


@app.get("/incidents/{incident_id}/stream")
async def stream_incident_events(incident_id: str, token: str = Query(...)):
    """Epic 4.1 — SSE endpoint for live incident events.
    Events: vitals_added, medication_added, status_changed, unit_location_updated, field_log_added, note_added.
    Authenticated by handoff token.
    """
    _validate_uuid(incident_id)
    if not verify_handoff_token(incident_id, token):
        raise HTTPException(status_code=403, detail="Invalid or expired token.")
    incident = await repo.get_incident(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")

    queue: asyncio.Queue = asyncio.Queue(maxsize=100)
    if incident_id not in _sse_queues:
        _sse_queues[incident_id] = []
    _sse_queues[incident_id].append(queue)

    async def event_generator():
        keepalive_count = 0
        try:
            # Send initial connection event
            yield f"event: connected\ndata: {{}}\n\n"
            while True:
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield message
                    keepalive_count = 0
                except asyncio.TimeoutError:
                    keepalive_count += 1
                    # Only check DB terminal status every 5th keepalive (~75s)
                    if keepalive_count % 5 == 0:
                        incident_check = await repo.get_incident(incident_id)
                        if incident_check and incident_check["status"] in ("handoff_complete", "closed"):
                            _notify_sse(incident_id, "stream_closed", {})
                            break
                    yield ": keepalive\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            if queue in _sse_queues.get(incident_id, []):
                _sse_queues[incident_id].remove(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )
