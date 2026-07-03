"""clinical_documents

Revision ID: 0009
Revises: 0008
Create Date: 2026-06-14 10:01:00.000000

"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '0009'
down_revision = '0008_alert_overrides'
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.create_table('clinical_documents',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('document_type', sa.Text(), nullable=False),
        sa.Column('patient_ref', sa.Text(), nullable=False),
        sa.Column('encounter_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('requires_clinician_review', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('reviewed_by', sa.Text(), nullable=True),
        sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('generated_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=True),
        sa.Column('guideline_citations', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
        sa.ForeignKeyConstraint(['encounter_id'], ['patient_encounters.encounter_id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.CheckConstraint("document_type IN ('sbar', 'referral', 'patient_summary', 'clinical_note')")
    )
    op.create_index(op.f('ix_clinical_documents_patient_ref'), 'clinical_documents', ['patient_ref'], unique=False)

def downgrade() -> None:
    op.drop_index(op.f('ix_clinical_documents_patient_ref'), table_name='clinical_documents')
    op.drop_table('clinical_documents')
