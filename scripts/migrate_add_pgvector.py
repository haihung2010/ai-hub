#!/usr/bin/env python3
"""Enable the pgvector extension on the AI Hub PostgreSQL database.

This migration is idempotent — running it multiple times is a no-op after
the first successful run. Safe to invoke as part of deployment bootstrap.

Usage:
    DATABASE_URL=postgresql://aihub:aihub_pass@localhost:5432/ai_hub \\
        ./venv/bin/python scripts/migrate_add_pgvector.py
"""

from __future__ import annotations

import os
import sys

import psycopg


DEFAULT_DB_URL = "postgresql://aihub:aihub_pass@localhost:5432/ai_hub"


def main() -> int:
    db_url = os.environ.get("DATABASE_URL", DEFAULT_DB_URL)
    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
            cur.execute(
                "SELECT extname, extversion FROM pg_extension WHERE extname = 'vector';"
            )
            row = cur.fetchone()
            if row is None:
                print(
                    "ERROR: pgvector extension not found after CREATE",
                    file=sys.stderr,
                )
                return 1
            ext_name, ext_version = row
            print(f"pgvector extension enabled (version {ext_version})")
        conn.commit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
