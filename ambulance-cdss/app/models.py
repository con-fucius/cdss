"""app/models.py.

SQLAlchemy ORM models for the incident data model (Phase 1).

Design notes:
- `incidents` is the single root record for a call-to-handoff lifecycle.
  This deliberately replaces the chronic-disease CDSS's multi-table
  longitudinal patient state — see docs/OUT_OF_SCOPE.md. An incident is
  short-lived and lifecycle-bound, not a multi-visit patient chart.
- `incident_dispatch_log` is append-only and immutable. It is the full
  transcript of a Mode 1 locked-script interview. Rows are never updated
  or deleted in normal operation — only inserted. This is the artifact
  a medico-legal review would reconstruct from.
- `incident_field_log` is the paramedic-side equivalent, append-only.
- `incident_vitals` stores computed scores (NEWS2, GCS) at write time —
  not recomputed later — so a historical record reflects what the
  clinician actually saw in the moment, even if scoring logic changes later.
- `incident_medications_given` logs every relevant drug/item a unit
  carries, considers, or administers (Phase 0.5, resolved) — logging is
  unconditional and does not depend on the item being administered; the
  `administered` column records that fact per row instead.
- `guidance_lookup_log` is intentionally a separate table from
  `incident_dispatch_log` — see docs/GOVERNANCE.md. Mode 1 and Mode 2
  must be independently reconstructable and never conflated.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum as PyEnum

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class IncidentStatus(str, PyEnum):
    RECEIVED = "received"
    DISPATCHED = "dispatched"
    ON_SCENE = "on_scene"
    TRANSPORTING = "transporting"
    HANDOFF_COMPLETE = "handoff_complete"
    CLOSED = "closed"


class RecordedBy(str, PyEnum):
    DISPATCH = "dispatch"
    FIELD = "field"


class Incident(Base):
    __tablename__ = "incidents"
    __table_args__ = (
        Index("idx_incidents_status", "status"),
        Index("idx_incidents_created_at", "created_at"),
    )

    incident_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    status: Mapped[str] = mapped_column(
        Enum(IncidentStatus, name="incident_status", native_enum=False, length=32),
        nullable=False,
        default=IncidentStatus.RECEIVED,
    )
    priority_code: Mapped[str | None] = mapped_column(String(32))
    chief_complaint: Mapped[str] = mapped_column(Text, nullable=False)

    # Caller location — kept as separate lat/lon + free text. This is PII
    # subject to the retention policy (Phase 1.9 — resolved: 30 days).
    caller_location_lat: Mapped[float | None] = mapped_column(Float)
    caller_location_lon: Mapped[float | None] = mapped_column(Float)
    caller_location_text: Mapped[str | None] = mapped_column(Text)

    dispatch_protocol_id: Mapped[str | None] = mapped_column(String(128))
    dispatch_protocol_version: Mapped[str | None] = mapped_column(String(64))
    # Full immutable snapshot of the protocol content used, per
    # docs/GOVERNANCE.md — guarantees reproducibility even if the protocol
    # registry is edited after this incident started.
    dispatch_protocol_snapshot: Mapped[dict | None] = mapped_column(JSONB)

    # Phase 4 — field-side protocol selection. Deliberately no snapshot
    # column here: FieldProtocol is not governance-locked the way
    # DispatchProtocol is (see app/protocols/schema.py docstring —
    # paramedics operate under clinical judgment latitude, not a
    # signed-off-by-medical-director locked script), so reproducibility-
    # by-snapshot is not the same requirement. The field log itself
    # (incident_field_log) is the append-only source of truth for what
    # was actually done in the field, independent of which protocol
    # version was selected as a checklist aid.
    field_protocol_id: Mapped[str | None] = mapped_column(String(128))
    field_protocol_version: Mapped[str | None] = mapped_column(String(64))

    assigned_unit_id: Mapped[str | None] = mapped_column(String(128))
    recommended_unit_type: Mapped[str | None] = mapped_column(String(64))

    routed_facility_id: Mapped[str | None] = mapped_column(String(128))
    routed_facility_name: Mapped[str | None] = mapped_column(String(256))

    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    on_scene_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    transporting_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    handoff_complete_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # PII purge marker — set by the retention job once enforced (Phase 1.9).
    pii_purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Dispatcher-side free-text annotations (Improvement 5 from IMPROVEMENTS 2).
    # Append-only: each PATCH appends a timestamped line. No overwrite.
    notes: Mapped[str | None] = mapped_column(Text)

    # ETA from the external dispatch service (Improvement 3.1).
    # Persisted so overdue detection can work after the HTTP response is gone.
    eta_minutes: Mapped[float | None] = mapped_column(Float)

    # Phase 2.8 — Triage enrichment from the Triage Ranker service.
    # Written asynchronously by a background create_task. Nullable because
    # the enrichment may not have resolved when the incident is first queried.
    triage_enrichment: Mapped[dict | None] = mapped_column(JSONB)

    # Epic 1.4 — Call transcription persistence.
    # Append-only text field storing the verbatim call transcript with
    # timestamps and speaker labels. Null when no audio transcription
    # was captured (manual-entry calls).
    transcript_text: Mapped[str | None] = mapped_column(Text)

    # Epic 1.5 — E911/AML location accuracy.
    # Stores the reported accuracy of the location pin from an external
    # push (e.g. E911, AML). Null when location was entered manually.
    location_accuracy_m: Mapped[float | None] = mapped_column(Float)

    # Next-of-kin contact information for family notification.
    next_of_kin_name: Mapped[str | None] = mapped_column(String(256))
    next_of_kin_phone: Mapped[str | None] = mapped_column(String(32))
    next_of_kin_relationship: Mapped[str | None] = mapped_column(String(64))


class IncidentDispatchLog(Base):
    """Append-only Mode 1 locked-script transcript. Never updated, never deleted."""

    __tablename__ = "incident_dispatch_log"
    __table_args__ = (Index("idx_dispatch_log_incident", "incident_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    incident_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("incidents.incident_id"), nullable=False
    )
    question_id: Mapped[str] = mapped_column(String(128), nullable=False)
    question_text: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    protocol_version: Mapped[str] = mapped_column(String(64), nullable=False)
    # Set true if this row represents a backtrack re-answer rather than a
    # forward step — see docs/GOVERNANCE.md backtracking policy.
    is_backtrack: Mapped[bool] = mapped_column(default=False, nullable=False)
    # Improvement 4.2 — points to the new row that superseded this one
    # during a correction-window edit. Null means this row is current.
    superseded_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class IncidentFieldLog(Base):
    """Append-only paramedic-side action log."""

    __tablename__ = "incident_field_log"
    __table_args__ = (Index("idx_field_log_incident", "incident_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    incident_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("incidents.incident_id"), nullable=False
    )
    step_id: Mapped[str] = mapped_column(String(128), nullable=False)
    action_type: Mapped[str] = mapped_column(String(32), nullable=False)
    data: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    recorded_by: Mapped[str] = mapped_column(String(128), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class IncidentVitals(Base):
    __tablename__ = "incident_vitals"
    __table_args__ = (
        Index("idx_vitals_incident", "incident_id"),
        Index("idx_vitals_recorded_at", "recorded_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    incident_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("incidents.incident_id"), nullable=False
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    recorded_by: Mapped[str] = mapped_column(String(32), nullable=False)

    respiratory_rate: Mapped[int | None] = mapped_column(Integer)
    spo2: Mapped[int | None] = mapped_column(Integer)
    spo2_scale: Mapped[int | None] = mapped_column(Integer)
    supplemental_o2: Mapped[bool | None] = mapped_column()
    bp_systolic: Mapped[int | None] = mapped_column(Integer)
    bp_diastolic: Mapped[int | None] = mapped_column(Integer)
    heart_rate: Mapped[int | None] = mapped_column(Integer)
    consciousness: Mapped[str | None] = mapped_column(String(20))  # A/V/P/U or full words
    temperature: Mapped[float | None] = mapped_column(Float)

    gcs_eye: Mapped[int | None] = mapped_column(Integer)
    gcs_verbal: Mapped[int | None] = mapped_column(Integer)
    gcs_motor: Mapped[int | None] = mapped_column(Integer)

    # Computed at insert time — see module docstring for why this is not
    # recomputed retroactively.
    news2_score: Mapped[int | None] = mapped_column(Integer)
    news2_risk_level: Mapped[str | None] = mapped_column(String(16))
    gcs_total: Mapped[int | None] = mapped_column(Integer)


class IncidentMedicationGiven(Base):
    """Resolved per Phase 0.5: every relevant drug or item a unit carries,
    considers, or administers is logged here — logging does not depend
    on the item actually being given. No allowlist/formulary gate is
    applied at the API layer (see app/main.py::add_incident_medication).
    `administered` records whether the item was actually given, since a
    row no longer implies that by existing.
    """

    __tablename__ = "incident_medications_given"
    __table_args__ = (Index("idx_meds_given_incident", "incident_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    incident_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("incidents.incident_id"), nullable=False
    )
    drug_name: Mapped[str] = mapped_column(String(256), nullable=False)
    dose: Mapped[str] = mapped_column(String(128), nullable=False)
    route: Mapped[str] = mapped_column(String(64), nullable=False)
    # Whether this item was actually administered, vs. carried/considered/
    # declined. Defaults true to preserve the meaning of pre-existing rows
    # written before this column existed.
    administered: Mapped[bool] = mapped_column(default=True, nullable=False)
    given_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    given_by: Mapped[str] = mapped_column(String(128), nullable=False)


class GuidanceLookupLog(Base):
    """Mode 2 usage log. Deliberately separate from IncidentDispatchLog — see
    docs/GOVERNANCE.md. Every lookup here is informational-only and must
    never have altered the locked-script outcome.
    """

    __tablename__ = "guidance_lookup_log"
    __table_args__ = (Index("idx_guidance_log_incident", "incident_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    incident_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("incidents.incident_id"), nullable=False
    )
    question_id: Mapped[str | None] = mapped_column(String(128))
    query_text: Mapped[str] = mapped_column(Text, nullable=False)
    result_summary: Mapped[str] = mapped_column(Text, nullable=False)
    dispatcher_id: Mapped[str] = mapped_column(String(128), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AuditEvent(Base):
    __tablename__ = 'audit_events'

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    actor_id: Mapped[str | None] = mapped_column(String(100))
    incident_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    details: Mapped[dict | None] = mapped_column(JSONB)
    ip_address: Mapped[str | None] = mapped_column(String(45))


class IncidentUnitLocation(Base):
    """Improvement 4.3 — lightweight location pings from the field unit
    during an active incident. Two data columns (lat, lon) per row,
    written once per interval, read once per routing call. No real-time
    streaming, no WebSocket, no geofencing — just raw coordinates.
    """

    __tablename__ = "incident_unit_location"
    __table_args__ = (Index("idx_unit_location_incident", "incident_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    incident_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("incidents.incident_id"), nullable=False
    )
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lon: Mapped[float] = mapped_column(Float, nullable=False)
    recorded_by: Mapped[str] = mapped_column(String(128), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class IncidentCasualty(Base):
    """Multi-casualty incident sub-table. Each row represents one casualty
    within an incident. The incident itself remains the root record —
    casualties are a child collection, not a replacement for the incident model.
    """

    __tablename__ = "incident_casualties"
    __table_args__ = (Index("ix_casualties_incident", "incident_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    incident_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("incidents.incident_id"), nullable=False
    )
    casualty_number: Mapped[int] = mapped_column(Integer, nullable=False)
    chief_complaint: Mapped[str | None] = mapped_column(String(500))
    triage_score: Mapped[str | None] = mapped_column(String(10))  # START: Immediate/Delayed/Minor/Deceased
    age_estimate: Mapped[int | None] = mapped_column(Integer)
    gender: Mapped[str | None] = mapped_column(String(10))
    vitals_summary: Mapped[dict | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(50), server_default="pending")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class IncidentNote(Base):
    """Structured incident notes — replaces the plain-text blob in Incident.notes.
    Each note is individually trackable with author, role, type, and audit timestamps.
    Supports soft-delete for audit trail preservation.
    """

    __tablename__ = "incident_notes"
    __table_args__ = (
        Index("ix_notes_incident", "incident_id"),
        Index("ix_notes_created", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    incident_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("incidents.incident_id"), nullable=False
    )
    note_text: Mapped[str] = mapped_column(Text, nullable=False)
    author_id: Mapped[str] = mapped_column(String(100), nullable=False)
    author_role: Mapped[str] = mapped_column(String(20), nullable=False)  # 'dispatcher', 'field', 'system'
    note_type: Mapped[str] = mapped_column(String(50), nullable=False)    # 'dispatcher_note', 'field_log', 'correction', 'system'
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
