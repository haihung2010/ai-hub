"""Per-model_mode rate limit (P3.2, 2026-06-11).

Per the security roadmap §P3.2: lighter models (Lite) get a
looser cap because they're cheap; heavier models (External =
cloud) get a tighter cap because they cost $$$ per request.

Default caps (configurable via env):
- Lite     : 60 RPM
- Normal   : 30 RPM
- External : 20 RPM

This runs INSIDE the /v1/chat handler (not in middleware)
because ``model_mode`` is a request body field, not a header.
The check is a fast O(1) ZSET lookup with the same sliding
window pattern as the per-key and per-tenant limiters.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any

logger = logging.getLogger(__name__)


DEFAULT_LITE_RPM = 60
DEFAULT_NORMAL_RPM = 30
DEFAULT_EXTERNAL_RPM = 20


def _limits_from_env() -> dict[str, int]:
    return {
        "lite": int(os.environ.get("RATE_LIMIT_LITE_RPM", DEFAULT_LITE_RPM)),
        "normal": int(os.environ.get("RATE_LIMIT_NORMAL_RPM", DEFAULT_NORMAL_RPM)),
        "external": int(os.environ.get("RATE_LIMIT_EXTERNAL_RPM", DEFAULT_EXTERNAL_RPM)),
    }


class InMemoryModelModeRateLimiter:
    def __init__(self, limits: dict[str, int] | None = None) -> None:
        self._limits = limits or _limits_from_env()
        self._hits: dict[str, list[float]] = {}

    def limit_for(self, model_mode: str) -> int:
        return self._limits.get(model_mode, self._limits.get("normal", DEFAULT_NORMAL_RPM))

    def allow(self, tenant_id: str, model_mode: str, now: float | None = None) -> bool:
        limit = self.limit_for(model_mode)
        ts = time.time() if now is None else now
        cutoff = ts - 60.0
        key = f"{tenant_id}:{model_mode}"
        hits = [t for t in self._hits.get(key, []) if t > cutoff]
        if len(hits) >= limit:
            self._hits[key] = hits
            return False
        hits.append(ts)
        self._hits[key] = hits
        return True


class RedisModelModeRateLimiter:
    def __init__(self, redis_client: Any, limits: dict[str, int] | None = None) -> None:
        import uuid as _uuid
        self._r = redis_client
        self._limits = limits or _limits_from_env()

    def limit_for(self, model_mode: str) -> int:
        return self._limits.get(model_mode, self._limits.get("normal", DEFAULT_NORMAL_RPM))

    def allow(self, tenant_id: str, model_mode: str, now: float | None = None) -> bool:
        import uuid as _uuid
        limit = self.limit_for(model_mode)
        ts = time.time() if now is None else now
        key = f"mrl:{tenant_id}:{model_mode}"
        pipe = self._r.pipeline()
        pipe.zremrangebyscore(key, 0, ts - 60.0)
        pipe.zadd(key, {str(_uuid.uuid4()): ts})
        pipe.zcard(key)
        pipe.expire(key, 61)
        _, _, count, _ = pipe.execute()
        return count <= limit


def make_model_mode_rate_limiter() -> InMemoryModelModeRateLimiter | RedisModelModeRateLimiter:
    redis_url = os.environ.get("REDIS_URL", "")
    if not redis_url:
        return InMemoryModelModeRateLimiter()
    try:
        import redis as redis_lib
        client = redis_lib.from_url(redis_url, decode_responses=True)
        client.ping()
        return RedisModelModeRateLimiter(client)
    except Exception as exc:
        logger.warning("Redis unavailable for model_mode rate limit, falling back to in-memory: %s", exc)
        return InMemoryModelModeRateLimiter()
