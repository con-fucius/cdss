"""Add alert overrides table.

Revision ID: 0008_alert_overrides
Revises: 0007_patient_state
Create Date: 2026-06-14

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0008_alert_overrides"
down_revision = "0007_patient_state"
branch_labels = None
depends_on = None

def upgrade() -> None:
    VALID_REASONS = (
        "clinically_irrelevant",
        "already_actioned",
        "patient_specific_exception",
        "incorrect_alert",
        "duplicate",
    )
    op.create_table(
        "alert_overrides",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("alert_type", sa.Text, nullable=False),
        sa.Column("alert_level", sa.String(32), nullable=False),
        sa.Column("alert_summary", sa.String(140), nullable=False),
        sa.Column("session_id", sa.String(128), nullable=False),
        sa.Column("patient_ref", sa.Text, nullable=True),
        sa.Column("override_reason", sa.Text, nullable=False),
        sa.Column("clinician_role", sa.String(64), nullable=False),
        sa.Column(
            "override_timestamp",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "override_reason IN ({})".format(
                ", ".join(f"'{r}'" for r in VALID_REASONS)
            ),
            name="ck_override_reason",
        ),
    )
    op.create_index("idx_ao_alert_type", "alert_overrides", ["alert_type"])
    op.create_index("idx_ao_session_id", "alert_overrides", ["session_id"])
    op.create_index("idx_ao_override_timestamp", "alert_overrides", ["override_timestamp"])


def downgrade() -> None:
    op.drop_table("alert_overrides")
