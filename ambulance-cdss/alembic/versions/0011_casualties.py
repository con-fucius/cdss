"""Add casualties table for multi-casualty incidents.

Revision ID: 0011_casualties
Revises: 0009_audit_events
Create Date: 2026-07-04
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0011_casualties"
down_revision = "0009_audit_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if 'incident_casualties' not in inspector.get_table_names():
        op.create_table(
            "incident_casualties",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column(
                "incident_id",
                UUID(as_uuid=True),
                sa.ForeignKey("incidents.id"),
                nullable=False,
            ),
            sa.Column("casualty_number", sa.Integer, nullable=False),
            sa.Column("chief_complaint", sa.String(500)),
            sa.Column("triage_score", sa.String(10)),
            sa.Column("age_estimate", sa.Integer),
            sa.Column("gender", sa.String(10)),
            sa.Column("vitals_summary", JSONB),
            sa.Column("status", sa.String(50), server_default="pending"),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
            ),
        )
        op.create_index(
            "ix_casualties_incident",
            "incident_casualties",
            ["incident_id"],
        )


def downgrade() -> None:
    op.drop_index("ix_casualties_incident", table_name="incident_casualties")
    op.drop_table("incident_casualties")
