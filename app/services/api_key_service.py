"""Virtual API key lookup and small-team policy metadata.

P2.4 (2026-06-10): secret rotation policy. Each row tracks
``last_rotated_at``; the rotation scheduler reads it to warn
operators before the deadline passes. ``rotate_key()`` mints a
new key_hash in-place so callers get a fresh secret without
having to update a row id.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import hashlib

from app.core.database import get_db_connection


# P2.4: rotation cadence (days). Override per-key in the api_keys
# table (e.g. for high-value service accounts you may want a tighter
# cadence). The constants here are the defaults applied at
# migration time and to keys that don't override.
DEFAULT_API_KEY_ROTATION_DAYS = 90
DEFAULT_WEBHOOK_HMAC_ROTATION_DAYS = 180
DEFAULT_DB_PASSWORD_ROTATION_DAYS = 180
# MiniMax key rotation cadence is governed by MiniMax's own policy
# (typically 60-90d). We log a reminder if we haven't seen a
# rotation in that long, but we don't have a DB row for the
# MiniMax key — we just check the env var presence.
DEFAULT_MINIMAX_KEY_ROTATION_DAYS = 75


@dataclass(frozen=True)
class ApiKeyRecord:
    id: str
    tenant_id: str
    name: str
    allow_external: bool
    rpm_limit: int
    max_parallel_requests: int
    is_admin: bool = False
    monthly_budget_usd: float | None = None
    allowed_projects: list[str] | None = None
    last_rotated_at: str | None = None
    created_at: str | None = None


class ApiKeyService:
    @staticmethod
    def hash_key(raw_key: str) -> str:
        return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()

    def lookup(self, raw_key: str) -> ApiKeyRecord | None:
        key_hash = self.hash_key(raw_key)
        with get_db_connection() as conn:
            row = conn.execute(
                "SELECT id, tenant_id, name, allow_external, rpm_limit, max_parallel_requests, monthly_budget_usd, is_admin, allowed_projects_json, last_rotated_at, created_at "
                "FROM api_keys WHERE key_hash = %s AND enabled = 1 "
                "AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP)",
                (key_hash,),
            ).fetchone()
        if row is None:
            return None
        import json as _json
        _ap = row["allowed_projects_json"]
        allowed_projects = _json.loads(_ap) if _ap and _ap != '[]' else None
        return ApiKeyRecord(
            id=row["id"],
            tenant_id=row["tenant_id"],
            name=row["name"],
            allow_external=bool(row["allow_external"]),
            rpm_limit=int(row["rpm_limit"]),
            max_parallel_requests=int(row["max_parallel_requests"]),
            is_admin=bool(row["is_admin"]),
            monthly_budget_usd=float(row["monthly_budget_usd"]) if row["monthly_budget_usd"] is not None else None,
            allowed_projects=allowed_projects,
            last_rotated_at=row["last_rotated_at"].isoformat() if row.get("last_rotated_at") else None,
            created_at=row["created_at"].isoformat() if row.get("created_at") else None,
        )

    @staticmethod
    def get_monthly_spend(api_key_id: str) -> float:
        start_of_month = date.today().replace(day=1).isoformat()
        with get_db_connection() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(cost_usd), 0) AS total FROM usage_events "
                "WHERE api_key_id = %s AND created_at >= %s::date",
                (api_key_id, start_of_month),
            ).fetchone()
        return float(row["total"] or 0)

    def create_key(
        self,
        name: str,
        tenant_id: str = "default",
        is_admin: bool = False,
        rpm_limit: int = 60,
        monthly_budget_usd: float | None = None,
    ) -> tuple[str, str]:
        import secrets
        from uuid import uuid4
        raw_key = f"ah_{secrets.token_urlsafe(32)}"
        key_id = f"ak_{uuid4().hex[:12]}"
        key_hash = self.hash_key(raw_key)

        with get_db_connection() as conn:
            conn.execute(
                """INSERT INTO api_keys (id, key_hash, name, tenant_id, is_admin, rpm_limit, monthly_budget_usd, last_rotated_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)""",
                (key_id, key_hash, name, tenant_id, int(is_admin), rpm_limit, monthly_budget_usd),
            )
            conn.commit()
        return key_id, raw_key

    # ── P2.4: rotation ───────────────────────────────────────────────

    def rotate_key(self, key_id: str) -> tuple[str, str] | None:
        """Mint a new raw_key for an existing row, in place.

        Returns ``(key_id, new_raw_key)`` on success, or ``None`` if
        the key_id doesn't exist. The old raw_key is invalidated
        immediately (because the row's key_hash changes).

        Use case: an operator wants to rotate without having to
        change the key_id (which is referenced in usage_events,
        billing, etc.). The new raw_key is returned ONCE; the
        caller must distribute it to the client.
        """
        import secrets
        new_raw_key = f"ah_{secrets.token_urlsafe(32)}"
        new_hash = self.hash_key(new_raw_key)
        with get_db_connection() as conn:
            row = conn.execute(
                "UPDATE api_keys SET key_hash = %s, last_rotated_at = CURRENT_TIMESTAMP "
                "WHERE id = %s RETURNING id",
                (new_hash, key_id),
            ).fetchone()
            conn.commit()
        if row is None:
            return None
        return key_id, new_raw_key

    def get_rotation_status(
        self,
        rotation_days: int = DEFAULT_API_KEY_ROTATION_DAYS,
    ) -> list[dict]:
        """Return every key whose last_rotated_at is older than
        ``rotation_days`` days, or that has never been rotated.

        The result is intended for an admin endpoint that ops can
        hit to see "which keys need rotating now?" — the action of
        rotating is still operator-driven (see rotate_key()).
        """
        with get_db_connection() as conn:
            rows = conn.execute(
                """
                SELECT id, name, tenant_id, is_admin, enabled,
                       created_at, last_rotated_at,
                       EXTRACT(DAY FROM (NOW() - COALESCE(last_rotated_at, created_at))) AS days_since_rotation
                FROM api_keys
                WHERE COALESCE(last_rotated_at, created_at) < NOW() - (%s * INTERVAL '1 day')
                ORDER BY days_since_rotation DESC
                """,
                (int(rotation_days),),
            ).fetchall()
        return [
            {
                "id": r["id"],
                "name": r["name"],
                "tenant_id": r["tenant_id"],
                "is_admin": bool(r["is_admin"]),
                "enabled": bool(r["enabled"]),
                "last_rotated_at": r["last_rotated_at"].isoformat() if r["last_rotated_at"] else None,
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                "days_since_rotation": int(r["days_since_rotation"]) if r["days_since_rotation"] is not None else None,
            }
            for r in rows
        ]
