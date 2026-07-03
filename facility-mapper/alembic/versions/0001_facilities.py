"""Create facilities and data_imports tables.

Revision ID: 0001
Revises: None
Create Date: 2026-07-01

This is the initial migration for the Facility Mapper service.
Creates the two core tables defined in app/models.py:

- facilities: authoritative facility data store (Postgres, not JSON files)
- data_imports: audit trail for data load operations

Design rationale:
A JSON file cannot be updated without redeploying the container. In a
country where hospitals open, close, lose ICU capacity, or change level
classifications, quarterly data refreshes are a patient safety requirement.
A database supports versioned updates, tracks data_as_of, and allows
level/service filter queries without loading 10,000 records into memory
on every request.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY

# revision identifiers, used by Alembic.
revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── facilities table ─────────────────────────────────────────────
    op.create_table(
        "facilities",
        sa.Column("facility_id", sa.String, primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("county", sa.Text, nullable=True),
        sa.Column("level", sa.Integer, nullable=False),
        sa.Column("lat", sa.Float, nullable=False),
        sa.Column("lon", sa.Float, nullable=False),
        sa.Column("phone", sa.Text, nullable=True),
        sa.Column("services", ARRAY(sa.Text), nullable=True),
        sa.Column(
            "is_active",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column("data_source", sa.Text, nullable=False),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    # Indexes for common query patterns
    op.create_index(
        "ix_facilities_level",
        "facilities",
        ["level"],
    )
    op.create_index(
        "ix_facilities_is_active",
        "facilities",
        ["is_active"],
    )
    op.create_index(
        "ix_facilities_county",
        "facilities",
        ["county"],
    )

    # ── data_imports table ───────────────────────────────────────────
    op.create_table(
        "data_imports",
        sa.Column(
            "id",
            sa.String,
            primary_key=True,
            server_default=sa.func.gen_random_uuid(),
        ),
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("record_count", sa.Integer, nullable=False),
        sa.Column(
            "loaded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("loaded_by", sa.Text, nullable=True),
    )

    op.create_index(
        "ix_data_imports_source",
        "data_imports",
        ["source"],
    )
    op.create_index(
        "ix_data_imports_loaded_at",
        "data_imports",
        ["loaded_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_data_imports_loaded_at", table_name="data_imports")
    op.drop_index("ix_data_imports_source", table_name="data_imports")
    op.drop_table("data_imports")
    op.drop_index("ix_facilities_county", table_name="facilities")
    op.drop_index("ix_facilities_is_active", table_name="facilities")
    op.drop_index("ix_facilities_level", table_name="facilities")
    op.drop_table("facilities")
