#!/usr/bin/env python3
"""Create ihi_device_overrides table. Idempotent."""
import os
import sys

import psycopg


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS ihi_device_overrides (
    id              SERIAL PRIMARY KEY,
    device_id       VARCHAR(50) NOT NULL,
    measurement     VARCHAR(50) NOT NULL,
    min_value       REAL,
    max_value       REAL,
    severity        VARCHAR(20) NOT NULL,
    source          VARCHAR(50) NOT NULL DEFAULT 'manual',
    set_by          VARCHAR(100),
    note            TEXT,
    valid_from      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    valid_to        TIMESTAMP,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(device_id, measurement)
);
CREATE INDEX IF NOT EXISTS idx_overrides_device
    ON ihi_device_overrides(device_id) WHERE valid_to IS NULL;
"""


def main():
    db_url = os.environ.get("DATABASE_URL", "postgresql://aihub:aihub_pass@localhost:5432/ai_hub")
    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
            cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'ihi_device_overrides' ORDER BY ordinal_position;"
            )
            cols = [r[0] for r in cur.fetchall()]
            expected = [
                "id", "device_id", "measurement", "min_value", "max_value",
                "severity", "source", "set_by", "note",
                "valid_from", "valid_to", "created_at",
            ]
            assert cols == expected, f"Schema mismatch: got {cols}, expected {expected}"
            print(f"ihi_device_overrides table ready ({len(cols)} columns)")
        conn.commit()


if __name__ == "__main__":
    main()
