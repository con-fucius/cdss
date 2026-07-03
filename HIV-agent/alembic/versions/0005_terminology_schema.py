"""Add terminology subsystem tables.

Revision ID: 0005_terminology_schema
Revises: 0004_upsert_constraints
Create Date: 2026-06-02

Creates five tables for the bounded UMLS terminology subsystem:
  - terminology_concepts      (one row per UMLS CUI)
  - terminology_aliases       (flattened synonyms for fast text lookup)
  - terminology_relations     (clinically filtered MRREL edges)
  - guideline_chunk_concepts  (chunk_id → CUI join table)
  - terminology_coverage      (admin coverage stats, updated on demand)

Notes:
- pg_trgm extension is required for gin_trgm_ops index on preferred_name.
  The migration creates it if not already present (CREATE EXTENSION IF NOT EXISTS).
- qdrant_id on terminology_concepts is nullable; it is NULL until the
  generate_embeddings_qdrant.py ETL has been run.
- No FK from guideline_chunk_concepts.chunk_id to LanceDB — that namespace
  is owned by LanceDB and not visible to Postgres.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005_terminology_schema"
down_revision = "0004_upsert_constraints"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # pg_trgm is required for the trigram similarity index on preferred_name
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # ── terminology_concepts ─────────────────────────────────────────
    op.create_table(
        "terminology_concepts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("cui", sa.String(12), nullable=False, unique=True),
        sa.Column("preferred_name", sa.String(500), nullable=False),
        sa.Column("definition", sa.Text),
        sa.Column(
            "semantic_types",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "synonyms",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "codes",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "sources",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("qdrant_id", sa.Integer),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("idx_tc_cui", "terminology_concepts", ["cui"])
    # Trigram index for similarity search on preferred_name
    op.execute(
        "CREATE INDEX idx_tc_preferred_name_trgm ON terminology_concepts "
        "USING gin (preferred_name gin_trgm_ops)"
    )
    # GIN index for JSONB semantic_types containment queries
    op.execute(
        "CREATE INDEX idx_tc_semantic_types ON terminology_concepts "
        "USING gin (semantic_types)"
    )

    # ── terminology_aliases ──────────────────────────────────────────
    op.create_table(
        "terminology_aliases",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("cui", sa.String(12), nullable=False),
        sa.Column("alias", sa.String(500), nullable=False),
        sa.Column("source_sab", sa.String(20), nullable=False, server_default=""),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("cui", "alias", name="uq_alias_cui_alias"),
    )
    op.create_index("idx_ta_alias_lower", "terminology_aliases", ["alias"])
    op.create_index("idx_ta_cui", "terminology_aliases", ["cui"])

    # ── terminology_relations ────────────────────────────────────────
    op.create_table(
        "terminology_relations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("cui1", sa.String(12), nullable=False),
        sa.Column("cui2", sa.String(12), nullable=False),
        sa.Column("relation_type", sa.String(20), nullable=False),
        sa.Column("relation_label", sa.String(200)),
        sa.Column("source_sab", sa.String(20)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "cui1", "cui2", "relation_type", "source_sab",
            name="uq_tr_triple_source",
        ),
    )
    op.create_index("idx_tr_cui1", "terminology_relations", ["cui1"])
    op.create_index("idx_tr_cui2", "terminology_relations", ["cui2"])
    op.create_index(
        "idx_tr_relation_label", "terminology_relations", ["relation_label"]
    )

    # ── guideline_chunk_concepts ─────────────────────────────────────
    op.create_table(
        "guideline_chunk_concepts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("chunk_id", sa.String(256), nullable=False),
        sa.Column("cui", sa.String(12), nullable=False),
        sa.Column("preferred_name", sa.String(500), nullable=False),
        sa.Column("disease", sa.String(64), nullable=False),
        sa.Column("confidence", sa.Float),
        sa.Column(
            "annotation_source",
            sa.String(32),
            nullable=False,
            server_default="exact_match",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("chunk_id", "cui", name="uq_gcc_chunk_cui"),
    )
    op.create_index("idx_gcc_chunk_id", "guideline_chunk_concepts", ["chunk_id"])
    op.create_index("idx_gcc_cui", "guideline_chunk_concepts", ["cui"])
    op.create_index("idx_gcc_disease", "guideline_chunk_concepts", ["disease"])

    # ── terminology_coverage ─────────────────────────────────────────
    op.create_table(
        "terminology_coverage",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("disease", sa.String(64), nullable=False),
        sa.Column("total_chunks", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "annotated_chunks", sa.Integer, nullable=False, server_default="0"
        ),
        sa.Column("unique_cuis", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "coverage_pct", sa.Float, nullable=False, server_default="0.0"
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("disease", name="uq_tcov_disease"),
    )


def downgrade() -> None:
    op.drop_table("terminology_coverage")
    op.drop_table("guideline_chunk_concepts")
    op.drop_table("terminology_relations")
    op.drop_table("terminology_aliases")
    op.drop_table("terminology_concepts")
