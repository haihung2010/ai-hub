#!/usr/bin/env python3
"""Migrate raw-byte embeddings to pgvector column.

Chay sau khi da apply migrate_pgvector.sql:
  cd /home/hung/ai-hub
  ./venv/bin/python scripts/migrate_embeddings.py
"""

import os
import struct
import sys

import psycopg

DB_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://aihub:aihub_pass@localhost:5432/ai_hub",
)


def bytes_to_vector(raw: bytes, dim: int = 384) -> str:
    """Convert raw float32 bytes to pgvector literal string '[0.1,0.2,...]'."""
    if not raw or len(raw) < dim * 4:
        return None
    floats = struct.unpack(f"{dim}f", raw[: dim * 4])
    return "[" + ",".join(f"{v:.8f}" for v in floats) + "]"


def main():
    conn = psycopg.connect(DB_URL)
    conn.row_factory = psycopg.rows.dict_row

    # Check how many need migration
    row = conn.execute(
        "SELECT count(*) AS n FROM knowledge_card_chunks "
        "WHERE embedding IS NOT NULL AND embedding_vec IS NULL"
    ).fetchone()
    total = row["n"]
    print(f"Chunks to migrate: {total}")

    if total == 0:
        print("Nothing to migrate.")
        conn.close()
        return

    # Fetch all rows with raw embedding but no vector
    rows = conn.execute(
        "SELECT id, embedding FROM knowledge_card_chunks "
        "WHERE embedding IS NOT NULL AND embedding_vec IS NULL"
    ).fetchall()

    migrated = 0
    failed = 0
    for r in rows:
        vec_str = bytes_to_vector(r["embedding"])
        if vec_str is None:
            failed += 1
            continue
        try:
            conn.execute(
                "UPDATE knowledge_card_chunks SET embedding_vec = %s::vector WHERE id = %s",
                (vec_str, r["id"]),
            )
            migrated += 1
        except Exception as e:
            print(f"  FAIL id={r['id']}: {e}")
            failed += 1
            conn.rollback()
            continue

    conn.commit()

    # Verify
    row = conn.execute(
        "SELECT count(*) AS n FROM knowledge_card_chunks WHERE embedding_vec IS NOT NULL"
    ).fetchone()

    print(f"Done: migrated={migrated}, failed={failed}, total_vec={row['n']}")
    conn.close()


if __name__ == "__main__":
    main()
