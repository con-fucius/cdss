-- Additional index optimizations for performance

-- Composite indexes for common queries
CREATE INDEX IF NOT EXISTS idx_umls_relations_cui1_cui2 ON umls_relations(cui1, cui2);
CREATE INDEX IF NOT EXISTS idx_umls_relations_type_cui1 ON umls_relations(relation_type, cui1);

-- Full-text search indexes
CREATE INDEX IF NOT EXISTS idx_umls_concepts_name_fts ON umls_concepts USING GIN(to_tsvector('english', preferred_name));
CREATE INDEX IF NOT EXISTS idx_clinical_documents_text_fts ON clinical_documents USING GIN(to_tsvector('english', text));

-- Partial indexes for common filters
CREATE INDEX IF NOT EXISTS idx_umls_concepts_preferred ON umls_concepts(cui) WHERE preferred_name IS NOT NULL;

-- Statistics for query planner
ANALYZE umls_concepts;
ANALYZE umls_relations;
ANALYZE clinical_documents;

