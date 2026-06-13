"""Redis-backed chat response cache with in-memory fallback.

Used by ai_service to skip LLM call when same (user_id, message) seen before.
For clothing e-commerce Q&A, same questions from different users can share
cache safely (product info is public).

Auto-fallback to in-memory if Redis is down. Tracks hits/misses for metrics.
"""
from __future__ import annotations

import hashlib
import logging
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)


class CacheService:
    """Thread-safe chat response cache.

    Key: sha256(f"{user_id}:{message}")
    Value: serialized response string
    TTL: cache_ttl_seconds (default 3600s = 1 hour)
    Size limit: cache_max_size_mb (default 100 MB) for in-memory fallback
    """

    def __init__(
        self,
        redis_url: str = "",
        ttl_seconds: int = 3600,
        max_size_mb: int = 100,
    ):
        self.ttl = ttl_seconds
        self._max_size_bytes = max_size_mb * 1024 * 1024
        self._memory_cache: dict[str, tuple[float, str]] = {}
        self._lock = threading.RLock()
        self.hits = 0
        self.misses = 0
        self._redis = None
        self._redis_failed = False
        if redis_url:
            self._init_redis(redis_url)

    def _init_redis(self, redis_url: str) -> None:
        try:
            import redis
            self._redis = redis.from_url(redis_url, decode_responses=True, socket_timeout=2)
            self._redis.ping()
            logger.info("CacheService: Redis connected at %s", redis_url)
        except Exception as e:
            logger.warning("CacheService: Redis unavailable (%r), using in-memory only", e)
            self._redis = None
            self._redis_failed = True

    @staticmethod
    def hash_key(user_id: str, message: str) -> str:
        """Hash user_id + message into a stable cache key."""
        return hashlib.sha256(f"{user_id}:{message}".encode("utf-8")).hexdigest()

    def get(self, key: str) -> Optional[str]:
        """Return cached value or None. Auto-fallback to memory if Redis fails."""
        if self._redis is not None:
            try:
                val = self._redis.get(key)
                if val is not None:
                    self.hits += 1
                    return val
                self.misses += 1
                return None
            except Exception as e:
                logger.warning("CacheService: Redis get failed (%r), falling back to memory", e)
                self._redis = None
        return self._get_memory(key)

    def _get_memory(self, key: str) -> Optional[str]:
        with self._lock:
            entry = self._memory_cache.get(key)
            if entry is None:
                self.misses += 1
                return None
            expires_at, value = entry
            if expires_at < time.time():
                del self._memory_cache[key]
                self.misses += 1
                return None
            self.hits += 1
            return value

    def set(self, key: str, value: str) -> None:
        """Store value with TTL. Auto-fallback if Redis fails."""
        if self._redis is not None:
            try:
                self._redis.setex(key, self.ttl, value)
                return
            except Exception as e:
                logger.warning("CacheService: Redis set failed (%r), falling back to memory", e)
                self._redis = None
        self._set_memory(key, value)

    def _set_memory(self, key: str, value: str) -> None:
        with self._lock:
            self._evict_if_needed(len(value))
            self._memory_cache[key] = (time.time() + self.ttl, value)

    def _evict_if_needed(self, new_entry_size: int) -> None:
        """Evict oldest 20% if over size limit."""
        with self._lock:
            total = sum(len(v) for _, v in self._memory_cache.values()) + new_entry_size
            if total <= self._max_size_bytes:
                return
            sorted_entries = sorted(self._memory_cache.items(), key=lambda kv: kv[1][0])
            evict_count = max(1, len(sorted_entries) // 5)
            for k, _ in sorted_entries[:evict_count]:
                del self._memory_cache[k]

    def metrics(self) -> dict:
        """Return hit/miss/size metrics for observability."""
        total = self.hits + self.misses
        with self._lock:
            size_bytes = sum(len(v) for _, v in self._memory_cache.values())
        return {
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate_pct": (self.hits / total * 100) if total else 0.0,
            "size_mb": size_bytes / 1024 / 1024,
            "size_entries": len(self._memory_cache),
            "redis_connected": self._redis is not None,
        }

    def clear(self) -> None:
        """Clear all cached values (testing only)."""
        with self._lock:
            self._memory_cache.clear()
        if self._redis is not None:
            try:
                self._redis.flushdb()
            except Exception:
                pass
