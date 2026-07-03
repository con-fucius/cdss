"""SQLAlchemy 2.x models for Phase 1 persistent storage.

Changes:
- EvidenceNode: added UniqueConstraint(disease, ref_id) so bulk upsert in
  repositories.upsert_evidence_graph can use ON CONFLICT DO UPDATE.
- EvidenceEdge: added UniqueConstraint(source_node_id, target_node_id,
  relation_type) for the same reason.
- EmbeddingCache: added index on created_at for TTL eviction query.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# ─────────────────────────────────────────────────────────────────────────────
# Enums
# ─────────────────────────────────────────────────────────────────────────────


class AlertLevel:
    CRITICAL = "CRITICAL"
    WARNING = "WARNING"
    INFO = "INFO"
    BACKGROUND = "BACKGROUND"


VALID_OVERRIDE_REASONS = frozenset(
    [
        "clinically_irrelevant",
        "already_actioned",
        "patient_specific_exception",
        "incorrect_alert",
        "duplicate",
    ]
)


class Base(DeclarativeBase):
    pass


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("idx_audit_logs_timestamp", "timestamp"),
        Index("idx_audit_logs_session_id", "session_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    session_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    query_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    disease: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    feedback_type: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    log_data: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


class SessionHistory(Base):
    __tablename__ = "session_history"
    __table_args__ = (Index("idx_session_history_session_created", "session_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[str] = mapped_column(String(128), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    message_metadata: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class EvidenceNode(Base):
    __tablename__ = "evidence_nodes"
    __table_args__ = (
        # Required for bulk upsert ON CONFLICT (disease, ref_id)
        UniqueConstraint("disease", "ref_id", name="uq_evidence_nodes_disease_ref"),
        Index("idx_evidence_nodes_type_disease", "node_type", "disease"),
        Index("idx_evidence_nodes_ref", "ref_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    node_type: Mapped[str] = mapped_column(String(64), nullable=False)
    ref_id: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    disease: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    label: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    source_ref: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    outgoing_edges: Mapped[list[EvidenceEdge]] = relationship(
        foreign_keys="EvidenceEdge.source_node_id",
        back_populates="source_node",
    )


class EvidenceEdge(Base):
    __tablename__ = "evidence_edges"
    __table_args__ = (
        # Required for bulk upsert ON CONFLICT (source, target, relation)
        UniqueConstraint(
            "source_node_id",
            "target_node_id",
            "relation_type",
            name="uq_evidence_edges_triple",
        ),
        Index("idx_evidence_edges_relation", "relation_type"),
        Index("idx_evidence_edges_source_target", "source_node_id", "target_node_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_node_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evidence_nodes.id"), nullable=False
    )
    target_node_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evidence_nodes.id"), nullable=False
    )
    relation_type: Mapped[str] = mapped_column(String(64), nullable=False)
    weight: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    source_ref: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    clinician_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    source_node: Mapped[EvidenceNode] = relationship(
        foreign_keys=[source_node_id], back_populates="outgoing_edges"
    )
    target_node: Mapped[EvidenceNode] = relationship(foreign_keys=[target_node_id])


class Feedback(Base):
    __tablename__ = "feedback"
    __table_args__ = (Index("idx_feedback_session_id", "session_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[str] = mapped_column(String(128), nullable=False)
    message_id: Mapped[str] = mapped_column(String(128), nullable=False)
    feedback_type: Mapped[str] = mapped_column(String(64), nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    correction: Mapped[str] = mapped_column(Text, nullable=False, default="")
    sources_used: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    external_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="CLINICIAN")
    display_name: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PatientRef(Base):
    __tablename__ = "patient_refs"
    __table_args__ = (Index("idx_patient_refs_patient_hash", "patient_hash"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    patient_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    salt_version: Mapped[str] = mapped_column(String(64), nullable=False, default="default")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class LongTermMemory(Base):
    __tablename__ = "long_term_memory"
    __table_args__ = (
        Index("idx_long_term_memory_patient_ref", "patient_ref_hash"),
        Index("idx_long_term_memory_session_id", "session_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    patient_ref_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    session_id: Mapped[str] = mapped_column(String(128), nullable=False)
    fact_type: Mapped[str] = mapped_column(String(64), nullable=False)
    fact_text: Mapped[str] = mapped_column(Text, nullable=False)
    source_message_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    approved_by: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PendingMemory(Base):
    __tablename__ = "pending_memory"
    __table_args__ = (
        Index("idx_pending_memory_session_id", "session_id"),
        Index("idx_pending_memory_patient_ref", "patient_ref_hash"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    patient_ref_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    session_id: Mapped[str] = mapped_column(String(128), nullable=False)
    fact_type: Mapped[str] = mapped_column(String(64), nullable=False)
    fact_text: Mapped[str] = mapped_column(Text, nullable=False)
    source_message_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class EmbeddingCache(Base):
    __tablename__ = "embedding_cache"
    __table_args__ = (
        Index("idx_embedding_cache_query_hash", "query_hash"),
        # TTL eviction scans by created_at — needs an index
        Index("idx_embedding_cache_created_at", "created_at"),
    )

    query_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    model_name: Mapped[str] = mapped_column(String(256), nullable=False)
    embedding: Mapped[list] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# ─────────────────────────────────────────────────────────────────────────────
# Patient state (Phase A — migration 0007_patient_state)
# ─────────────────────────────────────────────────────────────────────────────


class PatientEncounter(Base):
    __tablename__ = "patient_encounters"
    __table_args__ = (
        Index("idx_pe_patient_ref", "patient_ref"),
        Index("idx_pe_encounter_date", "encounter_date"),
        Index("idx_pe_patient_ref_date", "patient_ref", "encounter_date"),
    )

    encounter_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    patient_ref: Mapped[str] = mapped_column(Text, nullable=False)
    disease_scope: Mapped[str] = mapped_column(Text, nullable=False)
    encounter_date: Mapped[date] = mapped_column(
        Date, nullable=False, server_default=func.current_date()
    )
    encounter_type: Mapped[str] = mapped_column(String(32), nullable=False, default="initial")
    clinician_role: Mapped[str | None] = mapped_column(Text)
    facility_level: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    vitals: Mapped[list[PatientVital]] = relationship(
        back_populates="encounter", cascade="all, delete-orphan"
    )
    labs: Mapped[list[PatientLab]] = relationship(
        back_populates="encounter", cascade="all, delete-orphan"
    )
    medications: Mapped[list[PatientMedication]] = relationship(
        back_populates="encounter", cascade="all, delete-orphan"
    )
    diagnoses: Mapped[list[PatientDiagnosis]] = relationship(
        back_populates="encounter", cascade="all, delete-orphan"
    )


class PatientVital(Base):
    __tablename__ = "patient_vitals"
    __table_args__ = (
        Index("idx_pv_patient_ref", "patient_ref"),
        Index("idx_pv_encounter_id", "encounter_id"),
        Index("idx_pv_recorded_at", "recorded_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    encounter_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("patient_encounters.encounter_id", ondelete="CASCADE"),
        nullable=False,
    )
    patient_ref: Mapped[str] = mapped_column(Text, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    bp_systolic: Mapped[int | None] = mapped_column(Integer)
    bp_diastolic: Mapped[int | None] = mapped_column(Integer)
    heart_rate: Mapped[int | None] = mapped_column(Integer)
    respiratory_rate: Mapped[int | None] = mapped_column(Integer)
    temperature: Mapped[float | None] = mapped_column(Numeric(4, 1))
    spo2: Mapped[int | None] = mapped_column(Integer)
    weight_kg: Mapped[float | None] = mapped_column(Numeric(5, 1))
    height_cm: Mapped[float | None] = mapped_column(Numeric(5, 1))
    consciousness: Mapped[str | None] = mapped_column(Text)  # A/V/P/U per AVPU
    supplemental_o2: Mapped[bool | None] = mapped_column(Boolean)
    spo2_scale: Mapped[int | None] = mapped_column(Integer)  # 1 or 2 per NEWS2
    news2_score: Mapped[int | None] = mapped_column(Integer)
    news2_risk: Mapped[str | None] = mapped_column(Text)
    bmi: Mapped[float | None] = mapped_column(Numeric(4, 1))

    encounter: Mapped[PatientEncounter] = relationship(back_populates="vitals")


class PatientLab(Base):
    __tablename__ = "patient_labs"
    __table_args__ = (
        Index("idx_pl_patient_ref", "patient_ref"),
        Index("idx_pl_encounter_id", "encounter_id"),
        Index("idx_pl_lab_type", "lab_type"),
        Index("idx_pl_flag", "flag"),
        UniqueConstraint("encounter_id", "lab_type", name="uq_pl_encounter_lab_type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    encounter_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("patient_encounters.encounter_id", ondelete="CASCADE"),
        nullable=False,
    )
    patient_ref: Mapped[str] = mapped_column(Text, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    lab_type: Mapped[str] = mapped_column(Text, nullable=False)
    value: Mapped[float | None] = mapped_column(Numeric)
    unit: Mapped[str | None] = mapped_column(Text)
    reference_low: Mapped[float | None] = mapped_column(Numeric)
    reference_high: Mapped[float | None] = mapped_column(Numeric)
    flag: Mapped[str] = mapped_column(Text, nullable=False, default="normal")
    source: Mapped[str] = mapped_column(Text, nullable=False, default="entered")

    encounter: Mapped[PatientEncounter] = relationship(back_populates="labs")


class PatientMedication(Base):
    __tablename__ = "patient_medications"
    __table_args__ = (
        Index("idx_pm_patient_ref", "patient_ref"),
        Index("idx_pm_encounter_id", "encounter_id"),
        Index("idx_pm_status", "status"),
        Index("idx_pm_drug_name", "drug_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    encounter_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("patient_encounters.encounter_id", ondelete="CASCADE"),
        nullable=False,
    )
    patient_ref: Mapped[str] = mapped_column(Text, nullable=False)
    drug_name: Mapped[str] = mapped_column(Text, nullable=False)
    generic_name: Mapped[str | None] = mapped_column(Text)
    rxcui: Mapped[str | None] = mapped_column(Text)
    dose: Mapped[str | None] = mapped_column(Text)
    frequency: Mapped[str | None] = mapped_column(Text)
    route: Mapped[str | None] = mapped_column(Text)
    started_date: Mapped[date | None] = mapped_column(Date)
    stopped_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")
    indication: Mapped[str | None] = mapped_column(Text)
    prescribed_by: Mapped[str | None] = mapped_column(Text)

    encounter: Mapped[PatientEncounter] = relationship(back_populates="medications")


class PatientDiagnosis(Base):
    __tablename__ = "patient_diagnoses"
    __table_args__ = (
        Index("idx_pd_patient_ref", "patient_ref"),
        Index("idx_pd_encounter_id", "encounter_id"),
        Index("idx_pd_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    encounter_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("patient_encounters.encounter_id", ondelete="CASCADE"),
        nullable=False,
    )
    patient_ref: Mapped[str] = mapped_column(Text, nullable=False)
    condition_ref: Mapped[str | None] = mapped_column(Text)  # evidence graph ref_id
    condition_name: Mapped[str] = mapped_column(Text, nullable=False)
    icd10_code: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")
    onset_date: Mapped[date | None] = mapped_column(Date)
    resolved_date: Mapped[date | None] = mapped_column(Date)
    severity: Mapped[str | None] = mapped_column(Text)
    confirmed_by: Mapped[str | None] = mapped_column(Text)

    encounter: Mapped[PatientEncounter] = relationship(back_populates="diagnoses")


# ─────────────────────────────────────────────────────────────────────────────
# Alert governance (Phase B/G — migration 0008_alert_overrides)
# ─────────────────────────────────────────────────────────────────────────────


class AlertOverride(Base):
    __tablename__ = "alert_overrides"
    __table_args__ = (
        Index("idx_ao_alert_type", "alert_type"),
        Index("idx_ao_session_id", "session_id"),
        Index("idx_ao_override_timestamp", "override_timestamp"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    alert_type: Mapped[str] = mapped_column(Text, nullable=False)
    alert_level: Mapped[str] = mapped_column(String(32), nullable=False)
    alert_summary: Mapped[str] = mapped_column(String(140), nullable=False)
    session_id: Mapped[str] = mapped_column(String(128), nullable=False)
    patient_ref: Mapped[str | None] = mapped_column(Text)
    override_reason: Mapped[str] = mapped_column(Text, nullable=False)
    clinician_role: Mapped[str] = mapped_column(String(64), nullable=False)
    override_timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# ─────────────────────────────────────────────────────────────────────────────
# Clinical documents (Phase E — migration 0009_clinical_documents)
# ─────────────────────────────────────────────────────────────────────────────


class ClinicalDocument(Base):
    __tablename__ = "clinical_documents"
    __table_args__ = (
        Index("idx_cd_patient_ref", "patient_ref"),
        Index("idx_cd_document_type", "document_type"),
        Index("idx_cd_generated_at", "generated_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_type: Mapped[str] = mapped_column(String(32), nullable=False)
    patient_ref: Mapped[str] = mapped_column(Text, nullable=False)
    encounter_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("patient_encounters.encounter_id", ondelete="SET NULL"),
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    requires_clinician_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    reviewed_by: Mapped[str | None] = mapped_column(Text)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    guideline_citations: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
