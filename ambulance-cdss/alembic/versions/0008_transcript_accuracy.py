"""Add transcript_text and location_accuracy_m columns to incidents.

Revision ID: 0008_transcript_accuracy
Revises: 0007_merge_heads
Create Date: 2026-07-04

Epic 1.4 — transcript_text stores the append-only call transcript.
Epic 1.5 — location_accuracy_m stores E911/AML location accuracy in metres.
Both columns are nullable so existing rows are unaffected.
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision = "0008_transcript_accuracy"
down_revision = "0007_merge_heads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "incidents",
        sa.Column("transcript_text", sa.Text, nullable=True),
    )
    op.add_column(
        "incidents",
        sa.Column("location_accuracy_m", sa.Float, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("incidents", "location_accuracy_m")
    op.drop_column("incidents", "transcript_text")
