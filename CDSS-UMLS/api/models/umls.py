"""UMLS data models."""

import uuid

from sqlalchemy import ARRAY, Column, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID

from api.db import Base


class UMLSConcept(Base):
    """UMLS Concept model."""

    __tablename__ = "umls_concepts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    cui = Column(String(8), unique=True, nullable=False, index=True)
    preferred_name = Column(String(500), nullable=False)
    definition = Column(Text)
    semantic_types = Column(ARRAY(String), nullable=False)
    synonyms = Column(ARRAY(String))
    meta_data = Column(
        JSONB, name="metadata"
    )  # Use meta_data as attribute name, but keep 'metadata' as column name

    __table_args__ = (
        Index("idx_cui", "cui"),
        Index("idx_semantic_types", "semantic_types", postgresql_using="gin"),
    )


class UMLSRelation(Base):
    """UMLS Semantic Relation model."""

    __tablename__ = "umls_relations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    cui1 = Column(String(8), nullable=False, index=True)
    cui2 = Column(String(8), nullable=False, index=True)
    relation_type = Column(String(50), nullable=False)
    relation_label = Column(String(200))
    meta_data = Column(
        JSONB, name="metadata"
    )  # Use meta_data as attribute name, but keep 'metadata' as column name

    __table_args__ = (
        Index("idx_cui1_cui2", "cui1", "cui2"),
        Index("idx_relation_type", "relation_type"),
    )


class ClinicalDocument(Base):
    """Clinical document for RAG."""

    __tablename__ = "clinical_documents"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    text = Column(Text, nullable=False)
    source = Column(String(500))
    umls_concepts = Column(ARRAY(String))
    embedding = Column(Text)  # Vector stored as text, converted in queries
    meta_data = Column(
        JSONB, name="metadata"
    )  # Use meta_data as attribute name, but keep 'metadata' as column name
    created_at = Column(String(50))

    __table_args__ = (
        Index("idx_source", "source"),
        Index("idx_umls_concepts", "umls_concepts", postgresql_using="gin"),
    )
