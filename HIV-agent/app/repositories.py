"""
Database repositories for all persistent storage operations.

Fixes applied:
- upsert_evidence_graph: replaced N+1 per-row SELECTs with bulk
  INSERT ON CONFLICT DO UPDATE so seeding 50 nodes + 200 edges is one
  round-trip per entity type, not 250 sequential queries.
- put_embedding_cache: already uses ON CONFLICT; unchanged.
- get_embedding_cache_with_eviction: TTL-based cleanup on every write.
  Rows older than CDSS_EMBEDDING_CACHE_TTL_DAYS (default 30) are deleted.
"""

from __future__ import annotations

import os
import re
import uuid
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import select

from .db import get_session


# ─────────────────────────────────────────────────────────────────────────────
# Audit logs
# ─────────────────────────────────────────────────────────────────────────────

async def write_audit_log_db(
    event_type: str,
    session_id: str,
    query_id: str,
    disease: str,
    feedback_type: str,
    data: dict,
) -> None:
    from .models import AuditLog

    async with get_session() as session:
        session.add(
            AuditLog(
                event_type=event_type,
                session_id=session_id,
                query_id=query_id,
                disease=disease,
                feedback_type=feedback_type,
                log_data=data,
            )
        )
        await session.commit()


async def read_audit_logs_db(
    *,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    session_id: Optional[str] = None,
    disease: Optional[str] = None,
    feedback_type: Optional[str] = None,
    page: int = 1,
    limit: int = 50,
) -> Dict[str, Any]:
    from sqlalchemy import and_, func, select

    from .models import AuditLog

    filters = []
    if start_date:
        filters.append(AuditLog.timestamp >= f"{start_date} 00:00:00")
    if end_date:
        filters.append(AuditLog.timestamp <= f"{end_date} 23:59:59")
    if session_id:
        filters.append(AuditLog.session_id == session_id)
    if disease:
        filters.append(AuditLog.disease.ilike(f"%{disease}%"))
    if feedback_type:
        filters.append(AuditLog.feedback_type == feedback_type)

    where_clause = and_(*filters) if filters else None
    async with get_session() as session:
        total_stmt = select(func.count()).select_from(AuditLog)
        rows_stmt = (
            select(AuditLog)
            .order_by(AuditLog.timestamp.desc())
            .limit(limit)
            .offset((page - 1) * limit)
        )
        if where_clause is not None:
            total_stmt = total_stmt.where(where_clause)
            rows_stmt = rows_stmt.where(where_clause)

        total = await session.scalar(total_stmt)
        rows = (await session.scalars(rows_stmt)).all()

    return {
        "logs": [
            {
                "id": row.id,
                "timestamp": row.timestamp.isoformat() if row.timestamp else None,
                "event_type": row.event_type,
                "session_id": row.session_id,
                "query_id": row.query_id,
                "disease": row.disease,
                "feedback_type": row.feedback_type,
                "log_data": row.log_data,
            }
            for row in rows
        ],
        "total": total or 0,
        "page": page,
        "limit": limit,
    }


async def count_audit_logs_db() -> int:
    from sqlalchemy import func, select

    from .models import AuditLog

    async with get_session() as session:
        return int(
            await session.scalar(select(func.count()).select_from(AuditLog)) or 0
        )


# ─────────────────────────────────────────────────────────────────────────────
# Feedback
# ─────────────────────────────────────────────────────────────────────────────

async def write_feedback_db(
    session_id: str,
    message_id: str,
    feedback_type: str,
    note: str = "",
    correction: str = "",
    sources_used: Optional[List[str]] = None,
) -> None:
    from .models import Feedback

    async with get_session() as session:
        session.add(
            Feedback(
                session_id=session_id,
                message_id=message_id,
                feedback_type=feedback_type,
                note=note,
                correction=correction,
                sources_used=sources_used or [],
            )
        )
        await session.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Session history
# ─────────────────────────────────────────────────────────────────────────────

async def append_session_message(
    session_id: str,
    role: str,
    content: str,
    metadata: Optional[dict] = None,
) -> None:
    from .models import SessionHistory

    async with get_session() as session:
        session.add(
            SessionHistory(
                session_id=session_id,
                role=role,
                content=content,
                message_metadata=metadata or {},
            )
        )
        await session.commit()


async def get_session_messages(
    session_id: str, limit: int = 20
) -> List[Dict[str, str]]:
    from sqlalchemy import select

    from .models import SessionHistory

    async with get_session() as session:
        rows = (
            await session.scalars(
                select(SessionHistory)
                .where(SessionHistory.session_id == session_id)
                .order_by(SessionHistory.created_at.desc())
                .limit(limit)
            )
        ).all()

    return [{"role": row.role, "content": row.content} for row in reversed(rows)]


async def clear_session_messages(session_id: str) -> None:
    from sqlalchemy import delete

    from .models import SessionHistory

    async with get_session() as session:
        await session.execute(
            delete(SessionHistory).where(SessionHistory.session_id == session_id)
        )
        await session.commit()


async def count_active_sessions() -> int:
    from sqlalchemy import distinct, func, select

    from .models import SessionHistory

    async with get_session() as session:
        return int(
            await session.scalar(
                select(func.count(distinct(SessionHistory.session_id)))
            )
            or 0
        )


async def list_sessions(limit: int = 100) -> List[Dict[str, Any]]:
    from sqlalchemy import desc, func, select

    from .models import SessionHistory

    async with get_session() as session:
        rows = (
            await session.execute(
                select(
                    SessionHistory.session_id,
                    func.count(SessionHistory.id).label("message_count"),
                    func.max(SessionHistory.created_at).label("last_seen_at"),
                )
                .group_by(SessionHistory.session_id)
                .order_by(desc("last_seen_at"))
                .limit(limit)
            )
        ).all()

    return [
        {
            "session_id": row.session_id,
            "message_count": int(row.message_count or 0),
            "last_seen_at": row.last_seen_at.isoformat() if row.last_seen_at else None,
        }
        for row in rows
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Users
# ─────────────────────────────────────────────────────────────────────────────

async def list_users() -> List[Dict[str, Any]]:
    from sqlalchemy import select

    from .models import User

    async with get_session() as session:
        rows = (
            await session.scalars(
                select(User).order_by(User.created_at.desc(), User.external_id.asc())
            )
        ).all()
    return [_user_to_dict(row) for row in rows]


async def count_users() -> int:
    from sqlalchemy import func, select

    from .models import User

    async with get_session() as session:
        return int(await session.scalar(select(func.count()).select_from(User)) or 0)


async def create_user(
    external_id: str, role: str, display_name: str = ""
) -> Dict[str, Any]:
    from .models import User

    async with get_session() as session:
        user = User(
            external_id=external_id,
            role=role.upper(),
            display_name=display_name,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
    return _user_to_dict(user)


async def update_user(
    user_id: str,
    *,
    role: Optional[str] = None,
    display_name: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    from sqlalchemy import select

    from .models import User

    async with get_session() as session:
        user = await session.scalar(
            select(User).where(User.id == uuid.UUID(user_id))
        )
        if user is None:
            return None
        if role is not None:
            user.role = role.upper()
        if display_name is not None:
            user.display_name = display_name
        await session.commit()
        await session.refresh(user)
    return _user_to_dict(user)


async def delete_user(user_id: str) -> bool:
    from sqlalchemy import delete

    from .models import User

    async with get_session() as session:
        result = await session.execute(
            delete(User).where(User.id == uuid.UUID(user_id))
        )
        await session.commit()
    return bool(result.rowcount)


def _user_to_dict(user: Any) -> Dict[str, Any]:
    return {
        "id": str(user.id),
        "external_id": user.external_id,
        "role": user.role,
        "display_name": user.display_name,
        "created_at": user.created_at.isoformat() if user.created_at else None,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Patient state
# ─────────────────────────────────────────────────────────────────────────────

_VALID_ENCOUNTER_TYPES = {"initial", "follow_up", "emergency"}
_VALID_MEDICATION_STATUSES = {"active", "stopped", "suspended"}
_VALID_DIAGNOSIS_STATUSES = {"active", "resolved", "suspected"}


def _coerce_uuid(value: Any) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


def _clean_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _clean_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clean_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _clean_bool(value: Any) -> Optional[bool]:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "yes", "1", "on"}:
            return True
        if lowered in {"false", "no", "0", "off"}:
            return False
    return None


def _date_value(value: Any) -> Optional[date]:
    if value is None or value == "":
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value)).date()
    except ValueError:
        return None


def _normalise_disease_id(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


def _patient_encounter_to_dict(row: Any) -> Dict[str, Any]:
    return {
        "id": str(row.encounter_id),
        "encounter_id": str(row.encounter_id),
        "patient_ref": row.patient_ref,
        "disease_scope": row.disease_scope,
        "encounter_date": row.encounter_date.isoformat() if row.encounter_date else None,
        "encounter_type": row.encounter_type,
        "clinician_role": row.clinician_role,
        "facility_level": row.facility_level,
        "notes": row.notes,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _patient_vital_to_dict(row: Any) -> Dict[str, Any]:
    return {
        "id": str(row.id),
        "encounter_id": str(row.encounter_id),
        "patient_ref": row.patient_ref,
        "recorded_at": row.recorded_at.isoformat() if row.recorded_at else None,
        "bp_systolic": row.bp_systolic,
        "bp_diastolic": row.bp_diastolic,
        "heart_rate": row.heart_rate,
        "respiratory_rate": row.respiratory_rate,
        "temperature": row.temperature,
        "spo2": row.spo2,
        "weight_kg": row.weight_kg,
        "height_cm": row.height_cm,
        "consciousness": row.consciousness,
        "supplemental_o2": row.supplemental_o2,
        "spo2_scale": row.spo2_scale,
        "news2_score": row.news2_score,
        "news2_risk": row.news2_risk,
        "bmi": row.bmi,
    }


def _patient_lab_to_dict(row: Any) -> Dict[str, Any]:
    return {
        "id": str(row.id),
        "encounter_id": str(row.encounter_id),
        "patient_ref": row.patient_ref,
        "recorded_at": row.recorded_at.isoformat() if row.recorded_at else None,
        "lab_type": row.lab_type,
        "value": row.value,
        "unit": row.unit,
        "reference_low": row.reference_low,
        "reference_high": row.reference_high,
        "flag": row.flag,
        "source": row.source,
    }


def _patient_medication_to_dict(row: Any) -> Dict[str, Any]:
    return {
        "id": str(row.id),
        "encounter_id": str(row.encounter_id),
        "patient_ref": row.patient_ref,
        "drug_name": row.drug_name,
        "generic_name": row.generic_name,
        "rxcui": row.rxcui,
        "dose": row.dose,
        "frequency": row.frequency,
        "route": row.route,
        "started_date": row.started_date.isoformat() if row.started_date else None,
        "stopped_date": row.stopped_date.isoformat() if row.stopped_date else None,
        "status": row.status,
        "indication": row.indication,
        "prescribed_by": row.prescribed_by,
    }


def _patient_diagnosis_to_dict(row: Any) -> Dict[str, Any]:
    return {
        "id": str(row.id),
        "encounter_id": str(row.encounter_id),
        "patient_ref": row.patient_ref,
        "condition_ref": row.condition_ref,
        "condition_name": row.condition_name,
        "icd10_code": row.icd10_code,
        "status": row.status,
        "onset_date": row.onset_date.isoformat() if row.onset_date else None,
        "resolved_date": row.resolved_date.isoformat() if row.resolved_date else None,
        "severity": row.severity,
        "confirmed_by": row.confirmed_by,
    }


async def create_encounter(
    patient_ref: str,
    disease_scope: str,
    encounter_type: str = "initial",
    clinician_role: Optional[str] = None,
    facility_level: Optional[str] = None,
    notes: Optional[str] = None,
) -> Dict[str, Any]:
    from .models import PatientEncounter

    encounter_type = _normalise_disease_id(encounter_type)
    if encounter_type not in _VALID_ENCOUNTER_TYPES:
        raise ValueError(f"Invalid encounter_type: {encounter_type}")

    async with get_session() as session:
        row = PatientEncounter(
            patient_ref=patient_ref,
            disease_scope=_clean_text(disease_scope) or "all",
            encounter_type=encounter_type,
            clinician_role=_clean_text(clinician_role),
            facility_level=_clean_text(facility_level),
            notes=_clean_text(notes),
        )
        session.add(row)
        await session.flush()
        await session.commit()
        await session.refresh(row)
    return _patient_encounter_to_dict(row)


async def upsert_vitals(
    patient_ref: str,
    encounter_id: str,
    vitals_dict: Dict[str, Any],
) -> Dict[str, Any]:
    from sqlalchemy import delete

    from .models import PatientVital

    encounter_uuid = _coerce_uuid(encounter_id)
    payload = {
        "encounter_id": encounter_uuid,
        "patient_ref": patient_ref,
        "bp_systolic": _clean_int(vitals_dict.get("bp_systolic") or vitals_dict.get("systolic_bp")),
        "bp_diastolic": _clean_int(vitals_dict.get("bp_diastolic") or vitals_dict.get("diastolic_bp")),
        "heart_rate": _clean_int(vitals_dict.get("heart_rate") or vitals_dict.get("pulse")),
        "respiratory_rate": _clean_int(vitals_dict.get("respiratory_rate") or vitals_dict.get("rr")),
        "temperature": _clean_float(vitals_dict.get("temperature")),
        "spo2": _clean_int(vitals_dict.get("spo2") or vitals_dict.get("o2_saturation")),
        "weight_kg": _clean_float(vitals_dict.get("weight_kg") or vitals_dict.get("weight")),
        "height_cm": _clean_float(vitals_dict.get("height_cm") or vitals_dict.get("height")),
        "consciousness": _clean_text(vitals_dict.get("consciousness")),
        "supplemental_o2": _clean_bool(
            vitals_dict.get("supplemental_o2", vitals_dict.get("supplemental_oxygen"))
        ),
        "spo2_scale": _clean_int(vitals_dict.get("spo2_scale")),
        "news2_score": _clean_int(vitals_dict.get("news2_score")),
        "news2_risk": _clean_text(vitals_dict.get("news2_risk")),
        "bmi": _clean_float(vitals_dict.get("bmi")),
    }
    payload = {key: value for key, value in payload.items() if value is not None}

    async with get_session() as session:
        await session.execute(delete(PatientVital).where(PatientVital.encounter_id == encounter_uuid))
        row = PatientVital(**payload)
        session.add(row)
        await session.commit()
        await session.refresh(row)
    return _patient_vital_to_dict(row)


async def upsert_labs(
    patient_ref: str,
    encounter_id: str,
    lab_entries: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from .models import PatientLab

    encounter_uuid = _coerce_uuid(encounter_id)
    rows_payload = []
    for entry in lab_entries or []:
        lab_type = _clean_text(entry.get("lab_type"))
        if not lab_type:
            continue
        rows_payload.append(
            {
                "encounter_id": encounter_uuid,
                "patient_ref": patient_ref,
                "lab_type": lab_type,
                "value": _clean_float(entry.get("value")),
                "unit": _clean_text(entry.get("unit")),
                "reference_low": _clean_float(entry.get("reference_low")),
                "reference_high": _clean_float(entry.get("reference_high")),
                "flag": _clean_text(entry.get("flag")) or "normal",
                "source": _clean_text(entry.get("source")) or "entered",
            }
        )
    if not rows_payload:
        return []

    stmt = pg_insert(PatientLab).values(rows_payload)
    stmt = stmt.on_conflict_do_update(
        index_elements=[PatientLab.encounter_id, PatientLab.lab_type],
        set_={
            "patient_ref": stmt.excluded.patient_ref,
            "value": stmt.excluded.value,
            "unit": stmt.excluded.unit,
            "reference_low": stmt.excluded.reference_low,
            "reference_high": stmt.excluded.reference_high,
            "flag": stmt.excluded.flag,
            "source": stmt.excluded.source,
        },
    )

    async with get_session() as session:
        await session.execute(stmt)
        await session.commit()
        rows = (
            await session.scalars(
                select(PatientLab)
                .where(PatientLab.encounter_id == encounter_uuid)
                .order_by(PatientLab.lab_type)
            )
        ).all()
    return [_patient_lab_to_dict(row) for row in rows]


async def upsert_medications(
    patient_ref: str,
    encounter_id: str,
    medications: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    from sqlalchemy import delete
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from .models import PatientMedication

    encounter_uuid = _coerce_uuid(encounter_id)
    rows_payload = []
    for item in medications or []:
        drug_name = _clean_text(item.get("drug_name") or item.get("name"))
        if not drug_name:
            continue
        status = _normalise_disease_id(item.get("status") or "active")
        if status not in _VALID_MEDICATION_STATUSES:
            raise ValueError(f"Invalid medication status: {status}")
        rows_payload.append(
            {
                "encounter_id": encounter_uuid,
                "patient_ref": patient_ref,
                "drug_name": drug_name,
                "generic_name": _clean_text(item.get("generic_name")),
                "rxcui": _clean_text(item.get("rxcui")),
                "dose": _clean_text(item.get("dose")),
                "frequency": _clean_text(item.get("frequency")),
                "route": _clean_text(item.get("route")),
                "started_date": _date_value(item.get("started_date")),
                "stopped_date": _date_value(item.get("stopped_date")),
                "status": status,
                "indication": _clean_text(item.get("indication")),
                "prescribed_by": _clean_text(item.get("prescribed_by")),
            }
        )

    async with get_session() as session:
        await session.execute(delete(PatientMedication).where(PatientMedication.encounter_id == encounter_uuid))
        if rows_payload:
            await session.execute(pg_insert(PatientMedication).values(rows_payload))
        await session.commit()
        rows = (
            await session.scalars(
                select(PatientMedication)
                .where(PatientMedication.encounter_id == encounter_uuid)
                .order_by(PatientMedication.status, PatientMedication.drug_name)
            )
        ).all()
    return [_patient_medication_to_dict(row) for row in rows]


async def upsert_diagnoses(
    patient_ref: str,
    encounter_id: str,
    diagnoses: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    from sqlalchemy import delete
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from .models import PatientDiagnosis

    encounter_uuid = _coerce_uuid(encounter_id)
    rows_payload = []
    for item in diagnoses or []:
        condition_name = _clean_text(item.get("condition_name") or item.get("name"))
        if not condition_name:
            continue
        status = _normalise_disease_id(item.get("status") or "active")
        if status not in _VALID_DIAGNOSIS_STATUSES:
            raise ValueError(f"Invalid diagnosis status: {status}")
        rows_payload.append(
            {
                "encounter_id": encounter_uuid,
                "patient_ref": patient_ref,
                "condition_ref": _clean_text(item.get("condition_ref")),
                "condition_name": condition_name,
                "icd10_code": _clean_text(item.get("icd10_code")),
                "status": status,
                "onset_date": _date_value(item.get("onset_date")),
                "resolved_date": _date_value(item.get("resolved_date")),
                "severity": _clean_text(item.get("severity")),
                "confirmed_by": _clean_text(item.get("confirmed_by")),
            }
        )

    async with get_session() as session:
        await session.execute(delete(PatientDiagnosis).where(PatientDiagnosis.encounter_id == encounter_uuid))
        if rows_payload:
            await session.execute(pg_insert(PatientDiagnosis).values(rows_payload))
        await session.commit()
        rows = (
            await session.scalars(
                select(PatientDiagnosis)
                .where(PatientDiagnosis.encounter_id == encounter_uuid)
                .order_by(PatientDiagnosis.status, PatientDiagnosis.condition_name)
            )
        ).all()
    return [_patient_diagnosis_to_dict(row) for row in rows]


async def get_patient_state(patient_ref: str) -> Dict[str, Any]:
    from sqlalchemy import select

    from .models import PatientDiagnosis, PatientEncounter, PatientLab, PatientMedication, PatientVital

    async with get_session() as session:
        encounter = await session.scalar(
            select(PatientEncounter)
            .where(PatientEncounter.patient_ref == patient_ref)
            .order_by(PatientEncounter.encounter_date.desc(), PatientEncounter.created_at.desc())
            .limit(1)
        )
        if encounter is None:
            return {}

        encounter_id = encounter.encounter_id
        vitals_rows = (
            await session.scalars(
                select(PatientVital)
                .where(PatientVital.patient_ref == patient_ref)
                .order_by(PatientVital.recorded_at.desc())
            )
        ).all()
        lab_rows = (
            await session.scalars(
                select(PatientLab)
                .where(PatientLab.patient_ref == patient_ref)
                .order_by(PatientLab.recorded_at.desc())
            )
        ).all()
        medication_rows = (
            await session.scalars(
                select(PatientMedication)
                .where(PatientMedication.patient_ref == patient_ref)
                .order_by(PatientMedication.started_date.desc().nullslast(), PatientMedication.drug_name)
            )
        ).all()
        diagnosis_rows = (
            await session.scalars(
                select(PatientDiagnosis)
                .where(PatientDiagnosis.patient_ref == patient_ref)
                .order_by(PatientDiagnosis.onset_date.desc().nullslast(), PatientDiagnosis.condition_name)
            )
        ).all()

    vitals = [_patient_vital_to_dict(row) for row in vitals_rows]
    labs = [_patient_lab_to_dict(row) for row in lab_rows]
    medications = [_patient_medication_to_dict(row) for row in medication_rows]
    diagnoses = [_patient_diagnosis_to_dict(row) for row in diagnosis_rows]

    latest_labs_by_type: Dict[str, Dict[str, Any]] = {}
    for lab in labs:
        latest_labs_by_type.setdefault(lab["lab_type"], lab)

    active_medications = [
        medication for medication in medications if medication.get("status") == "active"
    ]
    active_diagnoses = [
        diagnosis for diagnosis in diagnoses if diagnosis.get("status") == "active"
    ]
    active_conditions = [
        _normalise_disease_id(diagnosis.get("condition_name") or "")
        for diagnosis in active_diagnoses
    ]
    active_conditions = [condition for condition in active_conditions if condition]

    # Temporal events: key clinical dates for monitoring and pathway tracking
    temporal_events: Dict[str, Any] = {}
    for medication in active_medications:
        if medication.get("started_date"):
            drug = medication.get("drug_name", "")
            if drug:
                temporal_events.setdefault("treatment_start_dates", {})[drug] = medication["started_date"]
    for lab_type_key in ("cd4", "cd4_count", "viral_load", "vl"):
        lab = latest_labs_by_type.get(lab_type_key)
        if lab and lab.get("recorded_at"):
            if "cd4" in lab_type_key:
                temporal_events["last_cd4_date"] = lab["recorded_at"]
            elif "vl" in lab_type_key or "viral_load" in lab_type_key:
                temporal_events["last_viral_load_date"] = lab["recorded_at"]

    # Regimen history: all medications (active + stopped/superseded)
    regimen_history = [
        {
            "drug_name": m.get("drug_name"),
            "status": m.get("status"),
            "started_date": m.get("started_date"),
            "stopped_date": m.get("stopped_date"),
        }
        for m in medications
    ]

    return {
        "patient_ref": patient_ref,
        "most_recent_encounter": _patient_encounter_to_dict(encounter),
        "encounters": [_patient_encounter_to_dict(encounter)],
        "most_recent_vitals": vitals[0] if vitals else None,
        "vitals": vitals,
        "latest_labs_by_type": latest_labs_by_type,
        "labs": labs,
        "active_medications": active_medications,
        "medications": active_medications,
        "all_medications": medications,
        "active_diagnoses": active_diagnoses,
        "diagnoses": active_diagnoses,
        "active_conditions": active_conditions,
        "all_diagnoses": diagnoses,
        "temporal_events": temporal_events,
        "regimen_history": regimen_history,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Clinical memory
# ─────────────────────────────────────────────────────────────────────────────

async def create_pending_memory(
    patient_ref_hash: str,
    session_id: str,
    fact_type: str,
    fact_text: str,
    source_message_ids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    from .models import PendingMemory

    async with get_session() as session:
        row = PendingMemory(
            patient_ref_hash=patient_ref_hash,
            session_id=session_id,
            fact_type=fact_type,
            fact_text=fact_text,
            source_message_ids=source_message_ids or [],
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
    return _memory_to_dict(row)


async def list_pending_memory(
    patient_ref_hash: Optional[str] = None,
    session_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    from sqlalchemy import select

    from .models import PendingMemory

    stmt = select(PendingMemory).order_by(PendingMemory.created_at.desc())
    if patient_ref_hash:
        stmt = stmt.where(PendingMemory.patient_ref_hash == patient_ref_hash)
    if session_id:
        stmt = stmt.where(PendingMemory.session_id == session_id)
    async with get_session() as session:
        rows = (await session.scalars(stmt)).all()
    return [_memory_to_dict(row) for row in rows]


async def count_pending_memory() -> int:
    from sqlalchemy import func, select

    from .models import PendingMemory

    async with get_session() as session:
        return int(
            await session.scalar(select(func.count()).select_from(PendingMemory)) or 0
        )


async def approve_pending_memory(
    memory_id: str, approved_by: str
) -> Optional[Dict[str, Any]]:
    from sqlalchemy import select

    from .models import LongTermMemory, PendingMemory

    async with get_session() as session:
        pending = await session.scalar(
            select(PendingMemory).where(PendingMemory.id == uuid.UUID(memory_id))
        )
        if pending is None:
            return None
        approved = LongTermMemory(
            patient_ref_hash=pending.patient_ref_hash,
            session_id=pending.session_id,
            fact_type=pending.fact_type,
            fact_text=pending.fact_text,
            source_message_ids=pending.source_message_ids,
            approved_by=approved_by,
        )
        await session.delete(pending)
        session.add(approved)
        await session.commit()
        await session.refresh(approved)
    return _memory_to_dict(approved, include_approved=True)


async def list_long_term_memory(
    patient_ref_hash: str,
) -> List[Dict[str, Any]]:
    from sqlalchemy import select

    from .models import LongTermMemory

    async with get_session() as session:
        rows = (
            await session.scalars(
                select(LongTermMemory)
                .where(LongTermMemory.patient_ref_hash == patient_ref_hash)
                .order_by(LongTermMemory.created_at.desc())
            )
        ).all()
    return [_memory_to_dict(row, include_approved=True) for row in rows]


async def list_all_long_term_memory(limit: int = 100) -> List[Dict[str, Any]]:
    from sqlalchemy import select

    from .models import LongTermMemory

    async with get_session() as session:
        rows = (
            await session.scalars(
                select(LongTermMemory)
                .order_by(LongTermMemory.created_at.desc())
                .limit(limit)
            )
        ).all()
    return [_memory_to_dict(row, include_approved=True) for row in rows]


async def count_long_term_memory() -> int:
    from sqlalchemy import func, select

    from .models import LongTermMemory

    async with get_session() as session:
        return int(
            await session.scalar(select(func.count()).select_from(LongTermMemory)) or 0
        )


def _memory_to_dict(row: Any, include_approved: bool = False) -> Dict[str, Any]:
    data = {
        "id": str(row.id),
        "patient_ref_hash": row.patient_ref_hash,
        "session_id": row.session_id,
        "fact_type": row.fact_type,
        "fact_text": row.fact_text,
        "source_message_ids": row.source_message_ids,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }
    if include_approved:
        data["approved_by"] = getattr(row, "approved_by", None)
    return data


# ─────────────────────────────────────────────────────────────────────────────
# Embedding cache  (with TTL eviction)
# ─────────────────────────────────────────────────────────────────────────────

async def get_embedding_cache(query_hash: str) -> Optional[List[float]]:
    from sqlalchemy import select

    from .models import EmbeddingCache

    async with get_session() as session:
        row = await session.scalar(
            select(EmbeddingCache).where(EmbeddingCache.query_hash == query_hash)
        )
    return list(row.embedding) if row else None


async def put_embedding_cache(
    query_hash: str,
    model_name: str,
    embedding: List[float],
) -> None:
    """
    Upsert an embedding and evict rows older than CDSS_EMBEDDING_CACHE_TTL_DAYS.
    Default TTL is 30 days.  Set to 0 to disable eviction.
    """
    from sqlalchemy.dialects.postgresql import insert

    from .models import EmbeddingCache

    stmt = insert(EmbeddingCache).values(
        query_hash=query_hash,
        model_name=model_name,
        embedding=embedding,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[EmbeddingCache.query_hash],
        set_={"model_name": model_name, "embedding": embedding},
    )
    async with get_session() as session:
        await session.execute(stmt)
        if _EMBEDDING_CACHE_TTL_DAYS > 0:
            from datetime import datetime, timedelta
            from sqlalchemy import delete
            cutoff = datetime.utcnow() - timedelta(days=_EMBEDDING_CACHE_TTL_DAYS)
            await session.execute(
                delete(EmbeddingCache).where(EmbeddingCache.created_at < cutoff)
            )
        await session.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Evidence graph  (bulk upsert — no N+1 selects)
# ─────────────────────────────────────────────────────────────────────────────

async def upsert_evidence_graph(
    disease: str,
    nodes: List[Dict[str, Any]],
    edges: List[Dict[str, Any]],
    clinician_id: str,
) -> Dict[str, int]:
    """
    Bulk-upsert nodes then edges for one disease.

    Nodes are matched by (disease, ref_id); edges by
    (source_node_id, target_node_id, relation_type).

    Uses INSERT ON CONFLICT DO UPDATE so the entire seed for a disease with
    50 nodes + 200 edges costs two batched round-trips, not 250 sequential
    SELECTs.
    """
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from .models import EvidenceEdge, EvidenceNode

    async with get_session() as session:
        # ── 1. Bulk upsert nodes ────────────────────────────────────────
        node_rows = [
            {
                "ref_id": n["ref_id"],
                "disease": disease,
                "node_type": n["node_type"],
                "label": n["label"],
                "source_ref": n.get("source_ref", ""),
                "payload": n.get("payload", {}),
            }
            for n in nodes
        ]
        if node_rows:
            node_stmt = pg_insert(EvidenceNode).values(node_rows)
            node_stmt = node_stmt.on_conflict_do_update(
                # Unique constraint must exist on (disease, ref_id) — see migration
                index_elements=["disease", "ref_id"],
                set_={
                    "node_type": node_stmt.excluded.node_type,
                    "label": node_stmt.excluded.label,
                    "source_ref": node_stmt.excluded.source_ref,
                    "payload": node_stmt.excluded.payload,
                },
            )
            await session.execute(node_stmt)
            await session.flush()

        # ── 2. Load inserted node IDs in one query ──────────────────────
        ref_ids = [n["ref_id"] for n in nodes]
        db_nodes = (
            await session.scalars(
                select(EvidenceNode).where(
                    EvidenceNode.disease == disease,
                    EvidenceNode.ref_id.in_(ref_ids),
                )
            )
        ).all()
        node_id_by_ref: Dict[str, Any] = {n.ref_id: n.id for n in db_nodes}

        # ── 3. Bulk upsert edges ────────────────────────────────────────
        edge_rows = []
        for e in edges:
            src_id = node_id_by_ref.get(e["source_ref"])
            tgt_id = node_id_by_ref.get(e["target_ref"])
            if src_id is None or tgt_id is None:
                continue  # normalise_graph_seed should have caught this
            edge_rows.append(
                {
                    "source_node_id": src_id,
                    "target_node_id": tgt_id,
                    "relation_type": e["relation_type"],
                    "weight": int(e.get("weight", 1)),
                    "source_ref": e.get("source_ref_text", ""),
                    "clinician_id": clinician_id,
                    "payload": e.get("payload", {}),
                }
            )
        if edge_rows:
            edge_stmt = pg_insert(EvidenceEdge).values(edge_rows)
            edge_stmt = edge_stmt.on_conflict_do_update(
                # Unique constraint on (source_node_id, target_node_id, relation_type)
                index_elements=["source_node_id", "target_node_id", "relation_type"],
                set_={
                    "weight": edge_stmt.excluded.weight,
                    "source_ref": edge_stmt.excluded.source_ref,
                    "clinician_id": edge_stmt.excluded.clinician_id,
                    "payload": edge_stmt.excluded.payload,
                },
            )
            await session.execute(edge_stmt)

        await session.commit()

    return {"nodes": len(nodes), "edges": len(edge_rows)}


async def query_evidence_graph_db(
    disease: str,
    query: str,
    top_k: int = 5,
) -> List[Dict[str, Any]]:
    from sqlalchemy import or_, select
    from sqlalchemy.orm import aliased

    from .evidence import score_graph_hit
    from .models import EvidenceEdge, EvidenceNode

    TargetNode = aliased(EvidenceNode)
    terms = [t for t in query.lower().split() if t]
    base_filter = [EvidenceNode.disease == disease]
    if terms:
        term_filters = []
        for term in terms:
            like = f"%{term}%"
            term_filters.extend(
                [
                    EvidenceNode.label.ilike(like),
                    EvidenceNode.node_type.ilike(like),
                    TargetNode.label.ilike(like),
                    TargetNode.node_type.ilike(like),
                    EvidenceEdge.relation_type.ilike(like),
                ]
            )
        base_filter.append(or_(*term_filters))

    async with get_session() as session:
        rows = (
            await session.execute(
                select(EvidenceNode, EvidenceEdge, TargetNode)
                .join(EvidenceEdge, EvidenceEdge.source_node_id == EvidenceNode.id)
                .join(TargetNode, TargetNode.id == EvidenceEdge.target_node_id)
                .where(*base_filter)
                .limit(max(top_k * 4, top_k))
            )
        ).all()

    scored = []
    for node, edge, target in rows:
        payload = {
            "source_node": _evidence_node_to_dict(node),
            "edge": _evidence_edge_to_dict(edge),
            "target_node": _evidence_node_to_dict(target),
        }
        scored.append(
            (
                score_graph_hit(
                    query,
                    payload["source_node"],
                    payload["edge"],
                    payload["target_node"],
                ),
                payload,
            )
        )
    scored.sort(key=lambda item: item[0], reverse=True)
    return [dict(item[1], score=item[0]) for item in scored[:top_k]]


async def evidence_graph_stats() -> Dict[str, Any]:
    from sqlalchemy import func, select

    from .models import EvidenceEdge, EvidenceNode

    async with get_session() as session:
        node_count = await session.scalar(
            select(func.count()).select_from(EvidenceNode)
        )
        edge_count = await session.scalar(
            select(func.count()).select_from(EvidenceEdge)
        )
        by_disease = (
            await session.execute(
                select(EvidenceNode.disease, func.count(EvidenceNode.id))
                .group_by(EvidenceNode.disease)
                .order_by(EvidenceNode.disease)
            )
        ).all()
        edge_by_disease = (
            await session.execute(
                select(EvidenceNode.disease, func.count(EvidenceEdge.id))
                .join(EvidenceEdge, EvidenceEdge.source_node_id == EvidenceNode.id)
                .group_by(EvidenceNode.disease)
                .order_by(EvidenceNode.disease)
            )
        ).all()

    node_counts = {d: int(c or 0) for d, c in by_disease}
    edge_counts = {d: int(c or 0) for d, c in edge_by_disease}
    diseases = sorted(set(node_counts) | set(edge_counts))

    return {
        "nodes": int(node_count or 0),
        "edges": int(edge_count or 0),
        "by_disease": node_counts,
        "by_disease_detail": {
            disease: {
                "nodes": node_counts.get(disease, 0),
                "edges": edge_counts.get(disease, 0),
            }
            for disease in diseases
        },
    }


async def list_evidence_nodes(
    disease: Optional[str] = None,
    node_type: Optional[str] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    from sqlalchemy import select

    from .models import EvidenceNode

    stmt = (
        select(EvidenceNode)
        .order_by(EvidenceNode.disease, EvidenceNode.node_type, EvidenceNode.label)
        .limit(limit)
    )
    if disease:
        stmt = stmt.where(EvidenceNode.disease == disease)
    if node_type:
        stmt = stmt.where(EvidenceNode.node_type == node_type)
    async with get_session() as session:
        rows = (await session.scalars(stmt)).all()
    return [_evidence_node_to_dict(row) for row in rows]


def _evidence_node_to_dict(node: Any) -> Dict[str, Any]:
    return {
        "id": str(node.id),
        "ref_id": node.ref_id,
        "node_type": node.node_type,
        "disease": node.disease,
        "label": node.label,
        "source_ref": node.source_ref,
        "payload": node.payload,
    }


def _evidence_edge_to_dict(edge: Any) -> Dict[str, Any]:
    return {
        "id": str(edge.id),
        "relation_type": edge.relation_type,
        "weight": edge.weight,
        "source_ref": edge.source_ref,
        "clinician_id": edge.clinician_id,
        "payload": edge.payload,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Alert Overrides (Phase B)
# ─────────────────────────────────────────────────────────────────────────────

async def create_alert_override(
    session_id: str,
    alert_type: str,
    alert_level: str,
    alert_summary: str,
    override_reason: str,
    clinician_role: str,
    patient_ref: Optional[str] = None,
) -> Dict[str, Any]:
    from .models import AlertOverride

    async with get_session() as session:
        row = AlertOverride(
            session_id=session_id,
            alert_type=alert_type,
            alert_level=alert_level,
            alert_summary=alert_summary,
            override_reason=override_reason,
            clinician_role=clinician_role,
            patient_ref=patient_ref,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)

    return {
        "id": str(row.id),
        "session_id": row.session_id,
        "alert_type": row.alert_type,
        "alert_level": row.alert_level,
        "alert_summary": row.alert_summary,
        "patient_ref": row.patient_ref,
        "override_reason": row.override_reason,
        "clinician_role": row.clinician_role,
        "override_timestamp": row.override_timestamp.isoformat() if row.override_timestamp else None,
    }


async def get_alert_override_report() -> Dict[str, Any]:
    from .models import AlertOverride, AuditLog
    from sqlalchemy import select, func
    
    async with get_session() as session:
        override_stmt = (
            select(AlertOverride.alert_type, func.count(AlertOverride.id).label("override_count"))
            .group_by(AlertOverride.alert_type)
        )
        override_res = await session.execute(override_stmt)
        overrides = {row.alert_type: row.override_count for row in override_res}
        
        fired_stmt = (
            select(AuditLog.disease, func.count(AuditLog.id).label("fired_count"))
            .where(AuditLog.event_type == "CLINICAL_SCORE")
            .group_by(AuditLog.disease)
        )
        fired_res = await session.execute(fired_stmt)
        fired = {row.disease: row.fired_count for row in fired_res}
        
        reason_stmt = (
            select(AlertOverride.alert_type, AlertOverride.override_reason, func.count(AlertOverride.id).label("reason_count"))
            .group_by(AlertOverride.alert_type, AlertOverride.override_reason)
        )
        reason_res = await session.execute(reason_stmt)
        
        reasons_by_type = {}
        for row in reason_res:
            atype = row.alert_type
            if atype not in reasons_by_type:
                reasons_by_type[atype] = []
            reasons_by_type[atype].append({"reason": row.override_reason, "count": row.reason_count})
            
        summary = []
        all_types = set(list(overrides.keys()) + list(fired.keys()))
        for alert_type in all_types:
            f_count = fired.get(alert_type, 0)
            o_count = overrides.get(alert_type, 0)
            rate = round((o_count / f_count * 100), 1) if f_count > 0 else 0.0
            
            top_reasons = sorted(reasons_by_type.get(alert_type, []), key=lambda x: x["count"], reverse=True)
            
            summary.append({
                "alert_type": alert_type,
                "total_fired": f_count,
                "total_overridden": o_count,
                "override_rate_pct": rate,
                "top_override_reasons": top_reasons[:5],
                "downgrade_candidate": rate > 80.0
            })
            
    return {"override_summary": summary}


# ─────────────────────────────────────────────────────────────────────────────
# Clinical Documents (Phase E)
# ─────────────────────────────────────────────────────────────────────────────

async def create_clinical_document(
    document_type: str,
    patient_ref: str,
    content: str,
    encounter_id: Optional[str] = None,
    requires_clinician_review: bool = True,
    guideline_citations: Optional[List[Dict[str, Any]]] = None
) -> Dict[str, Any]:
    from .models import ClinicalDocument
    import uuid
    async with get_session() as session:
        doc = ClinicalDocument(
            document_type=document_type,
            patient_ref=patient_ref,
            encounter_id=uuid.UUID(encounter_id) if encounter_id else None,
            content=content,
            requires_clinician_review=requires_clinician_review,
            guideline_citations=guideline_citations or []
        )
        session.add(doc)
        await session.commit()
        await session.refresh(doc)
        return {
            "id": str(doc.id),
            "document_type": doc.document_type,
            "patient_ref": doc.patient_ref,
            "encounter_id": str(doc.encounter_id) if doc.encounter_id else None,
            "content": doc.content,
            "requires_clinician_review": doc.requires_clinician_review,
            "reviewed_by": doc.reviewed_by,
            "reviewed_at": doc.reviewed_at.isoformat() if doc.reviewed_at else None,
            "generated_at": doc.generated_at.isoformat(),
            "guideline_citations": doc.guideline_citations
        }

async def get_clinical_document(document_id: str) -> Optional[Dict[str, Any]]:
    from .models import ClinicalDocument
    import uuid
    async with get_session() as session:
        doc = await session.get(ClinicalDocument, uuid.UUID(document_id))
        if not doc:
            return None
        return {
            "id": str(doc.id),
            "document_type": doc.document_type,
            "patient_ref": doc.patient_ref,
            "encounter_id": str(doc.encounter_id) if doc.encounter_id else None,
            "content": doc.content,
            "requires_clinician_review": doc.requires_clinician_review,
            "reviewed_by": doc.reviewed_by,
            "reviewed_at": doc.reviewed_at.isoformat() if doc.reviewed_at else None,
            "generated_at": doc.generated_at.isoformat(),
            "guideline_citations": doc.guideline_citations
        }

async def list_patient_documents(patient_ref: str) -> List[Dict[str, Any]]:
    from sqlalchemy import select
    from .models import ClinicalDocument
    async with get_session() as session:
        query = select(ClinicalDocument).where(
            ClinicalDocument.patient_ref == patient_ref
        ).order_by(ClinicalDocument.generated_at.desc())
        result = await session.execute(query)
        docs = result.scalars().all()
        return [
            {
                "id": str(doc.id),
                "document_type": doc.document_type,
                "encounter_id": str(doc.encounter_id) if doc.encounter_id else None,
                "requires_clinician_review": doc.requires_clinician_review,
                "reviewed_by": doc.reviewed_by,
                "reviewed_at": doc.reviewed_at.isoformat() if doc.reviewed_at else None,
                "generated_at": doc.generated_at.isoformat()
            }
            for doc in docs
        ]

async def review_clinical_document(document_id: str, reviewed_by: str) -> bool:
    from datetime import datetime
    from .models import ClinicalDocument
    import uuid
    async with get_session() as session:
        doc = await session.get(ClinicalDocument, uuid.UUID(document_id))
        if not doc:
            return False
        doc.reviewed_by = reviewed_by
        doc.reviewed_at = datetime.utcnow()
        await session.commit()
        return True
