#!/usr/bin/env python3
"""One-shot migration: copy all data from SQLite ai_hub.db → PostgreSQL.

Usage:
    SQLITE_PATH=ai_hub.db DATABASE_URL=postgresql://... python scripts/migrate_sqlite_to_pg.py

Idempotent: rows that already exist in PG are skipped (ON CONFLICT DO NOTHING).
Run order respects FK dependencies so re-runs on a partially-migrated DB are safe.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import sys
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

SQLITE_PATH = os.getenv("SQLITE_PATH", "ai_hub.db")
DATABASE_URL = os.getenv("DATABASE_URL", "")

def _coerce_embedding(row: dict) -> dict:
    if "embedding" in row and row["embedding"] is not None:
        row["embedding"] = bytes(row["embedding"])
    return row


# Tables in FK-safe insertion order.
# Each entry: (table_name, pk_columns, extra_transform_fn | None)
TABLES: list[tuple[str, list[str], object]] = [
    ("users", ["id"], None),
    ("sessions", ["id"], None),
    ("messages", ["id"], None),
    ("summaries", ["id"], None),
    ("memory_episodes", ["id"], None),
    ("memory_items", ["id"], None),
    ("memory_consolidations", ["id"], None),
    ("pinned_memories", ["id"], None),
    ("prediction_records", ["id"], None),
    ("api_keys", ["id"], None),
    ("usage_events", ["id"], None),
    ("failure_risk_events", ["id"], None),
    ("knowledge_cards", ["id"], None),
    ("knowledge_card_chunks", ["id"], _coerce_embedding),
]

# Tables with BIGSERIAL PKs — we must reset their sequences after bulk insert.
BIGSERIAL_TABLES = ["messages", "summaries"]


def _pg_table_exists(pg_conn, table: str) -> bool:
    row = pg_conn.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_name = %s",
        (table,),
    ).fetchone()
    return row is not None


def _read_sqlite_rows(sqlite_conn: sqlite3.Connection, table: str) -> list[dict]:
    sqlite_conn.row_factory = sqlite3.Row
    rows = sqlite_conn.execute(f"SELECT * FROM {table}").fetchall()  # noqa: S608
    return [dict(row) for row in rows]


def _build_upsert(table: str, columns: list[str], pk_columns: list[str]) -> str:
    cols = ", ".join(columns)
    placeholders = ", ".join(f"%({col})s" for col in columns)
    conflict_cols = ", ".join(pk_columns)
    return (
        f"INSERT INTO {table} ({cols}) VALUES ({placeholders})"
        f" ON CONFLICT ({conflict_cols}) DO NOTHING"
    )


def _migrate_table(
    sqlite_conn: sqlite3.Connection,
    pg_conn: psycopg.Connection,
    table: str,
    pk_columns: list[str],
    transform,
) -> int:
    if not _pg_table_exists(pg_conn, table):
        log.warning("Table %s does not exist in PG — skipping", table)
        return 0

    try:
        rows = _read_sqlite_rows(sqlite_conn, table)
    except sqlite3.OperationalError as exc:
        log.warning("Table %s not in SQLite (%s) — skipping", table, exc)
        return 0

    if not rows:
        log.info("%-35s 0 rows", table)
        return 0

    if transform:
        rows = [transform(row) for row in rows]

    columns = list(rows[0].keys())
    sql = _build_upsert(table, columns, pk_columns)

    inserted = 0
    with pg_conn.cursor() as cur:
        for row in rows:
            cur.execute(sql, row)
            inserted += cur.rowcount

    pg_conn.commit()
    log.info("%-35s %d / %d rows inserted", table, inserted, len(rows))
    return inserted


def _reset_sequences(pg_conn: psycopg.Connection) -> None:
    """Reset BIGSERIAL sequences so new inserts don't collide with migrated IDs."""
    for table in BIGSERIAL_TABLES:
        if not _pg_table_exists(pg_conn, table):
            continue
        pg_conn.execute(
            f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), COALESCE(MAX(id), 1)) FROM {table}"  # noqa: S608
        )
    pg_conn.commit()
    log.info("Sequences reset for: %s", ", ".join(BIGSERIAL_TABLES))


def main() -> None:
    if not DATABASE_URL:
        sys.exit("ERROR: DATABASE_URL is not set")

    sqlite_path = Path(SQLITE_PATH)
    if not sqlite_path.exists():
        sys.exit(f"ERROR: SQLite file not found: {sqlite_path}")

    log.info("Source SQLite: %s", sqlite_path.resolve())
    log.info("Target PG:     %s", DATABASE_URL.split("@")[-1])

    sqlite_conn = sqlite3.connect(str(sqlite_path))
    sqlite_conn.row_factory = sqlite3.Row

    with psycopg.connect(DATABASE_URL, row_factory=dict_row) as pg_conn:
        total = 0
        for table, pk_cols, transform in TABLES:
            total += _migrate_table(sqlite_conn, pg_conn, table, pk_cols, transform)

        _reset_sequences(pg_conn)

    sqlite_conn.close()
    log.info("Migration complete. Total rows inserted: %d", total)


if __name__ == "__main__":
    main()
