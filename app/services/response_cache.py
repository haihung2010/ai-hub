"""Response cache for /v1/chat (and Chatwoot /respond).

Caches AI responses in Redis keyed on (tenant_id + project_id + model_mode +
user_message). Skips caching when the request is conversation-specific
(has session_id or non-empty history).

Goal: popular queries like 'Giá sản phẩm A?' get answered from cache instead
of hitting llama.cpp → cuts p95 latency and GPU load.

Cache key: ``qcache:v1:<sha256_hex>``
Cache value: JSON ``{response, model, usage, cached_at}``
TTL: configurable via ``CHAT_CACHE_TTL_SECONDS`` (default 3600 = 1 hour)

Stats (in-process counters, not persisted):
- hits: cache hit count
- misses: cache miss count
- stores: write count
- errors: redis-side errors
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

CACHE_KEY_PREFIX = "qcache:v1"
DEFAULT_TTL_SECONDS = 3600
MAX_QUERY_LENGTH = 2000  # Don't cache extremely long queries


@dataclass
class CacheStats:
    hits: int = 0
    misses: int = 0
    stores: int = 0
    errors: int = 0
    skipped_session: int = 0
    skipped_history: int = 0


_STATS = CacheStats()


def get_stats() -> dict[str, int]:
    """Return current cache stats as a dict. Reset by process restart."""
    return {
        "hits": _STATS.hits,
        "misses": _STATS.misses,
        "stores": _STATS.stores,
        "errors": _STATS.errors,
        "skipped_session": _STATS.skipped_session,
        "skipped_history": _STATS.skipped_history,
        "hit_rate": round(_STATS.hits / max(_STATS.hits + _STATS.misses, 1), 3),
    }


def _is_cacheable(
    *,
    session_id: str | None,
    has_history: bool,
    model_mode: str,
) -> bool:
    """Decide whether a request is cacheable.

    Skip when:
    - session_id is set (conversation-specific, history matters)
    - history is non-empty (same as above — context matters)
    - model_mode is "external" or "thinking" (varies per request, e.g.
      MiniMax cloud routing)
    """
    if session_id:
        _STATS.skipped_session += 1
        return False
    if has_history:
        _STATS.skipped_history += 1
        return False
    if model_mode in ("external", "thinking"):
        return False
    return True


def _make_key(tenant_id: str, project_id: str, model_mode: str, user_message: str) -> str:
    """SHA256 of normalized query tuple. Truncates user_message to MAX_QUERY_LENGTH
    so very long inputs don't blow up Redis keys (Redis key limit is 512MB).
    """
    norm = f"{tenant_id}|{project_id}|{model_mode}|{user_message[:MAX_QUERY_LENGTH]}"
    h = hashlib.sha256(norm.encode("utf-8")).hexdigest()
    return f"{CACHE_KEY_PREFIX}:{h}"


def _get_redis():
    """Lazy import + connection. Returns None if Redis is down (graceful)."""
    try:
        import redis as redis_lib
        from app.core.config import get_settings
        return redis_lib.from_url(get_settings().redis_url, decode_responses=True)
    except Exception as exc:
        logger.warning("Redis init failed (cache will be skipped): %r", exc)
        return None


def _get_ttl() -> int:
    return int(os.environ.get("CHAT_CACHE_TTL_SECONDS", str(DEFAULT_TTL_SECONDS)))


def lookup(
    *,
    tenant_id: str,
    project_id: str,
    model_mode: str,
    user_message: str,
    session_id: str | None,
    has_history: bool,
) -> dict[str, Any] | None:
    """Try to fetch a cached response. Returns the cached dict or None.

    Updates in-process stats.
    """
    if not _is_cacheable(
        session_id=session_id,
        has_history=has_history,
        model_mode=model_mode,
    ):
        return None
    r = _get_redis()
    if r is None:
        return None
    key = _make_key(tenant_id, project_id, model_mode, user_message)
    try:
        raw = r.get(key)
    except Exception as exc:
        _STATS.errors += 1
        logger.warning("Cache lookup failed for key=%s: %r", key[:32], exc)
        return None
    if not raw:
        _STATS.misses += 1
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        _STATS.errors += 1
        return None
    data["_cache"] = "hit"  # marker so the caller knows
    data["_cached_at"] = data.get("_cached_at", "unknown")
    _STATS.hits += 1
    return data


def store(
    *,
    tenant_id: str,
    project_id: str,
    model_mode: str,
    user_message: str,
    session_id: str | None,
    has_history: bool,
    response: dict[str, Any],
) -> bool:
    """Store a response in cache. Returns True on success.

    Skips non-cacheable requests (session, history, external mode).
    """
    if not _is_cacheable(
        session_id=session_id,
        has_history=has_history,
        model_mode=model_mode,
    ):
        return False
    r = _get_redis()
    if r is None:
        return False
    key = _make_key(tenant_id, project_id, model_mode, user_message)
    payload = dict(response)
    payload["_cached_at"] = int(time.time())
    try:
        r.setex(key, _get_ttl(), json.dumps(payload, ensure_ascii=False))
        _STATS.stores += 1
        return True
    except Exception as exc:
        _STATS.errors += 1
        logger.warning("Cache store failed for key=%s: %r", key[:32], exc)
        return False
