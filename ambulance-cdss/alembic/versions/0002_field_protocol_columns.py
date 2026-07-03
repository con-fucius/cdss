"""Field protocol selection columns — Phase 4.

Revision ID: 0002_field_protocol_columns
Revises: 0001_incidents
Create Date: 2026-06-21

Adds field_protocol_id / field_protocol_version to incidents, mirroring
the dispatch_protocol_id / dispatch_protocol_version pattern from 0001 —
but deliberately WITHOUT a snapshot column. See app/models.py::Incident
field_protocol_id docstring for why: FieldProtocol is not governance-
locked the way DispatchProtocol is, so the reproducibility-by-snapshot
guarantee that exists for Mode 1 does not apply the same way here. The
append-only incident_field_log table (already created in 0001) remains
the source of truth for what was actually done in the field.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_field_protocol_columns"
down_revision = "0001_incidents"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("incidents", sa.Column("field_protocol_id", sa.String(128)))
    op.add_column("incidents", sa.Column("field_protocol_version", sa.String(64)))


def downgrade() -> None:
    op.drop_column("incidents", "field_protocol_version")
    op.drop_column("incidents", "field_protocol_id")
