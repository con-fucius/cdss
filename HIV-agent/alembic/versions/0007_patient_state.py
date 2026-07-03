"""Add patient state tables.

Revision ID: 0007_patient_state
Revises: 0006_terminology_hardening
Create Date: 2026-06-14

Five tables for persistent patient state:
  - patient_encounters   (anchor; one row per clinical encounter)
  - patient_vitals       (NEWS2-capable vitals + computed scores per encounter)
  - patient_labs         (one row per lab_type per encounter; upsert-safe)
  - patient_medications  (active/stopped medications with RxCUI linkage)
  - patient_diagnoses    (active/resolved diagnoses with ICD-10 + evidence graph ref)

Design decisions:
- patient_ref is ALWAYS the hashed output of patient_ref_from_context() —
  never a raw patient identifier. This is enforced by convention at every
  write site; this migration does not enforce it at DB level (hash is opaque text).
- patient_labs has a UNIQUE constraint on (encounter_id, lab_type) so that
  repeated upserts for the same lab in the same encounter overwrite, not duplicate.
- All tables index patient_ref for efficient patient-state aggregation.
- encounter_id is a UUID primary key used as FK in all child tables.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007_patient_state"
down_revision = "0006_terminology_hardening"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── patient_encounters ────────────────────────────────────────────
    op.create_table(
        "patient_encounters",
        sa.Column(
            "encounter_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("patient_ref", sa.Text, nullable=False),
        sa.Column("disease_scope", sa.Text, nullable=False),
        sa.Column(
            "encounter_date",
            sa.Date,
            nullable=False,
            server_default=sa.text("CURRENT_DATE"),
        ),
        sa.Column(
            "encounter_type",
            sa.Text,
            nullable=False,
            server_default="initial",
        ),
        sa.Column("clinician_role", sa.Text),
        sa.Column("facility_level", sa.Text),
        sa.Column("notes", sa.Text),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "encounter_type IN ('initial', 'follow_up', 'emergency')",
            name="ck_encounter_type",
        ),
    )
    op.create_index("idx_pe_patient_ref", "patient_encounters", ["patient_ref"])
    op.create_index("idx_pe_encounter_date", "patient_encounters", ["encounter_date"])
    op.create_index(
        "idx_pe_patient_ref_date",
        "patient_encounters",
        ["patient_ref", "encounter_date"],
    )

    # ── patient_vitals ────────────────────────────────────────────────
    op.create_table(
        "patient_vitals",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "encounter_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("patient_encounters.encounter_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("patient_ref", sa.Text, nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("bp_systolic", sa.Integer),
        sa.Column("bp_diastolic", sa.Integer),
        sa.Column("heart_rate", sa.Integer),
        sa.Column("respiratory_rate", sa.Integer),
        sa.Column("temperature", sa.Numeric(4, 1)),
        sa.Column("spo2", sa.Integer),
        sa.Column("weight_kg", sa.Numeric(5, 1)),
        sa.Column("height_cm", sa.Numeric(5, 1)),
        sa.Column("consciousness", sa.Text),           # A/V/P/U per AVPU scale
        sa.Column("supplemental_o2", sa.Boolean),
        sa.Column("spo2_scale", sa.Integer),           # 1 or 2 per NEWS2
        # Computed scores stored after calculation — NULL until scorer runs
        sa.Column("news2_score", sa.Integer),
        sa.Column("news2_risk", sa.Text),              # low / low-medium / medium / high
        sa.Column("bmi", sa.Numeric(4, 1)),
    )
    op.create_index("idx_pv_patient_ref", "patient_vitals", ["patient_ref"])
    op.create_index("idx_pv_encounter_id", "patient_vitals", ["encounter_id"])
    op.create_index("idx_pv_recorded_at", "patient_vitals", ["recorded_at"])

    # ── patient_labs ──────────────────────────────────────────────────
    op.create_table(
        "patient_labs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "encounter_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("patient_encounters.encounter_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("patient_ref", sa.Text, nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("lab_type", sa.Text, nullable=False),
        sa.Column("value", sa.Numeric),
        sa.Column("unit", sa.Text),
        sa.Column("reference_low", sa.Numeric),
        sa.Column("reference_high", sa.Numeric),
        sa.Column("flag", sa.Text, server_default="normal"),
        sa.Column("source", sa.Text, nullable=False, server_default="entered"),
        sa.UniqueConstraint(
            "encounter_id", "lab_type", name="uq_pl_encounter_lab_type"
        ),
    )
    op.create_index("idx_pl_patient_ref", "patient_labs", ["patient_ref"])
    op.create_index("idx_pl_encounter_id", "patient_labs", ["encounter_id"])
    op.create_index("idx_pl_lab_type", "patient_labs", ["lab_type"])
    op.create_index("idx_pl_flag", "patient_labs", ["flag"])

    # ── patient_medications ───────────────────────────────────────────
    op.create_table(
        "patient_medications",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "encounter_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("patient_encounters.encounter_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("patient_ref", sa.Text, nullable=False),
        sa.Column("drug_name", sa.Text, nullable=False),
        sa.Column("generic_name", sa.Text),
        sa.Column("rxcui", sa.Text),
        sa.Column("dose", sa.Text),
        sa.Column("frequency", sa.Text),
        sa.Column("route", sa.Text),
        sa.Column("started_date", sa.Date),
        sa.Column("stopped_date", sa.Date),
        sa.Column(
            "status",
            sa.Text,
            nullable=False,
            server_default="active",
        ),
        sa.Column("indication", sa.Text),
        sa.Column("prescribed_by", sa.Text),
        sa.CheckConstraint(
            "status IN ('active', 'stopped', 'suspended')",
            name="ck_medication_status",
        ),
    )
    op.create_index("idx_pm_patient_ref", "patient_medications", ["patient_ref"])
    op.create_index("idx_pm_encounter_id", "patient_medications", ["encounter_id"])
    op.create_index("idx_pm_status", "patient_medications", ["status"])
    op.create_index("idx_pm_drug_name", "patient_medications", ["drug_name"])

    # ── patient_diagnoses ─────────────────────────────────────────────
    op.create_table(
        "patient_diagnoses",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "encounter_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("patient_encounters.encounter_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("patient_ref", sa.Text, nullable=False),
        sa.Column("condition_ref", sa.Text),           # evidence graph ref_id
        sa.Column("condition_name", sa.Text, nullable=False),
        sa.Column("icd10_code", sa.Text),
        sa.Column(
            "status",
            sa.Text,
            nullable=False,
            server_default="active",
        ),
        sa.Column("onset_date", sa.Date),
        sa.Column("resolved_date", sa.Date),
        sa.Column("severity", sa.Text),
        sa.Column("confirmed_by", sa.Text),
        sa.CheckConstraint(
            "status IN ('active', 'resolved', 'suspected')",
            name="ck_diagnosis_status",
        ),
    )
    op.create_index("idx_pd_patient_ref", "patient_diagnoses", ["patient_ref"])
    op.create_index("idx_pd_encounter_id", "patient_diagnoses", ["encounter_id"])
    op.create_index("idx_pd_status", "patient_diagnoses", ["status"])


def downgrade() -> None:
    op.drop_table("patient_diagnoses")
    op.drop_table("patient_medications")
    op.drop_table("patient_labs")
    op.drop_table("patient_vitals")
    op.drop_table("patient_encounters")
