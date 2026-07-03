"""Add eta_minutes and notes columns to incidents.

Revision ID: 0004_eta_and_notes_columns
Revises: 0003_administered_column
Create Date: 2026-06-25

- eta_minutes: persisted from the dispatch service response so overdue
  detection works after the HTTP response is discarded (Improvement 3.1).
- notes: dispatcher-side free-text annotation, append-only
  (Improvement 5 from IMPROVEMENTS 2).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004_eta_and_notes_columns"
down_revision = "0003_administered_column"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "incidents",
        sa.Column("eta_minutes", sa.Float, nullable=True),
    )
    op.add_column(
        "incidents",
        sa.Column("notes", sa.Text, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("incidents", "notes")
    op.drop_column("incidents", "eta_minutes")
