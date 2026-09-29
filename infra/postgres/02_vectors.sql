-- Case notes and their embeddings (pgvector). Kept in its own file so the
-- core schema never depends on the vector extension.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS case_notes (
    note_id     text PRIMARY KEY REFERENCES evidence (evidence_id),
    account_id  text NOT NULL,
    author      text NOT NULL,
    created_at  timestamptz NOT NULL,
    text        text NOT NULL,
    embedding   vector(768)          -- filled later by `python -m fusion.notes embed`
);

-- HNSW index for fast approximate nearest-neighbour search by cosine distance.
CREATE INDEX IF NOT EXISTS case_notes_embedding_hnsw
    ON case_notes USING hnsw (embedding vector_cosine_ops);
