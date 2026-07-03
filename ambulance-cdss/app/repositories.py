"""
app/repositories.py

Data access layer for the incident model.

create_incident, append_dispatch_answer, append_field_log, add_vitals,
add_medication_given, log_guidance_lookup — one function per write path,
each doing exactly one insert plus whatever inline computation belongs at
write time (e.g. NEWS2/GCS computed and stored alongside vitals, not
recomputed later).

get_incident_full assembles everything for handoff-document generation in
a single call — this is the one place Phase 5 (handoff summary) needs to
read from.

purge_expired_incidents enforces the Phase 1.9 retention policy, resolved
at 30 days.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select, update

from .config import get_incident_retention_days
from .db import get_session
from .models import (
    GuidanceLookupLog,
    Incident,
    IncidentDispatchLog,
    IncidentFieldLog,
    IncidentMedicationGiven,
    IncidentStatus,
    IncidentUnitLocation,
    IncidentVitals,
)
from .scoring.scorers import ScoringError, compute_gcs_total, compute_news2

logger = logging.getLogger(__name__)


class InvalidStatusTransitionError(ValueError):
    """Raised when a requested status transition is not permitted."""

    def __init__(
        self,
        current_status: IncidentStatus,
        requested_status: IncidentStatus,
        allowed_statuses: set[IncidentStatus],
    ):
        self.current_status = current_status
        self.requested_status = requested_status
        self.allowed_statuses = allowed_statuses
        super().__init__(
            f"Invalid transition from {current_status.value!r} to "
            f"{requested_status.value!r}. "
            f"Allowed: {[s.value for s in allowed_statuses]}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Incident lifecycle
# ─────────────────────────────────────────────────────────────────────────────

async def create_incident(
    chief_complaint: str,
    caller_location_lat: Optional[float] = None,
    caller_location_lon: Optional[float] = None,
    caller_location_text: Optional[str] = None,
) -> Dict[str, Any]:
    async with get_session() as session:
        incident = Incident(
            chief_complaint=chief_complaint,
            caller_location_lat=caller_location_lat,
            caller_location_lon=caller_location_lon,
            caller_location_text=caller_location_text,
            status=IncidentStatus.RECEIVED,
        )
        session.add(incident)
        await session.commit()
        await session.refresh(incident)
    return _incident_to_dict(incident)


async def set_dispatch_protocol(
    incident_id: str,
    protocol_id: str,
    protocol_version: str,
    protocol_snapshot: Dict[str, Any],
) -> None:
    """
    Snapshot the full protocol content into the incident at the moment a
    call starts using it — see docs/GOVERNANCE.md. Guarantees the incident
    remains reproducible even if the protocol registry is edited later.
    """
    async with get_session() as session:
        await session.execute(
            update(Incident)
            .where(Incident.incident_id == uuid.UUID(incident_id))
            .values(
                dispatch_protocol_id=protocol_id,
                dispatch_protocol_version=protocol_version,
                dispatch_protocol_snapshot=protocol_snapshot,
            )
        )
        await session.commit()


async def set_field_protocol(
    incident_id: str,
    protocol_id: str,
    protocol_version: str,
) -> None:
    """
    Records which FieldProtocol checklist the field unit selected for this
    incident. Deliberately no snapshot argument — see
    app/models.py::Incident.field_protocol_id docstring. The selection is
    a convenience/orientation aid for the field runner, not a governance
    artifact; incident_field_log is the source of truth for what was
    actually done.
    """
    async with get_session() as session:
        await session.execute(
            update(Incident)
            .where(Incident.incident_id == uuid.UUID(incident_id))
            .values(
                field_protocol_id=protocol_id,
                field_protocol_version=protocol_version,
            )
        )
        await session.commit()


VALID_TRANSITIONS: Dict[IncidentStatus, set[IncidentStatus]] = {
    IncidentStatus.RECEIVED: {IncidentStatus.DISPATCHED, IncidentStatus.CLOSED},
    IncidentStatus.DISPATCHED: {IncidentStatus.ON_SCENE, IncidentStatus.CLOSED},
    IncidentStatus.ON_SCENE: {IncidentStatus.TRANSPORTING, IncidentStatus.CLOSED},
    IncidentStatus.TRANSPORTING: {IncidentStatus.HANDOFF_COMPLETE, IncidentStatus.CLOSED},
    IncidentStatus.HANDOFF_COMPLETE: {IncidentStatus.CLOSED},
    IncidentStatus.CLOSED: set(),
}


async def update_incident_status(
    incident_id: str,
    status: IncidentStatus,
    **timestamp_fields: Any,
) -> None:
    """
    timestamp_fields: any of dispatched_at, on_scene_at, transporting_at,
    handoff_complete_at, closed_at — pass the field name with a datetime
    value to stamp it alongside the status change in one write.

    Enforces valid status transitions via VALID_TRANSITIONS. Raises
    InvalidStatusTransitionError if the requested transition is not
    permitted from the incident's current status.
    """
    async with get_session() as session:
        incident = await session.scalar(
            select(Incident).where(Incident.incident_id == uuid.UUID(incident_id))
        )
        if incident is None:
            raise ValueError(f"Incident {incident_id} not found")
        current_status = incident.status
        allowed = VALID_TRANSITIONS.get(current_status, set())
        if status not in allowed:
            raise InvalidStatusTransitionError(current_status, status, allowed)
        values: Dict[str, Any] = {"status": status}
        values.update(timestamp_fields)
        await session.execute(
            update(Incident)
            .where(Incident.incident_id == uuid.UUID(incident_id))
            .values(**values)
        )
        await session.commit()


async def set_dispatch_outcome(
    incident_id: str,
    priority_code: str,
    recommended_unit_type: str,
) -> None:
    async with get_session() as session:
        await session.execute(
            update(Incident)
            .where(Incident.incident_id == uuid.UUID(incident_id))
            .values(
                priority_code=priority_code,
                recommended_unit_type=recommended_unit_type,
            )
        )
        await session.commit()


async def set_assigned_unit(incident_id: str, unit_id: str) -> None:
    async with get_session() as session:
        await session.execute(
            update(Incident)
            .where(Incident.incident_id == uuid.UUID(incident_id))
            .values(assigned_unit_id=unit_id)
        )
        await session.commit()


async def set_dispatch_eta(incident_id: str, eta_minutes: float) -> None:
    """Persist the ETA from the dispatch service response (Improvement 3.1)."""
    async with get_session() as session:
        await session.execute(
            update(Incident)
            .where(Incident.incident_id == uuid.UUID(incident_id))
            .values(eta_minutes=eta_minutes)
        )
        await session.commit()


async def set_routed_facility(
    incident_id: str, facility_id: str, facility_name: str
) -> None:
    async with get_session() as session:
        await session.execute(
            update(Incident)
            .where(Incident.incident_id == uuid.UUID(incident_id))
            .values(routed_facility_id=facility_id, routed_facility_name=facility_name)
        )
        await session.commit()


async def set_triage_enrichment(
    incident_id: str,
    enrichment_dict: Dict[str, Any],
) -> None:
    """Persist triage enrichment result from the Triage Ranker service.
    Written asynchronously by a background create_task in create_incident.
    """
    async with get_session() as session:
        await session.execute(
            update(Incident)
            .where(Incident.incident_id == uuid.UUID(incident_id))
            .values(triage_enrichment=enrichment_dict)
        )
        await session.commit()


async def list_incidents(
    status: Optional[str] = None,
    priority_code: Optional[str] = None,
    assigned_unit_id: Optional[str] = None,
    created_after: Optional[datetime] = None,
    created_before: Optional[datetime] = None,
    chief_complaint_contains: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    """
    List incidents with optional filters. Excludes purged incidents
    (pii_purged_at IS NOT NULL). Ordered by created_at DESC (most recent
    first). Returns a flat list of incident dicts — no nested children.
    """
    if limit < 1 or limit > 200:
        raise ValueError("limit must be between 1 and 200")
    if offset < 0:
        raise ValueError("offset must be non-negative")
    if created_after is not None and created_before is not None:
        if created_after > created_before:
            raise ValueError("created_after must not be after created_before")
    if chief_complaint_contains is not None:
        stripped = chief_complaint_contains.strip()
        if len(stripped) < 2:
            raise ValueError("chief_complaint_contains must be at least 2 characters")
        chief_complaint_contains = stripped

    async with get_session() as session:
        stmt = select(Incident).where(Incident.pii_purged_at.is_(None))
        if status is not None:
            stmt = stmt.where(Incident.status == status)
        if priority_code is not None:
            stmt = stmt.where(Incident.priority_code == priority_code)
        if assigned_unit_id is not None:
            stmt = stmt.where(Incident.assigned_unit_id == assigned_unit_id)
        if created_after is not None:
            stmt = stmt.where(Incident.created_at >= created_after)
        if created_before is not None:
            stmt = stmt.where(Incident.created_at <= created_before)
        if chief_complaint_contains is not None:
            stmt = stmt.where(
                Incident.chief_complaint.ilike(f"%{chief_complaint_contains}%")
            )
        stmt = stmt.order_by(Incident.created_at.desc()).limit(limit).offset(offset)
        rows = (await session.scalars(stmt)).all()
    return [_incident_to_dict(r) for r in rows]


async def get_incident(incident_id: str) -> Optional[Dict[str, Any]]:
    async with get_session() as session:
        incident = await session.scalar(
            select(Incident).where(Incident.incident_id == uuid.UUID(incident_id))
        )
    return _incident_to_dict(incident) if incident else None


# ─────────────────────────────────────────────────────────────────────────────
# Dispatch log (Mode 1 — append-only, immutable)
# ─────────────────────────────────────────────────────────────────────────────

async def append_dispatch_answer(
    incident_id: str,
    question_id: str,
    question_text: str,
    answer: str,
    protocol_version: str,
    is_backtrack: bool = False,
) -> Dict[str, Any]:
    async with get_session() as session:
        row = IncidentDispatchLog(
            incident_id=uuid.UUID(incident_id),
            question_id=question_id,
            question_text=question_text,
            answer=answer,
            protocol_version=protocol_version,
            is_backtrack=is_backtrack,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
    return _dispatch_log_to_dict(row)


async def get_dispatch_log(incident_id: str) -> List[Dict[str, Any]]:
    async with get_session() as session:
        rows = (
            await session.scalars(
                select(IncidentDispatchLog)
                .where(IncidentDispatchLog.incident_id == uuid.UUID(incident_id))
                .order_by(IncidentDispatchLog.timestamp.asc())
            )
        ).all()
    return [_dispatch_log_to_dict(r) for r in rows]


# ─────────────────────────────────────────────────────────────────────────────
# Field log (append-only)
# ─────────────────────────────────────────────────────────────────────────────

async def append_field_log(
    incident_id: str,
    step_id: str,
    action_type: str,
    data: Dict[str, Any],
    recorded_by: str,
) -> Dict[str, Any]:
    async with get_session() as session:
        row = IncidentFieldLog(
            incident_id=uuid.UUID(incident_id),
            step_id=step_id,
            action_type=action_type,
            data=data,
            recorded_by=recorded_by,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
    return _field_log_to_dict(row)


async def get_field_log(incident_id: str) -> List[Dict[str, Any]]:
    async with get_session() as session:
        rows = (
            await session.scalars(
                select(IncidentFieldLog)
                .where(IncidentFieldLog.incident_id == uuid.UUID(incident_id))
                .order_by(IncidentFieldLog.timestamp.asc())
            )
        ).all()
    return [_field_log_to_dict(r) for r in rows]


# ─────────────────────────────────────────────────────────────────────────────
# Vitals (with inline-computed scores)
# ─────────────────────────────────────────────────────────────────────────────

async def add_vitals(
    incident_id: str,
    recorded_by: str,
    vitals: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Computes NEWS2 and GCS total at write time (if the required inputs are
    present) and stores the computed values alongside the raw vitals — see
    module docstring for why this is not recomputed retroactively.

    Improvement 4 — after writing, compares the new NEWS2 score against
    the most recent prior vitals row for this incident and returns a
    trend_alert dict alongside the vitals row. The trend_alert is always
    present in the response so the field UI can rely on its key existence.
    """
    news2_result = None
    news2_missing_fields: list[str] = []
    try:
        news2_result = compute_news2(vitals)
    except ScoringError as exc:
        news2_missing_fields = exc.missing_fields

    gcs_total = None
    if all(k in vitals and vitals[k] is not None for k in ("gcs_eye", "gcs_verbal", "gcs_motor")):
        gcs_total = compute_gcs_total(
            vitals["gcs_eye"], vitals["gcs_verbal"], vitals["gcs_motor"]
        )

    new_news2_score = news2_result.score if news2_result else None
    new_news2_risk = news2_result.risk_level if news2_result else None

    async with get_session() as session:
        row = IncidentVitals(
            incident_id=uuid.UUID(incident_id),
            recorded_by=recorded_by,
            respiratory_rate=vitals.get("respiratory_rate"),
            spo2=vitals.get("spo2"),
            spo2_scale=vitals.get("spo2_scale"),
            supplemental_o2=vitals.get("supplemental_o2"),
            bp_systolic=vitals.get("bp_systolic"),
            bp_diastolic=vitals.get("bp_diastolic"),
            heart_rate=vitals.get("heart_rate"),
            consciousness=vitals.get("consciousness"),
            temperature=vitals.get("temperature"),
            gcs_eye=vitals.get("gcs_eye"),
            gcs_verbal=vitals.get("gcs_verbal"),
            gcs_motor=vitals.get("gcs_motor"),
            news2_score=new_news2_score,
            news2_risk_level=new_news2_risk,
            gcs_total=gcs_total,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)

        # Improvement 4 — query the most recent prior vitals row for trend
        prior_news2_score = None
        prior_news2_risk = None
        if new_news2_score is not None:
            prior_row = await session.scalar(
                select(IncidentVitals)
                .where(
                    IncidentVitals.incident_id == uuid.UUID(incident_id),
                    IncidentVitals.id != row.id,
                    IncidentVitals.news2_score.is_not(None),
                )
                .order_by(IncidentVitals.recorded_at.desc())
                .limit(1)
            )
            if prior_row is not None:
                prior_news2_score = prior_row.news2_score
                prior_news2_risk = prior_row.news2_risk_level

        # GCS trend — query the most recent prior vitals row with a GCS score
        prior_gcs = None
        if gcs_total is not None:
            gcs_prior_row = await session.scalar(
                select(IncidentVitals)
                .where(
                    IncidentVitals.incident_id == uuid.UUID(incident_id),
                    IncidentVitals.id != row.id,
                    IncidentVitals.gcs_total.is_not(None),
                )
                .order_by(IncidentVitals.recorded_at.desc())
                .limit(1)
            )
            if gcs_prior_row is not None:
                prior_gcs = gcs_prior_row.gcs_total

    # Compute trend_alert (NEWS2)
    trend_alert = _compute_news2_trend(
        new_news2_score, new_news2_risk, prior_news2_score, prior_news2_risk
    )

    # Compute gcs_trend_alert
    gcs_trend_alert = _compute_gcs_trend(gcs_total, prior_gcs)

    result = _vitals_to_dict(row)
    result["trend_alert"] = trend_alert
    result["gcs_trend_alert"] = gcs_trend_alert
    result["news2_missing_fields"] = news2_missing_fields
    return result


def _news2_risk_level(score: int) -> str:
    """Maps a numeric NEWS2 score to its risk level category."""
    if score >= 7:
        return "high"
    if score >= 5:
        return "medium"
    return "low"


def _risk_level_index(level: str) -> int:
    """Maps risk level string to numeric index for boundary comparison."""
    return {"low": 0, "low-medium": 1, "medium": 2, "high": 3}.get(level.lower(), 0)


def _compute_news2_trend(
    new_score: Optional[int],
    new_risk: Optional[str],
    prior_score: Optional[int],
    prior_risk: Optional[str],
) -> Dict[str, Any]:
    """
    Computes the NEWS2 trend alert dict. Always returns a complete dict
    with all keys present so the field UI can rely on its existence.
    """
    if new_score is None or prior_score is None:
        return {
            "trend": "no_prior_data",
            "delta": None,
            "prior_news2": prior_score,
            "new_news2": new_score,
            "crossed_risk_boundary": False,
        }

    delta = new_score - prior_score

    if delta >= 3:
        trend = "rapid_deterioration"
    elif delta >= 1:
        trend = "deteriorating"
    elif delta == 0:
        trend = "stable"
    else:
        trend = "improving"

    # Check if risk level crossed a boundary (low -> medium or medium -> high)
    crossed_boundary = False
    if prior_risk and new_risk:
        prior_idx = _risk_level_index(prior_risk)
        new_idx = _risk_level_index(new_risk)
        crossed_boundary = new_idx > prior_idx

    return {
        "trend": trend,
        "delta": delta,
        "prior_news2": prior_score,
        "new_news2": new_score,
        "crossed_risk_boundary": crossed_boundary,
    }


def _gcs_severity_band(gcs_total: int) -> str:
    """
    Maps a GCS total score to its severity band:
    - mild (13–15)
    - moderate (9–12)
    - severe (≤8)
    """
    if gcs_total <= 8:
        return "severe"
    if gcs_total <= 12:
        return "moderate"
    return "mild"


def _compute_gcs_trend(
    new_gcs: Optional[int],
    prior_gcs: Optional[int],
) -> Dict[str, Any]:
    """
    Computes the GCS trend alert dict. Always returns a complete dict
    with all keys present so the field UI can rely on its existence.
    GCS is inverted from NEWS2: lower is worse (rapid deterioration
    is a large negative delta).
    """
    if new_gcs is None or prior_gcs is None:
        return {
            "trend": "no_prior_data",
            "delta": None,
            "prior_gcs": prior_gcs,
            "new_gcs": new_gcs,
            "crossed_severity_threshold": False,
        }

    delta = new_gcs - prior_gcs

    if delta <= -3:
        trend = "rapid_deterioration"
    elif delta <= -1:
        trend = "deteriorating"
    elif delta == 0:
        trend = "stable"
    else:
        trend = "improving"

    # Check if severity band crossed a threshold (new is worse than prior)
    crossed_threshold = False
    if new_gcs != prior_gcs:
        new_band = _gcs_severity_band(new_gcs)
        prior_band = _gcs_severity_band(prior_gcs)
        # crossed_threshold when the new score is in a worse (lower) band
        band_order = {"mild": 0, "moderate": 1, "severe": 2}
        crossed_threshold = band_order[new_band] > band_order[prior_band]

    return {
        "trend": trend,
        "delta": delta,
        "prior_gcs": prior_gcs,
        "new_gcs": new_gcs,
        "crossed_severity_threshold": crossed_threshold,
    }


async def get_vitals_history(incident_id: str) -> List[Dict[str, Any]]:
    async with get_session() as session:
        rows = (
            await session.scalars(
                select(IncidentVitals)
                .where(IncidentVitals.incident_id == uuid.UUID(incident_id))
                .order_by(IncidentVitals.recorded_at.asc())
            )
        ).all()
    return [_vitals_to_dict(r) for r in rows]


# ─────────────────────────────────────────────────────────────────────────────
# Medications/items given (Phase 0.5 — resolved: log everything
# relevant, regardless of whether it was administered; see
# IncidentMedicationGiven.administered for the per-row record of that)
# ─────────────────────────────────────────────────────────────────────────────

async def add_medication_given(
    incident_id: str,
    drug_name: str,
    dose: str,
    route: str,
    given_by: str,
    administered: bool = True,
) -> Dict[str, Any]:
    async with get_session() as session:
        row = IncidentMedicationGiven(
            incident_id=uuid.UUID(incident_id),
            drug_name=drug_name,
            dose=dose,
            route=route,
            given_by=given_by,
            administered=administered,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
    return _medication_to_dict(row)


async def get_medications_given(incident_id: str) -> List[Dict[str, Any]]:
    async with get_session() as session:
        rows = (
            await session.scalars(
                select(IncidentMedicationGiven)
                .where(IncidentMedicationGiven.incident_id == uuid.UUID(incident_id))
                .order_by(IncidentMedicationGiven.given_at.asc())
            )
        ).all()
    return [_medication_to_dict(r) for r in rows]


# ─────────────────────────────────────────────────────────────────────────────
# Guidance lookup (Mode 2 — separate from dispatch log, see docs/GOVERNANCE.md)
# ─────────────────────────────────────────────────────────────────────────────

async def log_guidance_lookup(
    incident_id: str,
    query_text: str,
    result_summary: str,
    dispatcher_id: str,
    question_id: Optional[str] = None,
) -> Dict[str, Any]:
    async with get_session() as session:
        row = GuidanceLookupLog(
            incident_id=uuid.UUID(incident_id),
            question_id=question_id,
            query_text=query_text,
            result_summary=result_summary,
            dispatcher_id=dispatcher_id,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
    return _guidance_log_to_dict(row)


async def get_guidance_lookups(incident_id: str) -> List[Dict[str, Any]]:
    async with get_session() as session:
        rows = (
            await session.scalars(
                select(GuidanceLookupLog)
                .where(GuidanceLookupLog.incident_id == uuid.UUID(incident_id))
                .order_by(GuidanceLookupLog.timestamp.asc())
            )
        ).all()
    return [_guidance_log_to_dict(r) for r in rows]


# ─────────────────────────────────────────────────────────────────────────────
# Incident notes (Improvement 5 — append-only dispatcher annotation)
# ─────────────────────────────────────────────────────────────────────────────

async def append_incident_note(
    incident_id: str,
    note_text: str,
    author_id: str,
    timestamp: datetime,
) -> Dict[str, Any]:
    """
    Appends a timestamped free-text note to Incident.notes. The note
    is formatted as "[ISO timestamp] author: text" and appended on a
    new line if notes already exist. This is append-only by design —
    overwriting would destroy prior dispatcher notes, which are part of
    the legally complete incident record.
    """
    cleaned_text = note_text.strip()
    if not cleaned_text:
        raise ValueError("Note text cannot be empty")
    cleaned_author = author_id.strip()
    if not cleaned_author:
        raise ValueError("Author ID cannot be empty")

    new_line = f"[{timestamp.isoformat()}] {cleaned_author}: {cleaned_text}"

    async with get_session() as session:
        incident = await session.scalar(
            select(Incident).where(Incident.incident_id == uuid.UUID(incident_id))
        )
        if incident is None:
            raise ValueError(f"Incident {incident_id} not found")
        if incident.notes:
            incident.notes = incident.notes + "\n" + new_line
        else:
            incident.notes = new_line
        await session.commit()
        await session.refresh(incident)
    return _incident_to_dict(incident)


# ─────────────────────────────────────────────────────────────────────────────
# Full incident assembly (Phase 1.8 exit criterion — used by Phase 5 handoff)
# ─────────────────────────────────────────────────────────────────────────────

async def get_incident_full(incident_id: str) -> Optional[Dict[str, Any]]:
    """
    Single call assembling everything needed for handoff-document
    generation: incident root record, full dispatch transcript, full field
    log, vitals history, medications given, and guidance lookups used.
    The four child queries run concurrently via asyncio.gather.
    """
    incident = await get_incident(incident_id)
    if incident is None:
        return None

    dispatch_log, field_log, vitals_history, medications_given, guidance_lookups = (
        await asyncio.gather(
            get_dispatch_log(incident_id),
            get_field_log(incident_id),
            get_vitals_history(incident_id),
            get_medications_given(incident_id),
            get_guidance_lookups(incident_id),
        )
    )

    return {
        "incident": incident,
        "dispatch_log": dispatch_log,
        "field_log": field_log,
        "vitals_history": vitals_history,
        "medications_given": medications_given,
        "guidance_lookups": guidance_lookups,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Structured incident timeline (Improvement 3)
# ─────────────────────────────────────────────────────────────────────────────

async def get_incident_timeline(incident_id: str) -> Optional[Dict[str, Any]]:
    """
    Returns a single chronologically-ordered list of every event that
    happened during an incident: dispatch answers, field actions, vitals
    records, medications, and guidance lookups, interleaved by timestamp.
    Calls get_incident_full() once and merges the sub-arrays in Python.

    Each row: {"timestamp": str (ISO), "event_type": str, "source": str, "data": dict}
    where event_type is one of: dispatch_answer, field_action, vitals,
    medication, guidance_lookup; source is "dispatch", "field", or "system".
    """
    full = await get_incident_full(incident_id)
    if full is None:
        return None

    events: List[Dict[str, Any]] = []

    for row in full["dispatch_log"]:
        events.append({
            "timestamp": row.get("timestamp"),
            "event_type": "dispatch_answer",
            "source": "dispatch",
            "data": row,
        })

    for row in full["field_log"]:
        events.append({
            "timestamp": row.get("timestamp"),
            "event_type": "field_action",
            "source": "field",
            "data": row,
        })

    for row in full["vitals_history"]:
        events.append({
            "timestamp": row.get("recorded_at"),
            "event_type": "vitals",
            "source": "field",
            "data": row,
        })

    for row in full["medications_given"]:
        events.append({
            "timestamp": row.get("given_at"),
            "event_type": "medication",
            "source": "field",
            "data": row,
        })

    for row in full["guidance_lookups"]:
        events.append({
            "timestamp": row.get("timestamp"),
            "event_type": "guidance_lookup",
            "source": "system",
            "data": row,
        })

    # Sort by timestamp ascending. Rows with None timestamp sort last.
    # Tie-break by event_type alphabetically for deterministic ordering.
    events.sort(key=lambda e: (e["timestamp"] is None, e["timestamp"] or "", e["event_type"]))

    return {
        "incident_id": full["incident"]["incident_id"],
        "events": events,
        "event_count": len(events),
    }


# ─────────────────────────────────────────────────────────────────────────────
# ETA tracking (Improvement 3.1)
# ─────────────────────────────────────────────────────────────────────────────

async def set_dispatch_eta(incident_id: str, eta_minutes: float) -> None:
    async with get_session() as session:
        await session.execute(
            update(Incident)
            .where(Incident.incident_id == uuid.UUID(incident_id))
            .values(eta_minutes=eta_minutes)
        )
        await session.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Answer correction window (Improvement 4.2)
# ─────────────────────────────────────────────────────────────────────────────

async def get_dispatch_log_entry(log_id: str) -> Optional[Dict[str, Any]]:
    """Fetch a single dispatch log row by its UUID."""
    async with get_session() as session:
        row = await session.scalar(
            select(IncidentDispatchLog)
            .where(IncidentDispatchLog.id == uuid.UUID(log_id))
        )
    return _dispatch_log_to_dict(row) if row else None


async def correct_dispatch_answer(
    log_id: str,
    corrected_answer: str,
    new_log_id: uuid.UUID,
) -> None:
    """
    Marks the original dispatch log row as superseded by pointing
    superseded_by at the new (corrected) row's UUID. The original row
    is never deleted — the full correction history is preserved in the
    append-only log.
    """
    async with get_session() as session:
        await session.execute(
            update(IncidentDispatchLog)
            .where(IncidentDispatchLog.id == uuid.UUID(log_id))
            .values(superseded_by=new_log_id)
        )
        await session.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Unit location (Improvement 4.3)
# ─────────────────────────────────────────────────────────────────────────────

async def add_unit_location(
    incident_id: str,
    lat: float,
    lon: float,
    recorded_by: str,
    timestamp: Optional[datetime] = None,
) -> Dict[str, Any]:
    async with get_session() as session:
        row = IncidentUnitLocation(
            incident_id=uuid.UUID(incident_id),
            lat=lat,
            lon=lon,
            recorded_by=recorded_by,
        )
        if timestamp is not None:
            row.recorded_at = timestamp
        session.add(row)
        await session.commit()
        await session.refresh(row)
    return {
        "id": str(row.id),
        "incident_id": str(row.incident_id),
        "lat": row.lat,
        "lon": row.lon,
        "recorded_by": row.recorded_by,
        "recorded_at": row.recorded_at.isoformat() if row.recorded_at else None,
    }


async def get_latest_unit_location(incident_id: str) -> Optional[Dict[str, Any]]:
    async with get_session() as session:
        row = await session.scalar(
            select(IncidentUnitLocation)
            .where(IncidentUnitLocation.incident_id == uuid.UUID(incident_id))
            .order_by(IncidentUnitLocation.recorded_at.desc())
            .limit(1)
        )
    if row is None:
        return None
    return {
        "id": str(row.id),
        "incident_id": str(row.incident_id),
        "lat": row.lat,
        "lon": row.lon,
        "recorded_by": row.recorded_by,
        "recorded_at": row.recorded_at.isoformat() if row.recorded_at else None,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Shift handover report (Improvement 4.1)
# ─────────────────────────────────────────────────────────────────────────────

async def get_shift_handover(
    shift_start: datetime,
    shift_end: datetime,
) -> Dict[str, Any]:
    """
    Assembles a shift handover report covering all incidents within the
    [shift_start, shift_end) window. Returns structured data only — the
    caller is responsible for text rendering via render_shift_handover_text.
    """
    async with get_session() as session:
        rows = (
            await session.scalars(
                select(Incident)
                .where(
                    Incident.created_at >= shift_start,
                    Incident.created_at < shift_end,
                )
                .order_by(Incident.created_at.asc())
            )
        ).all()

    now = datetime.now(timezone.utc)
    total = len(rows)
    by_status: Dict[str, int] = {}
    by_priority: Dict[str, int] = {}

    active_statuses = {
        IncidentStatus.RECEIVED,
        IncidentStatus.DISPATCHED,
        IncidentStatus.ON_SCENE,
        IncidentStatus.TRANSPORTING,
    }

    active_at_shift_end = []
    resolved: List[Dict[str, Any]] = []

    for row in rows:
        sk = str(row.status)
        by_status[sk] = by_status.get(sk, 0) + 1
        pk = row.priority_code or "no_outcome_yet"
        by_priority[pk] = by_priority.get(pk, 0) + 1

        d = _incident_to_dict(row)

        if row.status in active_statuses:
            active_at_shift_end.append(d)
        elif row.status in {IncidentStatus.HANDOFF_COMPLETE, IncidentStatus.CLOSED}:
            # Compute timeline durations where timestamps are available
            d2s = None
            s2h = None
            if row.dispatched_at and row.on_scene_at:
                d2s = round(
                    (row.on_scene_at - row.dispatched_at).total_seconds() / 60, 1
                )
            if row.on_scene_at and row.handoff_complete_at:
                s2h = round(
                    (row.handoff_complete_at - row.on_scene_at).total_seconds() / 60, 1
                )
            resolved.append({
                "incident_id": d["incident_id"],
                "priority_code": d["priority_code"],
                "chief_complaint": d["chief_complaint"],
                "assigned_unit_id": d["assigned_unit_id"],
                "dispatch_to_scene_minutes": d2s,
                "scene_to_handoff_minutes": s2h,
            })

    # Top 3 resolved by priority severity, then by dispatch_to_scene_minutes
    resolved_sorted = sorted(
        resolved,
        key=lambda r: (
            _priority_sort(r["priority_code"]),
            r["dispatch_to_scene_minutes"] or 9999,
        ),
    )
    top_resolved = resolved_sorted[:3]

    return {
        "shift_start": shift_start.isoformat(),
        "shift_end": shift_end.isoformat(),
        "total_incidents": total,
        "by_status": by_status,
        "by_priority": by_priority,
        "active_at_shift_end": active_at_shift_end,
        "active_at_shift_end_count": len(active_at_shift_end),
        "top_resolved": top_resolved,
    }


def render_shift_handover_text(handover: Dict[str, Any]) -> str:
    """Plain-text rendering of a shift handover report dict."""
    lines = []
    lines.append("=" * 72)
    lines.append("SHIFT HANDOVER REPORT")
    lines.append("=" * 72)
    lines.append(f"Shift start: {handover['shift_start']}")
    lines.append(f"Shift end:   {handover['shift_end']}")
    lines.append(f"Total incidents: {handover['total_incidents']}")
    lines.append("")

    lines.append("By status:")
    if handover["by_status"]:
        for status, count in sorted(handover["by_status"].items()):
            lines.append(f"  {status}: {count}")
    else:
        lines.append("  (none)")
    lines.append("")

    lines.append("By priority:")
    if handover["by_priority"]:
        for priority, count in sorted(handover["by_priority"].items()):
            lines.append(f"  {priority}: {count}")
    else:
        lines.append("  (none)")
    lines.append("")

    lines.append(
        f"Active at shift end: {handover['active_at_shift_end_count']}"
    )
    if handover["active_at_shift_end"]:
        for inc in handover["active_at_shift_end"]:
            overdue_marker = " [OVERDUE]" if inc.get("overdue") else ""
            lines.append(
                f"  {inc['incident_id']} | {inc.get('priority_code','?')} | "
                f"{inc['status']} | unit: {inc.get('assigned_unit_id','unassigned')}"
                f"{overdue_marker}"
            )
    else:
        lines.append("  (none)")
    lines.append("")

    lines.append("Top resolved incidents (P1 first, fastest response first):")
    if handover["top_resolved"]:
        for inc in handover["top_resolved"]:
            d2s = f"{inc['dispatch_to_scene_minutes']}min" if inc.get("dispatch_to_scene_minutes") is not None else "?"
            s2h = f"{inc['scene_to_handoff_minutes']}min" if inc.get("scene_to_handoff_minutes") is not None else "?"
            lines.append(
                f"  {inc['incident_id']} | {inc.get('priority_code','?')} | "
                f"dispatch→scene: {d2s} | scene→handoff: {s2h}"
            )
    else:
        lines.append("  (none)")
    lines.append("=" * 72)
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Retention / purge (Phase 1.9 — resolved: 30 days, see
# get_incident_retention_days in app/config.py)
# ─────────────────────────────────────────────────────────────────────────────

async def purge_expired_incidents() -> Dict[str, int]:
    """
    Purges PII fields (caller_location_*) from incidents closed longer
    than INCIDENT_RETENTION_DAYS ago (resolved per Phase 1.9: 30 days),
    stamping pii_purged_at. Still a no-op if a deployment deliberately
    overrides INCIDENT_RETENTION_DAYS to 0 or below.

    Not yet scheduled (cron / periodic task) — call manually or wire to a
    scheduler.
    """
    retention_days = get_incident_retention_days()
    if retention_days <= 0:
        logger.info(
            "purge_expired_incidents: no-op. INCIDENT_RETENTION_DAYS is %d "
            "(<=0).",
            retention_days,
        )
        return {"purged": 0, "skipped_reason": "retention_disabled"}

    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    async with get_session() as session:
        rows = (
            await session.scalars(
                select(Incident).where(
                    Incident.closed_at.is_not(None),
                    Incident.closed_at < cutoff,
                    Incident.pii_purged_at.is_(None),
                )
            )
        ).all()
        for incident in rows:
            incident.caller_location_lat = None
            incident.caller_location_lon = None
            incident.caller_location_text = None
            incident.pii_purged_at = datetime.now(timezone.utc)
        await session.commit()

    logger.info("purge_expired_incidents: purged %d incidents", len(rows))
    return {"purged": len(rows)}


# ─────────────────────────────────────────────────────────────────────────────
# Dashboard queries (Phase 6)
# ─────────────────────────────────────────────────────────────────────────────

# Priority ordering for sort: P1_* most urgent, ROUTE_REASSESS least.
_PRIORITY_SORT_KEY: dict[str | None, int] = {
    None: 99,
    "ROUTE_REASSESS": 98,
    "P3_TRAUMA_MINOR": 30,
    "P2_AIRWAY_PARTIAL": 21,
    "P2_TRAUMA_HIGH_MECHANISM": 20,
    "P1_AIRWAY_COMPLETE": 10,
    "P1_TRAUMA_AIRWAY_COMPROMISE": 9,
    "P1_TRAUMA_SEVERE_BLEEDING": 8,
}
_DEFAULT_PRIORITY_SORT = 50  # unknown P1 codes sort higher than P2/P3


def _priority_sort(code: str | None) -> int:
    if code is None:
        return _PRIORITY_SORT_KEY[None]
    if code in _PRIORITY_SORT_KEY:
        return _PRIORITY_SORT_KEY[code]
    # Unknown P1_ codes sort highest of the numerically significant group
    if code.startswith("P1_"):
        return 5
    if code.startswith("P2_"):
        return 25
    if code.startswith("P3_"):
        return 35
    return _DEFAULT_PRIORITY_SORT


async def get_active_incidents(limit: int = 100) -> List[Dict[str, Any]]:
    """
    All non-closed incidents, for the dashboard active-incidents view.
    Sorted by priority severity (P1 first) then by created_at ascending
    (older incidents first within the same priority group).
    """
    non_closed_statuses = [
        s for s in IncidentStatus if s != IncidentStatus.CLOSED
    ]
    async with get_session() as session:
        rows = (
            await session.scalars(
                select(Incident)
                .where(Incident.status.in_(non_closed_statuses))
                .order_by(Incident.created_at.asc())
                .limit(limit)
            )
        ).all()
    # Sort in Python after fetch: priority_code contains a string, not an
    # integer, so a DB ORDER BY would be alphabetical rather than severity-
    # ordered. The limit above keeps the in-memory sort cheap.
    rows_sorted = sorted(rows, key=lambda r: (_priority_sort(r.priority_code), r.created_at))
    return [_incident_to_dict(r) for r in rows_sorted]


async def get_dashboard_stats(window_hours: int = 24) -> Dict[str, Any]:
    """
    Counts by status and by priority_code over the last `window_hours`.
    Used by GET /dashboard/stats to give a control-room overview.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)
    async with get_session() as session:
        # All incidents within the window — one query, aggregate in Python
        # to avoid complex CASE/GROUP BY across DB engine differences.
        rows = (
            await session.scalars(
                select(Incident)
                .where(Incident.created_at >= cutoff)
            )
        ).all()

    by_status: dict[str, int] = {}
    by_priority: dict[str, int] = {}
    total = len(rows)
    for row in rows:
        status_key = str(row.status)
        by_status[status_key] = by_status.get(status_key, 0) + 1
        priority_key = row.priority_code or "no_outcome_yet"
        by_priority[priority_key] = by_priority.get(priority_key, 0) + 1

    active_count = sum(
        1 for r in rows
        if r.status not in (IncidentStatus.CLOSED, IncidentStatus.HANDOFF_COMPLETE)
    )
    critical_count = sum(
        1 for r in rows
        if r.priority_code and r.priority_code.startswith("P1_")
    )

    return {
        "window_hours": window_hours,
        "total_incidents": total,
        "active_incidents": active_count,
        "critical_priority_incidents": critical_count,
        "by_status": by_status,
        "by_priority": by_priority,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Serialisation helpers
# ─────────────────────────────────────────────────────────────────────────────

def _incident_to_dict(row: Incident) -> Dict[str, Any]:
    # Compute ETA-derived fields from persisted columns
    eta_minutes = getattr(row, "eta_minutes", None)
    dispatched_at = row.dispatched_at
    estimated_on_scene_at = None
    overdue = False

    if dispatched_at is not None and eta_minutes is not None:
        estimated_on_scene_at = dispatched_at + timedelta(minutes=eta_minutes)
        if row.status == IncidentStatus.DISPATCHED:
            overdue = datetime.now(timezone.utc) > estimated_on_scene_at

    return {
        "incident_id": str(row.incident_id),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "status": row.status,
        "priority_code": row.priority_code,
        "chief_complaint": row.chief_complaint,
        "caller_location_lat": row.caller_location_lat,
        "caller_location_lon": row.caller_location_lon,
        "caller_location_text": row.caller_location_text,
        "dispatch_protocol_id": row.dispatch_protocol_id,
        "dispatch_protocol_version": row.dispatch_protocol_version,
        "field_protocol_id": row.field_protocol_id,
        "field_protocol_version": row.field_protocol_version,
        "assigned_unit_id": row.assigned_unit_id,
        "recommended_unit_type": row.recommended_unit_type,
        "routed_facility_id": row.routed_facility_id,
        "routed_facility_name": row.routed_facility_name,
        "dispatched_at": row.dispatched_at.isoformat() if row.dispatched_at else None,
        "on_scene_at": row.on_scene_at.isoformat() if row.on_scene_at else None,
        "transporting_at": row.transporting_at.isoformat() if row.transporting_at else None,
        "handoff_complete_at": row.handoff_complete_at.isoformat() if row.handoff_complete_at else None,
        "closed_at": row.closed_at.isoformat() if row.closed_at else None,
        "pii_purged_at": row.pii_purged_at.isoformat() if row.pii_purged_at else None,
        "notes": row.notes,
        "triage_enrichment": getattr(row, "triage_enrichment", None),
        "eta_minutes": eta_minutes,
        "estimated_on_scene_at": estimated_on_scene_at.isoformat() if estimated_on_scene_at else None,
        "overdue": overdue,
    }


def _dispatch_log_to_dict(row: IncidentDispatchLog) -> Dict[str, Any]:
    return {
        "id": str(row.id),
        "question_id": row.question_id,
        "question_text": row.question_text,
        "answer": row.answer,
        "protocol_version": row.protocol_version,
        "is_backtrack": row.is_backtrack,
        "superseded_by": str(row.superseded_by) if row.superseded_by else None,
        "timestamp": row.timestamp.isoformat() if row.timestamp else None,
    }


def _field_log_to_dict(row: IncidentFieldLog) -> Dict[str, Any]:
    return {
        "id": str(row.id),
        "step_id": row.step_id,
        "action_type": row.action_type,
        "data": row.data,
        "recorded_by": row.recorded_by,
        "timestamp": row.timestamp.isoformat() if row.timestamp else None,
    }


def _vitals_to_dict(row: IncidentVitals) -> Dict[str, Any]:
    return {
        "id": str(row.id),
        "recorded_at": row.recorded_at.isoformat() if row.recorded_at else None,
        "recorded_by": row.recorded_by,
        "respiratory_rate": row.respiratory_rate,
        "spo2": row.spo2,
        "spo2_scale": row.spo2_scale,
        "supplemental_o2": row.supplemental_o2,
        "bp_systolic": row.bp_systolic,
        "bp_diastolic": row.bp_diastolic,
        "heart_rate": row.heart_rate,
        "consciousness": row.consciousness,
        "temperature": row.temperature,
        "gcs_eye": row.gcs_eye,
        "gcs_verbal": row.gcs_verbal,
        "gcs_motor": row.gcs_motor,
        "news2_score": row.news2_score,
        "news2_risk_level": row.news2_risk_level,
        "gcs_total": row.gcs_total,
    }


def _medication_to_dict(row: IncidentMedicationGiven) -> Dict[str, Any]:
    return {
        "id": str(row.id),
        "drug_name": row.drug_name,
        "dose": row.dose,
        "route": row.route,
        "administered": row.administered,
        "given_at": row.given_at.isoformat() if row.given_at else None,
        "given_by": row.given_by,
    }


def _guidance_log_to_dict(row: GuidanceLookupLog) -> Dict[str, Any]:
    return {
        "id": str(row.id),
        "question_id": row.question_id,
        "query_text": row.query_text,
        "result_summary": row.result_summary,
        "dispatcher_id": row.dispatcher_id,
        "timestamp": row.timestamp.isoformat() if row.timestamp else None,
    }


def _unit_location_to_dict(row: IncidentUnitLocation) -> Dict[str, Any]:
    return {
        "id": str(row.id),
        "incident_id": str(row.incident_id),
        "lat": row.lat,
        "lon": row.lon,
        "recorded_by": row.recorded_by,
        "recorded_at": row.recorded_at.isoformat() if row.recorded_at else None,
    }


# (No additional functions here — all functions are defined above.)
