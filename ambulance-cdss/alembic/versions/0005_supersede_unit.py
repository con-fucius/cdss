"""Add superseded_by to dispatch log and create incident_unit_location table.

Revision ID: 0005_supersede_unit
Revises: 0004_eta_and_notes_columns
Create Date: 2026-06-25

- superseded_by: nullable UUID FK on incident_dispatch_log pointing to the
  new row that replaced this one during a correction-window edit (4.2).
- incident_unit_location: lightweight table for field-unit GPS pings (4.3).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005_supersede_unit"
down_revision = "0004_eta_and_notes_columns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "incident_dispatch_log",
        sa.Column(
            "superseded_by",
            sa.dialects.postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_table(
        "incident_unit_location",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "incident_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("incidents.incident_id"),
            nullable=False,
        ),
        sa.Column("lat", sa.Float, nullable=False),
        sa.Column("lon", sa.Float, nullable=False),
        sa.Column("recorded_by", sa.String(128), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "idx_unit_location_incident",
        "incident_unit_location",
        ["incident_id"],
    )


def downgrade() -> None:
    op.drop_index("idx_unit_location_incident", table_name="incident_unit_location")
    op.drop_table("incident_unit_location")
    op.drop_column("incident_dispatch_log", "superseded_by")
