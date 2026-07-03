"""Add administered column to incident_medications_given.

Revision ID: 0003_administered_column
Revises: 0002_field_protocol_columns
Create Date: 2026-06-21

Resolved per Phase 0.5: medication logging is unconditional and does not
depend on the item being administered. `administered` records whether the
item was actually given on a per-row basis, rather than the row's
existence implying it was. Defaults True so pre-existing rows written
before this migration (when the endpoint required administration to log)
remain truthful — all of them represent items that were given.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_administered_column"
down_revision = "0002_field_protocol_columns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "incident_medications_given",
        sa.Column(
            "administered",
            sa.Boolean,
            nullable=False,
            server_default=sa.true(),
        ),
    )


def downgrade() -> None:
    op.drop_column("incident_medications_given", "administered")
