-- RAG knowledge base for TestLab (Postgres 15+, Neon).
-- Separate from testlab_schema.sql on purpose: that file is replayed into a fresh
-- schema by every test, and this one needs the pgvector extension.
--
-- Apply once per Neon branch (use the DIRECT connection string, not the pooler):
--   psql "$DATABASE_URL_DIRECT" -f db/rag_schema.sql
--
-- Embedding dimension is fixed at table creation. 1536 matches OpenAI
-- text-embedding-3-small; change it here if you use another model, and re-ingest.

BEGIN;

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS kb_documents (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id    uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    source        text NOT NULL,            -- what expected.source in test cases refers to
    title         text,
    content_hash  text NOT NULL,            -- sha256 of the text: unchanged docs are skipped on re-ingest
    metadata      jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (project_id, source)
);

CREATE TABLE IF NOT EXISTS kb_chunks (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id      uuid NOT NULL REFERENCES kb_documents(id) ON DELETE CASCADE,
    project_id       uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,  -- denormalised: every search filters on it
    position         integer NOT NULL,
    text             text NOT NULL,
    heading          text,
    embedding        vector(1536),
    embedding_model  text,
    tsv              tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED,
    created_at       timestamptz NOT NULL DEFAULT now(),
    UNIQUE (document_id, position)
);

CREATE INDEX IF NOT EXISTS idx_kb_documents_project ON kb_documents(project_id);
CREATE INDEX IF NOT EXISTS idx_kb_chunks_project    ON kb_chunks(project_id);
CREATE INDEX IF NOT EXISTS idx_kb_chunks_document   ON kb_chunks(document_id);
CREATE INDEX IF NOT EXISTS idx_kb_chunks_tsv        ON kb_chunks USING gin (tsv);
CREATE INDEX IF NOT EXISTS idx_kb_chunks_embedding  ON kb_chunks USING hnsw (embedding vector_cosine_ops);

COMMIT;
