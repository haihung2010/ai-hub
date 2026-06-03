#!/usr/bin/env python3
"""Create ihi_case_embeddings table for vector similarity search. Idempotent."""
import os
import sys

import psycopg


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS ihi_case_embeddings (
    case_id INTEGER PRIMARY KEY REFERENCES ihi_rag_cases(id) ON DELETE CASCADE,
    embedding vector(384),
    model_version VARCHAR(50) DEFAULT 'paraphrase-multilingual-MiniLM-L12-v2',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ihi_case_embeddings_ivfflat
    ON ihi_case_embeddings USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 10);
"""


def main():
    db_url = os.environ.get("DATABASE_URL", "postgresql://aihub:aihub_pass@localhost:5432/ai_hub")
    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
            cur.execute(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_name = 'ihi_case_embeddings' ORDER BY ordinal_position;"
            )
            rows = cur.fetchall()
            assert len(rows) == 4, f"Expected 4 columns, got {len(rows)}"
            assert rows[1][1] == "USER-DEFINED", f"embedding column should be vector type, got {rows[1][1]}"
            print("ihi_case_embeddings table ready (4 columns, vector(384) + ivfflat index)")
        conn.commit()


if __name__ == "__main__":
    main()
