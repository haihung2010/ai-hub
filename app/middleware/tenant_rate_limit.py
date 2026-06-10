"""Per-tenant rate limit (P1.1, 2026-06-10).

OWASP API4:2023 — Unrestricted Resource Consumption. Per-key
limiting alone is not enough: one tenant could mint 1000 API keys
and blow through the 60-RPM-per-key limit (1000 * 60 = 60 000 RPM
total) and starve the other tenants on the same shared GPU.

The fix: a second sliding-window limiter keyed on the tenant_id
(default 200 RPM, configurable per-tenant via Settings).
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any

from app.middleware.security import RATE_LIMIT_WINDOW_SECONDS

logger = logging.getLogger(__name__)

DEFAULT_TENANT_RPM = 200


class InMemoryTenantRateLimiter:
    """Process-local fallback. Loses state on restart; OK for tests."""

    def __init__(self, default_rpm: int = DEFAULT_TENANT_RPM, window_seconds: int = RATE_LIMIT_WINDOW_SECONDS) -> None:
        self._default = default_rpm
        self._window = window_seconds
        self._hits: dict[str, list[float]] = {}

    def allow(self, tenant_id: str, rpm_limit: int | None = None, now: float | None = None) -> bool:
        limit = rpm_limit or self._default
        ts = time.time() if now is None else now
        cutoff = ts - self._window
        hits = [t for t in self._hits.get(tenant_id, []) if t > cutoff]
        if len(hits) >= limit:
            self._hits[tenant_id] = hits
            return False
        hits.append(ts)
        self._hits[tenant_id] = hits
        return True


class RedisTenantRateLimiter:
    """Sliding-window ZSET-based limiter, per-tenant."""

    def __init__(self, redis_client: Any, default_rpm: int = DEFAULT_TENANT_RPM, window_seconds: int = RATE_LIMIT_WINDOW_SECONDS) -> None:
        self._r = redis_client
        self._default = default_rpm
        self._window = window_seconds

    def allow(self, tenant_id: str, rpm_limit: int | None = None, now: float | None = None) -> bool:
        import uuid as _uuid
        limit = rpm_limit or self._default
        ts = time.time() if now is None else now
        key = f"trl:tenant:{tenant_id}"
        pipe = self._r.pipeline()
        pipe.zremrangebyscore(key, 0, ts - self._window)
        pipe.zadd(key, {str(_uuid.uuid4()): ts})
        pipe.zcard(key)
        pipe.expire(key, self._window + 1)
        _, _, count, _ = pipe.execute()
        return count <= limit


def make_tenant_rate_limiter(default_rpm: int = DEFAULT_TENANT_RPM) -> InMemoryTenantRateLimiter | RedisTenantRateLimiter:
    """Factory: pick Redis if REDIS_URL is reachable, else in-memory."""
    redis_url = os.environ.get("REDIS_URL", "")
    if not redis_url:
        return InMemoryTenantRateLimiter(default_rpm=default_rpm)
    try:
        import redis as redis_lib
        client = redis_lib.from_url(redis_url, decode_responses=True)
        client.ping()
        return RedisTenantRateLimiter(client, default_rpm=default_rpm)
    except Exception as exc:
        logger.warning("Redis unavailable for tenant rate limit, falling back to in-memory: %s", exc)
        return InMemoryTenantRateLimiter(default_rpm=default_rpm)
