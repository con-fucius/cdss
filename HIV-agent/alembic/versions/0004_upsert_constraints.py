"""Add unique constraints for bulk upsert and embedding cache TTL index.

Revision ID: 0004_upsert_constraints
Revises: 0003_evidence_graph_fields
Create Date: 2026-06-02

Changes:
- UniqueConstraint(disease, ref_id) on evidence_nodes  — required by
  repositories.upsert_evidence_graph INSERT ON CONFLICT DO UPDATE.
- UniqueConstraint(source_node_id, target_node_id, relation_type) on
  evidence_edges — required by the same bulk edge upsert path.
- Index on embedding_cache.created_at — required by TTL eviction query
  that deletes rows older than CDSS_EMBEDDING_CACHE_TTL_DAYS.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0004_upsert_constraints"
down_revision = "0003_evidence_graph_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # evidence_nodes: unique (disease, ref_id) for bulk upsert
    op.create_unique_constraint(
        "uq_evidence_nodes_disease_ref",
        "evidence_nodes",
        ["disease", "ref_id"],
    )

    # evidence_edges: unique (source, target, relation) for bulk upsert
    op.create_unique_constraint(
        "uq_evidence_edges_triple",
        "evidence_edges",
        ["source_node_id", "target_node_id", "relation_type"],
    )

    # embedding_cache: index on created_at for TTL eviction
    op.create_index(
        "idx_embedding_cache_created_at",
        "embedding_cache",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_embedding_cache_created_at", table_name="embedding_cache")
    op.drop_constraint(
        "uq_evidence_edges_triple", "evidence_edges", type_="unique"
    )
    op.drop_constraint(
        "uq_evidence_nodes_disease_ref", "evidence_nodes", type_="unique"
    )
