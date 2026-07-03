"""app/terminology/models.py.

SQLAlchemy ORM models for the terminology subsystem.

Design decisions:
- terminology_concepts: one row per UMLS CUI.  preferred_name and
  semantic_types come directly from MRCONSO + MRSTY via the ETL.
  definition is the first non-empty MRDEF entry (MSH preferred).
  synonyms and codes are JSONB to avoid the ARRAY(String) extension
  dependency present in the UMLS repo's original models.

- terminology_aliases: flattened synonym table for fast ILIKE / tsvector
  lookup without touching the JSONB array.  One row per (cui, alias).
  Enables the alias-expansion query path in TerminologyService.link_text.

- terminology_relations: one row per MRREL edge we care about.
  Filtered at ETL time to a clinically relevant subset.
  source_sab is the UMLS source abbreviation (MSH, SNOMEDCT_US, etc.)
  so downstream code can trust-weight by source.

- guideline_chunk_concepts: join table linking IndexedChunks (by their
  LanceDB chunk_id) to CUIs found during ingestion-time annotation.
  chunk_id is a VARCHAR not a FK because LanceDB owns that namespace.
  This table is write-only during ingestion and read-only during query.

- terminology_coverage: admin visibility into how many chunks per disease
  have been annotated.  Populated by the coverage_report() helper.
  Not a materialised view — updated on demand by the admin endpoint.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class TermBase(DeclarativeBase):
    """Separate declarative base so terminology models never collide
    with the main app models when both are imported in the same process.
    """

    pass


class TerminologyConcept(TermBase):
    __tablename__ = "terminology_concepts"
    __table_args__ = (
        Index("idx_tc_cui", "cui"),
        Index(
            "idx_tc_preferred_name_trgm",
            "preferred_name",
            postgresql_using="gin",
            postgresql_ops={"preferred_name": "gin_trgm_ops"},
        ),
        Index(
            "idx_tc_semantic_types",
            "semantic_types",
            postgresql_using="gin",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    cui: Mapped[str] = mapped_column(String(12), unique=True, nullable=False)
    preferred_name: Mapped[str] = mapped_column(String(500), nullable=False)
    definition: Mapped[str | None] = mapped_column(Text)
    # JSONB avoids the ARRAY(String) extension needed in the UMLS repo's models
    semantic_types: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    synonyms: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    # codes: list of {code, source, term_type} dicts — from MRCONSO
    codes: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    # sources: list of SAB abbreviations present for this CUI
    sources: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    # qdrant_id: integer assigned during embedding upload — null until embedded
    qdrant_id: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TerminologyAlias(TermBase):
    """Flattened synonym table for fast text lookup without JSONB unnest."""

    __tablename__ = "terminology_aliases"
    __table_args__ = (
        UniqueConstraint("cui", "alias", name="uq_alias_cui_alias"),
        Index("idx_ta_alias_lower", "alias"),
        Index("idx_ta_cui", "cui"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    cui: Mapped[str] = mapped_column(String(12), nullable=False)
    alias: Mapped[str] = mapped_column(String(500), nullable=False)
    # source_sab: which UMLS source provided this alias (MSH, SNOMEDCT_US …)
    source_sab: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TerminologyRelation(TermBase):
    """Clinically relevant UMLS relations (filtered subset of MRREL).

    relation_type: RB (broader), RN (narrower), RO (other), CHD, PAR, SIB …
    relation_label: the RELA label when present (e.g. 'may_treat', 'has_ingredient')
    source_sab: UMLS source abbreviation — used for trust-weighting.
    Trust order (highest first): SNOMEDCT_US > MSH > ICD10CM > RXNORM > others.
    Do NOT use UMLS relations to override guideline-derived evidence graph edges.
    """

    __tablename__ = "terminology_relations"
    __table_args__ = (
        UniqueConstraint("cui1", "cui2", "relation_type", "source_sab", name="uq_tr_triple_source"),
        Index("idx_tr_cui1", "cui1"),
        Index("idx_tr_cui2", "cui2"),
        Index("idx_tr_relation_label", "relation_label"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    cui1: Mapped[str] = mapped_column(String(12), nullable=False)
    cui2: Mapped[str] = mapped_column(String(12), nullable=False)
    relation_type: Mapped[str] = mapped_column(String(20), nullable=False)
    relation_label: Mapped[str | None] = mapped_column(String(200))
    source_sab: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class GuidelineChunkConcept(TermBase):
    """Join table: LanceDB chunk_id → UMLS CUI(s) found during ingestion annotation.

    chunk_id is a VARCHAR, not a FK — LanceDB owns that namespace.
    confidence: float in [0, 1] from the QuickUMLS / Qdrant similarity score.
    annotation_source: "quickumls" | "qdrant_similarity" | "exact_match"
    This table is append-only during ingestion, read-only during query time.
    """

    __tablename__ = "guideline_chunk_concepts"
    __table_args__ = (
        UniqueConstraint("chunk_id", "cui", name="uq_gcc_chunk_cui"),
        Index("idx_gcc_chunk_id", "chunk_id"),
        Index("idx_gcc_cui", "cui"),
        Index("idx_gcc_disease", "disease"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    chunk_id: Mapped[str] = mapped_column(String(256), nullable=False)
    cui: Mapped[str] = mapped_column(String(12), nullable=False)
    preferred_name: Mapped[str] = mapped_column(String(500), nullable=False)
    disease: Mapped[str] = mapped_column(String(64), nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float)
    annotation_source: Mapped[str] = mapped_column(
        String(32), nullable=False, default="exact_match"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TerminologyCoverage(TermBase):
    """Admin-visible coverage summary: annotated vs total chunks per disease.
    Populated on demand by TerminologyService.coverage_report().
    Not a materialised view — a plain writable table updated by the admin endpoint.
    """

    __tablename__ = "terminology_coverage"
    __table_args__ = (UniqueConstraint("disease", name="uq_tcov_disease"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    disease: Mapped[str] = mapped_column(String(64), nullable=False)
    total_chunks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    annotated_chunks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    unique_cuis: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    coverage_pct: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
