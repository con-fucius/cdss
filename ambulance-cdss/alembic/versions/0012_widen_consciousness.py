"""Widen consciousness column to accept full words."""

from alembic import op
import sqlalchemy as sa

revision = "0012_widen_consciousness"
down_revision = "0011_casualties"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column('incident_vitals', 'consciousness', type_=sa.String(20))


def downgrade():
    op.alter_column('incident_vitals', 'consciousness', type_=sa.String(4))
