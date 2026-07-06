"""Create audit_events table

Revision ID: 0009_audit_events
Revises: 0008_transcript_accuracy
Create Date: 2026-07-04
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0009_audit_events"
down_revision = "0008_transcript_accuracy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "timestamp",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("actor_id", sa.String(100)),
        sa.Column("incident_id", UUID(as_uuid=True)),
        sa.Column("details", JSONB),
        sa.Column("ip_address", sa.String(45)),
    )
    op.create_index("idx_audit_timestamp", "audit_events", ["timestamp"])
    op.create_index("idx_audit_incident", "audit_events", ["incident_id"])


def downgrade() -> None:
    op.drop_index("idx_audit_incident", table_name="audit_events")
    op.drop_index("idx_audit_timestamp", table_name="audit_events")
    op.drop_table("audit_events")
