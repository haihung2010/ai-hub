"""Virtual API key lookup and small-team policy metadata."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import hashlib

from app.core.database import get_db_connection


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


class ApiKeyService:
    @staticmethod
    def hash_key(raw_key: str) -> str:
        return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()

    def lookup(self, raw_key: str) -> ApiKeyRecord | None:
        key_hash = self.hash_key(raw_key)
        with get_db_connection() as conn:
            row = conn.execute(
                "SELECT id, tenant_id, name, allow_external, rpm_limit, max_parallel_requests, monthly_budget_usd, is_admin "
                "FROM api_keys WHERE key_hash = %s AND enabled = 1 "
                "AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP)",
                (key_hash,),
            ).fetchone()
        if row is None:
            return None
        return ApiKeyRecord(
            id=row["id"],
            tenant_id=row["tenant_id"],
            name=row["name"],
            allow_external=bool(row["allow_external"]),
            rpm_limit=int(row["rpm_limit"]),
            max_parallel_requests=int(row["max_parallel_requests"]),
            is_admin=bool(row["is_admin"]),
            monthly_budget_usd=float(row["monthly_budget_usd"]) if row["monthly_budget_usd"] is not None else None,
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
                """INSERT INTO api_keys (id, key_hash, name, tenant_id, is_admin, rpm_limit, monthly_budget_usd)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                (key_id, key_hash, name, tenant_id, int(is_admin), rpm_limit, monthly_budget_usd),
            )
            conn.commit()
        return key_id, raw_key
