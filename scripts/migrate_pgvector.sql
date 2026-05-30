-- ============================================================
--  AI Hub: pgvector + FTS Migration
--  Chay: PGPASSWORD=aihub_pass psql -h localhost -U aihub -d ai_hub -f scripts/migrate_pgvector.sql
-- ============================================================

-- 1. Enable pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- 2. Add vector column to knowledge_card_chunks
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'knowledge_card_chunks' AND column_name = 'embedding_vec'
  ) THEN
    ALTER TABLE knowledge_card_chunks ADD COLUMN embedding_vec vector(384);
    RAISE NOTICE 'Added embedding_vec column';
  ELSE
    RAISE NOTICE 'embedding_vec column already exists';
  END IF;
END $$;

-- 3. Add tsvector column for full-text search
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'knowledge_card_chunks' AND column_name = 'content_tsv'
  ) THEN
    ALTER TABLE knowledge_card_chunks ADD COLUMN content_tsv tsvector;
    RAISE NOTICE 'Added content_tsv column';
  ELSE
    RAISE NOTICE 'content_tsv column already exists';
  END IF;
END $$;

-- 4. Populate content_tsv from content
UPDATE knowledge_card_chunks
SET content_tsv = to_tsvector('simple', COALESCE(content, ''))
WHERE content_tsv IS NULL;

-- 5. Populate embedding_vec from raw bytes
--    (Python migration script is better for this — run scripts/migrate_embeddings.py)
--    This is a placeholder; the Python script handles binary -> float[] conversion.

-- 6. Create HNSW index for vector search (fast approximate nearest neighbor)
CREATE INDEX IF NOT EXISTS idx_chunks_embedding_hnsw
  ON knowledge_card_chunks
  USING hnsw (embedding_vec vector_cosine_ops)
  WITH (m = 16, ef_construction = 200);

-- 7. Create GIN index for full-text search
CREATE INDEX IF NOT EXISTS idx_chunks_content_tsv
  ON knowledge_card_chunks
  USING gin(content_tsv);

-- 8. Trigger to auto-update content_tsv on INSERT/UPDATE
CREATE OR REPLACE FUNCTION update_chunk_tsv() RETURNS trigger AS $$
BEGIN
  NEW.content_tsv := to_tsvector('simple', COALESCE(NEW.content, ''));
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_chunk_tsv ON knowledge_card_chunks;
CREATE TRIGGER trg_chunk_tsv
  BEFORE INSERT OR UPDATE OF content ON knowledge_card_chunks
  FOR EACH ROW EXECUTE FUNCTION update_chunk_tsv();

-- 9. Verify
SELECT
  'pgvector' AS feature,
  CASE WHEN EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector')
    THEN 'OK' ELSE 'MISSING' END AS status
UNION ALL
SELECT
  'embedding_vec column',
  CASE WHEN EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'knowledge_card_chunks' AND column_name = 'embedding_vec'
  ) THEN 'OK' ELSE 'MISSING' END
UNION ALL
SELECT
  'content_tsv column',
  CASE WHEN EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'knowledge_card_chunks' AND column_name = 'content_tsv'
  ) THEN 'OK' ELSE 'MISSING' END
UNION ALL
SELECT
  'hnsw index',
  CASE WHEN EXISTS (
    SELECT 1 FROM pg_indexes WHERE indexname = 'idx_chunks_embedding_hnsw'
  ) THEN 'OK' ELSE 'MISSING' END
UNION ALL
SELECT
  'tsvector index',
  CASE WHEN EXISTS (
    SELECT 1 FROM pg_indexes WHERE indexname = 'idx_chunks_content_tsv'
  ) THEN 'OK' ELSE 'MISSING' END
UNION ALL
SELECT
  'tsv trigger',
  CASE WHEN EXISTS (
    SELECT 1 FROM pg_trigger WHERE tgname = 'trg_chunk_tsv'
  ) THEN 'OK' ELSE 'MISSING' END;
