"""Harden terminology schema defaults and relation uniqueness.

Revision ID: 0006_terminology_hardening
Revises: 0005_terminology_schema
Create Date: 2026-06-03

This migration is intentionally defensive. Some local databases may already
have run the initial terminology migration before source_sab was made
non-nullable. Postgres unique constraints allow duplicate NULL values, so a
nullable source_sab weakens uq_tr_triple_source.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006_terminology_hardening"
down_revision = "0005_terminology_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE terminology_relations SET source_sab = '' WHERE source_sab IS NULL")
    op.alter_column(
        "terminology_relations",
        "source_sab",
        existing_type=sa.String(length=20),
        nullable=False,
        server_default="",
    )
    for table, column in [
        ("terminology_concepts", "semantic_types"),
        ("terminology_concepts", "synonyms"),
        ("terminology_concepts", "codes"),
        ("terminology_concepts", "sources"),
    ]:
        op.alter_column(
            table,
            column,
            existing_type=postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        )


def downgrade() -> None:
    for table, column in [
        ("terminology_concepts", "semantic_types"),
        ("terminology_concepts", "synonyms"),
        ("terminology_concepts", "codes"),
        ("terminology_concepts", "sources"),
    ]:
        op.alter_column(
            table,
            column,
            existing_type=postgresql.JSONB(),
            server_default=None,
            nullable=False,
        )
    op.alter_column(
        "terminology_relations",
        "source_sab",
        existing_type=sa.String(length=20),
        nullable=True,
        server_default=None,
    )
