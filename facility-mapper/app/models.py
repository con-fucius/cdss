"""
facility-mapper/app/models.py

SQLAlchemy ORM models for the Facility Mapper service.

Two tables:
- facilities: the authoritative facility data store (Postgres, not JSON files)
- data_imports: audit trail for data load operations

Design rationale from IMPLEMENTATION PLAN:
A JSON file cannot be updated without redeploying the container. In a
country where hospitals open, close, lose ICU capacity, or change level
classifications, quarterly data refreshes are a patient safety requirement.
A database supports versioned upserts, tracks data_as_of, and allows
level/service filter queries without loading 10,000 records into memory
on every request.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base for all Facility Mapper models."""

    pass


class Facility(Base):
    """Health facility record.

    Fields match the data schema defined in IMPLEMENTATION PLAN Phase 1.2.
    The level field uses Kenya KEPH levels (1-6).
    """

    __tablename__ = "facilities"

    facility_id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    county: Mapped[str | None] = mapped_column(Text, nullable=True)
    level: Mapped[int] = mapped_column(Integer, nullable=False)
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lon: Mapped[float] = mapped_column(Float, nullable=False)
    phone: Mapped[str | None] = mapped_column(Text, nullable=True)
    services: Mapped[list[str] | None] = mapped_column(
        ARRAY(Text), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    data_source: Mapped[str] = mapped_column(Text, nullable=False)
    last_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class DataImport(Base):
    """Audit record for data load operations.

    Tracks when facility data was loaded, from what source, and how
    many records were processed. This is the data currency audit trail —
    critical for patient safety (stale facility data = wrong routing).
    """

    __tablename__ = "data_imports"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, server_default=func.gen_random_uuid()
    )
    source: Mapped[str] = mapped_column(Text, nullable=False)
    record_count: Mapped[int] = mapped_column(Integer, nullable=False)
    loaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    loaded_by: Mapped[str | None] = mapped_column(Text, nullable=True)
