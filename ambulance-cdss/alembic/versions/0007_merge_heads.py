"""Continuation migration after branch cleanup.

Revision ID: 0007_merge_heads
Revises: 0006_triage_enrichment
Create Date: 2026-07-03

The duplicate branch (0003_med_admin_flag) was removed since it duplicated
0003_administered_column. This migration simply records that 0006 is the
single head.
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = "0007_merge_heads"
down_revision = "0006_triage_enrichment"
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
