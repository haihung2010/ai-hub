#!/usr/bin/env python3
"""Selective truncate of test-bed data. Keeps api_keys, auth_failures, rate_limit_buckets.

Usage: ./venv/bin/python scripts/testbed/reset.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from app.core.database import get_db_connection, init_db  # noqa: E402

EPHEMERAL_TABLES = (
    "messages",
    "summaries",
    "memory_items",
    "memory_episodes",
    "memory_consolidations",
    "pinned_memories",
    "fanpage_facts",
    "prediction_records",
    "failure_risk_events",
    "usage_events",
    "knowledge_card_chunks",
    "knowledge_cards",
    "sessions",
    "users",
)


def main() -> int:
    init_db()
    with get_db_connection() as conn:
        rows = conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname='public'"
        ).fetchall()
        existing = {row["tablename"] for row in rows}
        tables = [t for t in EPHEMERAL_TABLES if t in existing]
        if not tables:
            print("[reset] no ephemeral tables found")
            return 0
        conn.execute(
            "TRUNCATE TABLE " + ", ".join(tables) + " RESTART IDENTITY CASCADE"
        )
        conn.commit()
    print(f"[reset] truncated {len(tables)} tables: {', '.join(tables)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
