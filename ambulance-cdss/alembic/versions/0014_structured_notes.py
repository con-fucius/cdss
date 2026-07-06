"""Create structured incident_notes table for auditable notes."""

from alembic import op
import sqlalchemy as sa

revision = "0014_structured_notes"
down_revision = "0013_next_of_kin"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'incident_notes',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('incident_id', sa.UUID(), sa.ForeignKey('incidents.incident_id'), nullable=False),
        sa.Column('note_text', sa.Text(), nullable=False),
        sa.Column('author_id', sa.String(100), nullable=False),
        sa.Column('author_role', sa.String(20), nullable=False),
        sa.Column('note_type', sa.String(50), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('is_deleted', sa.Boolean(), server_default=sa.text('false')),
    )
    op.create_index('ix_notes_incident', 'incident_notes', ['incident_id'])
    op.create_index('ix_notes_created', 'incident_notes', ['created_at'])


def downgrade():
    op.drop_index('ix_notes_created')
    op.drop_index('ix_notes_incident')
    op.drop_table('incident_notes')
