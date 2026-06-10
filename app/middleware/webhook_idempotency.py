"""Webhook idempotency (P1.6, 2026-06-10).

Chatwoot, Facebook, and any other webhook source can retry the same
delivery multiple times. Without dedup, AI Hub would re-process the
message and post the AI reply back to Chatwoot multiple times, which
the customer sees as a duplicated reply.

Reference: GitHub webhooks guide + Stripe webhooks guide.

Approach: SETNX on a per-(source, delivery_id) key with a 24h TTL.
First call: returns False (NOT a duplicate — process the webhook).
Second call within 24h: returns True (duplicate — short-circuit).
After 24h: TTL expires, the delivery is forgotten.

Falls back to a per-process in-memory dict when Redis is unavailable
(matches the rate-limiter's degradation pattern).
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_TTL_SECONDS = 24 * 60 * 60  # 24h


class InMemoryWebhookIdempotency:
    """Process-local fallback when Redis is down. Loses dedup on restart
    but is better than nothing. Used by tests too."""

    def __init__(self, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> None:
        self._ttl = ttl_seconds
        self._seen: dict[str, float] = {}

    def is_duplicate(self, source: str, delivery_id: str) -> bool:
        key = f"{source}:{delivery_id}"
        now = time.time()
        # Prune expired entries to keep the dict bounded
        if self._seen and len(self._seen) > 10_000:
            cutoff = now - self._ttl
            self._seen = {k: v for k, v in self._seen.items() if v > cutoff}
        if key in self._seen:
            return True
        self._seen[key] = now
        return False

    def reset(self) -> None:
        self._seen.clear()


class RedisWebhookIdempotency:
    """SETNX-based dedup with 24h TTL. Survives restarts.

    Naming convention: ``webhook:idem:<source>:<delivery_id>``.
    """

    def __init__(self, redis_client: Any, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> None:
        self._r = redis_client
        self._ttl = ttl_seconds

    def is_duplicate(self, source: str, delivery_id: str) -> bool:
        key = f"webhook:idem:{source}:{delivery_id}"
        # setnx returns True if the key was new (i.e. NOT a duplicate)
        # — we return the opposite, since the caller wants to know
        # "should I short-circuit?"
        was_new = self._r.setnx(key, "1")
        if was_new:
            self._r.expire(key, self._ttl)
            return False
        return True


def make_idempotency() -> InMemoryWebhookIdempotency | RedisWebhookIdempotency:
    """Pick Redis if REDIS_URL is set and reachable, else in-memory.

    The check is best-effort: a runtime Redis outage degrades to
    in-memory, not to a hard failure. That matches the rest of the
    security stack.
    """
    redis_url = os.environ.get("REDIS_URL", "")
    if not redis_url:
        return InMemoryWebhookIdempotency()
    try:
        import redis as redis_lib
        client = redis_lib.from_url(redis_url, decode_responses=True)
        client.ping()
        return RedisWebhookIdempotency(client)
    except Exception as exc:
        logger.warning("Redis unavailable for webhook idempotency, falling back to in-memory: %s", exc)
        return InMemoryWebhookIdempotency()
