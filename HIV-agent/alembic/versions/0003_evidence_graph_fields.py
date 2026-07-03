"""evidence graph retrieval fields

Revision ID: 0003_evidence_graph_fields
Revises: 0002_memory_foundation
Create Date: 2026-06-01
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_evidence_graph_fields"
down_revision = "0002_memory_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("evidence_nodes", sa.Column("ref_id", sa.String(length=256), nullable=False, server_default=""))
    op.add_column("evidence_nodes", sa.Column("label", sa.String(length=256), nullable=False, server_default=""))
    op.add_column("evidence_edges", sa.Column("weight", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("evidence_edges", sa.Column("source_ref", sa.String(length=512), nullable=False, server_default=""))
    op.add_column("evidence_edges", sa.Column("clinician_id", sa.String(length=128), nullable=False, server_default=""))
    op.create_index("idx_evidence_nodes_type_disease", "evidence_nodes", ["node_type", "disease"])
    op.create_index("idx_evidence_nodes_ref", "evidence_nodes", ["ref_id"])
    op.create_index("idx_evidence_edges_relation", "evidence_edges", ["relation_type"])
    op.create_index("idx_evidence_edges_source_target", "evidence_edges", ["source_node_id", "target_node_id"])


def downgrade() -> None:
    op.drop_index("idx_evidence_edges_source_target", table_name="evidence_edges")
    op.drop_index("idx_evidence_edges_relation", table_name="evidence_edges")
    op.drop_index("idx_evidence_nodes_ref", table_name="evidence_nodes")
    op.drop_index("idx_evidence_nodes_type_disease", table_name="evidence_nodes")
    op.drop_column("evidence_edges", "clinician_id")
    op.drop_column("evidence_edges", "source_ref")
    op.drop_column("evidence_edges", "weight")
    op.drop_column("evidence_nodes", "label")
    op.drop_column("evidence_nodes", "ref_id")
