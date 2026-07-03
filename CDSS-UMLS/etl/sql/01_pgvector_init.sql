-- Initialize pgvector extension for vector similarity search

-- Enable pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- Verify extension
SELECT * FROM pg_extension WHERE extname = 'vector';

-- Create vector index for similarity search (if not exists)
-- This will be created after documents are indexed
-- CREATE INDEX IF NOT EXISTS idx_clinical_documents_embedding ON clinical_documents 
-- USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

-- Note: ivfflat index should be created after loading substantial data
-- For initial setup, use sequential scan until enough data is loaded

