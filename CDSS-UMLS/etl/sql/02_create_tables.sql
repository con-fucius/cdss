-- Create tables for UMLS CDSS

-- UMLS Concepts
CREATE TABLE IF NOT EXISTS umls_concepts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    cui VARCHAR(8) UNIQUE NOT NULL,
    preferred_name VARCHAR(500) NOT NULL,
    definition TEXT,
    semantic_types TEXT[],
    synonyms TEXT[],
    metadata JSONB,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- UMLS Relations
CREATE TABLE IF NOT EXISTS umls_relations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    cui1 VARCHAR(8) NOT NULL,
    cui2 VARCHAR(8) NOT NULL,
    relation_type VARCHAR(50) NOT NULL,
    relation_label VARCHAR(200),
    metadata JSONB,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Clinical Documents for RAG
CREATE TABLE IF NOT EXISTS clinical_documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    text TEXT NOT NULL,
    source VARCHAR(500),
    umls_concepts TEXT[],
    embedding vector(384),  -- For pgvector
    metadata JSONB,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_umls_concepts_cui ON umls_concepts(cui);
CREATE INDEX IF NOT EXISTS idx_umls_concepts_semantic_types ON umls_concepts USING GIN(semantic_types);
CREATE INDEX IF NOT EXISTS idx_umls_relations_cui1 ON umls_relations(cui1);
CREATE INDEX IF NOT EXISTS idx_umls_relations_cui2 ON umls_relations(cui2);
CREATE INDEX IF NOT EXISTS idx_umls_relations_type ON umls_relations(relation_type);
CREATE INDEX IF NOT EXISTS idx_clinical_documents_source ON clinical_documents(source);
CREATE INDEX IF NOT EXISTS idx_clinical_documents_concepts ON clinical_documents USING GIN(umls_concepts);

