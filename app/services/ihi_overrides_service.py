"""PG CRUD for ihi_device_overrides table.

Trust hierarchy integration: get_active_override() is consulted BEFORE
default thresholds in loader.get_effective_threshold().
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class DeviceOverride:
    """A single (device, measurement) threshold override."""
    device_id: str
    measurement: str
    min_value: float | None
    max_value: float | None
    severity: str           # "NORMAL" / "WARNING" / "DANGER"
    source: str             # "manual" / "auto_learned"
    set_by: str | None
    note: str | None


def get_active_override(db_pool, device_id: str, measurement: str) -> Optional[DeviceOverride]:
    """Return active override for (device, measurement) or None.

    "Active" = valid_from <= now AND (valid_to IS NULL OR valid_to > now).
    """
    if db_pool is None:
        return None
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT device_id, measurement, min_value, max_value, severity,
                       source, set_by, note
                FROM ihi_device_overrides
                WHERE device_id = %s AND measurement = %s
                  AND valid_from <= CURRENT_TIMESTAMP
                  AND (valid_to IS NULL OR valid_to > CURRENT_TIMESTAMP)
                ORDER BY created_at DESC
                LIMIT 1
            """, (device_id, measurement))
            row = cur.fetchone()
            if row is None:
                return None
            return DeviceOverride(
                device_id=row[0], measurement=row[1],
                min_value=row[2], max_value=row[3],
                severity=row[4], source=row[5],
                set_by=row[6], note=row[7],
            )


def set_override(db_pool, device_id: str, measurement: str,
                 min_value: float | None, max_value: float | None,
                 severity: str, source: str = "manual",
                 set_by: str | None = None, note: str | None = None) -> int:
    """Insert or update an override. Returns the row id."""
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO ihi_device_overrides
                (device_id, measurement, min_value, max_value, severity, source, set_by, note)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (device_id, measurement) DO UPDATE
                SET min_value = EXCLUDED.min_value,
                    max_value = EXCLUDED.max_value,
                    severity = EXCLUDED.severity,
                    source = EXCLUDED.source,
                    set_by = EXCLUDED.set_by,
                    note = EXCLUDED.note,
                    valid_from = CURRENT_TIMESTAMP,
                    valid_to = NULL
                RETURNING id
            """, (device_id, measurement, min_value, max_value, severity, source, set_by, note))
            row_id = cur.fetchone()[0]
        conn.commit()
        return row_id


def delete_override(db_pool, device_id: str, measurement: str) -> bool:
    """Delete an override. Returns True if a row was deleted."""
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                DELETE FROM ihi_device_overrides
                WHERE device_id = %s AND measurement = %s
            """, (device_id, measurement))
            deleted = cur.rowcount > 0
        conn.commit()
        return deleted
