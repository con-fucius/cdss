"""Add triage_enrichment JSONB column to incidents table.

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-01

Phase 2.8 — Adds a nullable JSONB column to store triage enrichment
results from the Triage Ranker service. Written asynchronously when
the background task completes (fire-and-forget create_task).
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers
revision = "0006_triage_enrichment"
down_revision = "0005_supersede_unit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "incidents",
        sa.Column("triage_enrichment", JSONB, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("incidents", "triage_enrichment")
