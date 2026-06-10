"""Unit tests for per-tenant rate limit (P1.1, 2026-06-10).

OWASP API4:2023 — per-key limit alone lets one tenant mint 1000 keys
and bypass the cap. The fix is a second sliding-window limiter keyed
on tenant_id.
"""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.no_isolated_db]


# ──────────────────────────────────────────────────────────────────────
# Pure unit tests — InMemoryTenantRateLimiter
# ──────────────────────────────────────────────────────────────────────


def test_in_memory_tenant_limiter_allows_under_limit() -> None:
    from app.middleware.tenant_rate_limit import InMemoryTenantRateLimiter
    lim = InMemoryTenantRateLimiter(default_rpm=3, window_seconds=60)
    assert lim.allow("t1") is True
    assert lim.allow("t1") is True
    assert lim.allow("t1") is True


def test_in_memory_tenant_limiter_blocks_at_limit() -> None:
    from app.middleware.tenant_rate_limit import InMemoryTenantRateLimiter
    lim = InMemoryTenantRateLimiter(default_rpm=2, window_seconds=60)
    assert lim.allow("t1") is True
    assert lim.allow("t1") is True
    assert lim.allow("t1") is False  # 3rd request over limit


def test_in_memory_tenant_limiter_per_tenant_isolation() -> None:
    """One tenant hitting the cap must NOT block other tenants."""
    from app.middleware.tenant_rate_limit import InMemoryTenantRateLimiter
    lim = InMemoryTenantRateLimiter(default_rpm=1, window_seconds=60)
    # t1 exhausts
    assert lim.allow("t1") is True
    assert lim.allow("t1") is False
    # t2 still has full quota
    assert lim.allow("t2") is True
    assert lim.allow("t2") is False


def test_in_memory_tenant_limiter_window_expiry() -> None:
    """After the window passes, the tenant can make requests again."""
    from app.middleware.tenant_rate_limit import InMemoryTenantRateLimiter
    lim = InMemoryTenantRateLimiter(default_rpm=1, window_seconds=1)
    now = 1000.0
    assert lim.allow("t1", now=now) is True
    assert lim.allow("t1", now=now + 0.5) is False
    # Window has slid past the first request
    assert lim.allow("t1", now=now + 2.0) is True


def test_in_memory_tenant_limiter_per_call_override() -> None:
    """A specific rpm_limit override beats the default."""
    from app.middleware.tenant_rate_limit import InMemoryTenantRateLimiter
    lim = InMemoryTenantRateLimiter(default_rpm=10, window_seconds=60)
    # Allow 2, block the 3rd
    assert lim.allow("t1", rpm_limit=2) is True
    assert lim.allow("t1", rpm_limit=2) is True
    assert lim.allow("t1", rpm_limit=2) is False


# ──────────────────────────────────────────────────────────────────────
# Factory: make_tenant_rate_limiter degrades gracefully
# ──────────────────────────────────────────────────────────────────────


def test_factory_returns_in_memory_when_no_redis(monkeypatch) -> None:
    from app.middleware import tenant_rate_limit
    monkeypatch.delenv("REDIS_URL", raising=False)
    lim = tenant_rate_limit.make_tenant_rate_limiter(default_rpm=42)
    assert isinstance(lim, tenant_rate_limit.InMemoryTenantRateLimiter)
    assert lim._default == 42
