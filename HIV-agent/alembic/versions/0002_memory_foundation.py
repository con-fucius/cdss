"""memory foundation schema

Revision ID: 0002_memory_foundation
Revises: 0001_initial_foundation
Create Date: 2026-06-01
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0002_memory_foundation"
down_revision = "0001_initial_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "long_term_memory",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("patient_ref_hash", sa.String(length=64), nullable=False),
        sa.Column("session_id", sa.String(length=128), nullable=False),
        sa.Column("fact_type", sa.String(length=64), nullable=False),
        sa.Column("fact_text", sa.Text(), nullable=False),
        sa.Column("source_message_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("approved_by", sa.String(length=128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_long_term_memory_patient_ref", "long_term_memory", ["patient_ref_hash"])
    op.create_index("idx_long_term_memory_session_id", "long_term_memory", ["session_id"])

    op.create_table(
        "pending_memory",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("patient_ref_hash", sa.String(length=64), nullable=False),
        sa.Column("session_id", sa.String(length=128), nullable=False),
        sa.Column("fact_type", sa.String(length=64), nullable=False),
        sa.Column("fact_text", sa.Text(), nullable=False),
        sa.Column("source_message_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_pending_memory_patient_ref", "pending_memory", ["patient_ref_hash"])
    op.create_index("idx_pending_memory_session_id", "pending_memory", ["session_id"])

    op.create_table(
        "embedding_cache",
        sa.Column("query_hash", sa.String(length=64), nullable=False),
        sa.Column("model_name", sa.String(length=256), nullable=False),
        sa.Column("embedding", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("query_hash"),
    )
    op.create_index("idx_embedding_cache_query_hash", "embedding_cache", ["query_hash"])


def downgrade() -> None:
    op.drop_index("idx_embedding_cache_query_hash", table_name="embedding_cache")
    op.drop_table("embedding_cache")
    op.drop_index("idx_pending_memory_session_id", table_name="pending_memory")
    op.drop_index("idx_pending_memory_patient_ref", table_name="pending_memory")
    op.drop_table("pending_memory")
    op.drop_index("idx_long_term_memory_session_id", table_name="long_term_memory")
    op.drop_index("idx_long_term_memory_patient_ref", table_name="long_term_memory")
    op.drop_table("long_term_memory")
