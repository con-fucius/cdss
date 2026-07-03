"""Incident data model — Phase 1.

Revision ID: 0001_incidents
Revises:
Create Date: 2026-06-21

Creates the six tables that make up the Ambulance CDSS incident model:
  - incidents                     (root record, single call-to-handoff lifecycle)
  - incident_dispatch_log         (append-only Mode 1 transcript)
  - incident_field_log            (append-only paramedic-side log)
  - incident_vitals               (vitals + computed scores at write time)
  - incident_medications_given    (narrow prehospital formulary, schema-ready)
  - guidance_lookup_log           (Mode 2 usage, separate from dispatch log)

See app/models.py for full column rationale.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001_incidents"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "incidents",
        sa.Column(
            "incident_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(32),
            nullable=False,
            server_default="received",
        ),
        sa.Column("priority_code", sa.String(32)),
        sa.Column("chief_complaint", sa.Text, nullable=False),
        sa.Column("caller_location_lat", sa.Float),
        sa.Column("caller_location_lon", sa.Float),
        sa.Column("caller_location_text", sa.Text),
        sa.Column("dispatch_protocol_id", sa.String(128)),
        sa.Column("dispatch_protocol_version", sa.String(64)),
        sa.Column("dispatch_protocol_snapshot", postgresql.JSONB),
        sa.Column("assigned_unit_id", sa.String(128)),
        sa.Column("recommended_unit_type", sa.String(64)),
        sa.Column("routed_facility_id", sa.String(128)),
        sa.Column("routed_facility_name", sa.String(256)),
        sa.Column("dispatched_at", sa.DateTime(timezone=True)),
        sa.Column("on_scene_at", sa.DateTime(timezone=True)),
        sa.Column("transporting_at", sa.DateTime(timezone=True)),
        sa.Column("handoff_complete_at", sa.DateTime(timezone=True)),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.Column("pii_purged_at", sa.DateTime(timezone=True)),
    )
    op.create_index("idx_incidents_status", "incidents", ["status"])
    op.create_index("idx_incidents_created_at", "incidents", ["created_at"])

    op.create_table(
        "incident_dispatch_log",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "incident_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("incidents.incident_id"),
            nullable=False,
        ),
        sa.Column("question_id", sa.String(128), nullable=False),
        sa.Column("question_text", sa.Text, nullable=False),
        sa.Column("answer", sa.Text, nullable=False),
        sa.Column("protocol_version", sa.String(64), nullable=False),
        sa.Column(
            "is_backtrack", sa.Boolean, nullable=False, server_default=sa.false()
        ),
        sa.Column(
            "timestamp",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "idx_dispatch_log_incident", "incident_dispatch_log", ["incident_id"]
    )

    op.create_table(
        "incident_field_log",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "incident_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("incidents.incident_id"),
            nullable=False,
        ),
        sa.Column("step_id", sa.String(128), nullable=False),
        sa.Column("action_type", sa.String(32), nullable=False),
        sa.Column(
            "data", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'")
        ),
        sa.Column("recorded_by", sa.String(128), nullable=False),
        sa.Column(
            "timestamp",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("idx_field_log_incident", "incident_field_log", ["incident_id"])

    op.create_table(
        "incident_vitals",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "incident_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("incidents.incident_id"),
            nullable=False,
        ),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("recorded_by", sa.String(32), nullable=False),
        sa.Column("respiratory_rate", sa.Integer),
        sa.Column("spo2", sa.Integer),
        sa.Column("spo2_scale", sa.Integer),
        sa.Column("supplemental_o2", sa.Boolean),
        sa.Column("bp_systolic", sa.Integer),
        sa.Column("bp_diastolic", sa.Integer),
        sa.Column("heart_rate", sa.Integer),
        sa.Column("consciousness", sa.String(4)),
        sa.Column("temperature", sa.Float),
        sa.Column("gcs_eye", sa.Integer),
        sa.Column("gcs_verbal", sa.Integer),
        sa.Column("gcs_motor", sa.Integer),
        sa.Column("news2_score", sa.Integer),
        sa.Column("news2_risk_level", sa.String(16)),
        sa.Column("gcs_total", sa.Integer),
    )
    op.create_index("idx_vitals_incident", "incident_vitals", ["incident_id"])
    op.create_index("idx_vitals_recorded_at", "incident_vitals", ["recorded_at"])

    op.create_table(
        "incident_medications_given",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "incident_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("incidents.incident_id"),
            nullable=False,
        ),
        sa.Column("drug_name", sa.String(256), nullable=False),
        sa.Column("dose", sa.String(128), nullable=False),
        sa.Column("route", sa.String(64), nullable=False),
        sa.Column(
            "given_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("given_by", sa.String(128), nullable=False),
    )
    op.create_index(
        "idx_meds_given_incident", "incident_medications_given", ["incident_id"]
    )

    op.create_table(
        "guidance_lookup_log",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "incident_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("incidents.incident_id"),
            nullable=False,
        ),
        sa.Column("question_id", sa.String(128)),
        sa.Column("query_text", sa.Text, nullable=False),
        sa.Column("result_summary", sa.Text, nullable=False),
        sa.Column("dispatcher_id", sa.String(128), nullable=False),
        sa.Column(
            "timestamp",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "idx_guidance_log_incident", "guidance_lookup_log", ["incident_id"]
    )


def downgrade() -> None:
    op.drop_table("guidance_lookup_log")
    op.drop_table("incident_medications_given")
    op.drop_table("incident_vitals")
    op.drop_table("incident_field_log")
    op.drop_table("incident_dispatch_log")
    op.drop_table("incidents")
