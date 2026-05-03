"""Virtual API key lookup and small-team policy metadata."""

from __future__ import annotations

from dataclasses import dataclass
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


class ApiKeyService:
    @staticmethod
    def hash_key(raw_key: str) -> str:
        return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()

    def lookup(self, raw_key: str) -> ApiKeyRecord | None:
        key_hash = self.hash_key(raw_key)
        with get_db_connection() as conn:
            row = conn.execute(
                "SELECT id, tenant_id, name, allow_external, rpm_limit, max_parallel_requests "
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
        )
