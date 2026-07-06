"""Add next-of-kin fields to incidents."""

from alembic import op
import sqlalchemy as sa

revision = "0013_next_of_kin"
down_revision = "0012_widen_consciousness"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('incidents', sa.Column('next_of_kin_name', sa.String(256), nullable=True))
    op.add_column('incidents', sa.Column('next_of_kin_phone', sa.String(32), nullable=True))
    op.add_column('incidents', sa.Column('next_of_kin_relationship', sa.String(64), nullable=True))


def downgrade():
    op.drop_column('incidents', 'next_of_kin_relationship')
    op.drop_column('incidents', 'next_of_kin_phone')
    op.drop_column('incidents', 'next_of_kin_name')
