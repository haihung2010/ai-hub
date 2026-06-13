"""Unit tests for CacheService."""
from __future__ import annotations

import time

import pytest

from app.services.cache_service import CacheService

pytestmark = pytest.mark.no_isolated_db  # CacheService does not touch the DB


def test_hash_key_deterministic():
    k1 = CacheService.hash_key("user1", "hello")
    k2 = CacheService.hash_key("user1", "hello")
    assert k1 == k2
    assert len(k1) == 64  # sha256 hex


def test_hash_key_differs_by_user():
    k1 = CacheService.hash_key("user1", "hello")
    k2 = CacheService.hash_key("user2", "hello")
    assert k1 != k2


def test_hash_key_differs_by_message():
    k1 = CacheService.hash_key("user1", "hello")
    k2 = CacheService.hash_key("user1", "goodbye")
    assert k1 != k2


def test_get_returns_none_on_miss():
    c = CacheService(ttl_seconds=60)
    assert c.get("nonexistent_key") is None
    assert c.misses == 1
    assert c.hits == 0


def test_set_then_get_returns_value():
    c = CacheService(ttl_seconds=60)
    k = CacheService.hash_key("u1", "msg")
    c.set(k, "response text")
    assert c.get(k) == "response text"
    assert c.hits == 1
    assert c.misses == 0


def test_ttl_expiration():
    c = CacheService(ttl_seconds=1)
    k = CacheService.hash_key("u1", "msg")
    c.set(k, "response")
    assert c.get(k) == "response"
    time.sleep(1.1)
    assert c.get(k) is None
    assert c.misses == 1  # post-expiration get is the only miss


def test_metrics_basic():
    c = CacheService(ttl_seconds=60)
    k = CacheService.hash_key("u1", "msg")
    c.set(k, "v")
    c.get(k)  # hit
    c.get("missing")  # miss
    m = c.metrics()
    assert m["hits"] == 1
    assert m["misses"] == 1
    assert m["hit_rate_pct"] == 50.0
    assert m["size_entries"] == 1
    assert m["redis_connected"] is False


def test_metrics_empty_cache():
    c = CacheService(ttl_seconds=60)
    m = c.metrics()
    assert m["hits"] == 0
    assert m["misses"] == 0
    assert m["hit_rate_pct"] == 0.0
    assert m["size_mb"] == 0.0


def test_lru_eviction():
    """When over size limit, evict oldest 20%."""
    c = CacheService(ttl_seconds=60, max_size_mb=1)  # 1 MB
    # Fill cache with ~1 MB of entries (each 100KB)
    big_value = "x" * (100 * 1024)  # 100KB
    for i in range(12):
        c.set(f"key_{i}", big_value)
    # 12 * 100KB = 1.2 MB > 1 MB limit
    # Should have evicted at least 1 (oldest)
    assert c.metrics()["size_entries"] < 12


def test_clear():
    c = CacheService(ttl_seconds=60)
    c.set("k1", "v1")
    c.set("k2", "v2")
    c.clear()
    assert c.get("k1") is None
    assert c.metrics()["size_entries"] == 0


def test_concurrent_access():
    """Thread-safety smoke test."""
    import threading
    c = CacheService(ttl_seconds=60)

    def worker(i):
        k = f"key_{i}"
        c.set(k, f"value_{i}")
        c.get(k)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(50)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    m = c.metrics()
    assert m["hits"] + m["misses"] >= 50  # at least 50 misses (no hits because 50 different keys)
