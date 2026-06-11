"""Unit tests for per-model_mode rate limit (P3.2, 2026-06-11)."""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.no_isolated_db]


# ──────────────────────────────────────────────────────────────────────
# Pure unit tests — InMemoryModelModeRateLimiter
# ──────────────────────────────────────────────────────────────────────


def test_lite_60rpm_allows_60() -> None:
    from app.middleware.model_mode_rate_limit import InMemoryModelModeRateLimiter
    lim = InMemoryModelModeRateLimiter({"lite": 60, "normal": 30, "external": 20})
    for _ in range(60):
        assert lim.allow("t1", "lite") is True
    assert lim.allow("t1", "lite") is False


def test_external_20rpm_blocks_at_21() -> None:
    from app.middleware.model_mode_rate_limit import InMemoryModelModeRateLimiter
    lim = InMemoryModelModeRateLimiter({"lite": 60, "normal": 30, "external": 20})
    for _ in range(20):
        assert lim.allow("t1", "external") is True
    assert lim.allow("t1", "external") is False


def test_per_tenant_per_mode_isolation() -> None:
    """Each (tenant, mode) pair has its own bucket."""
    from app.middleware.model_mode_rate_limit import InMemoryModelModeRateLimiter
    lim = InMemoryModelModeRateLimiter({"lite": 2, "normal": 2, "external": 2})
    # tenant t1, mode lite — fill
    assert lim.allow("t1", "lite") is True
    assert lim.allow("t1", "lite") is True
    assert lim.allow("t1", "lite") is False
    # t1, normal — fresh
    assert lim.allow("t1", "normal") is True
    # t2, lite — fresh
    assert lim.allow("t2", "lite") is True


def test_unknown_mode_falls_back_to_normal() -> None:
    """An unknown model_mode uses the 'normal' limit (defensive)."""
    from app.middleware.model_mode_rate_limit import InMemoryModelModeRateLimiter
    lim = InMemoryModelModeRateLimiter({"lite": 60, "normal": 3, "external": 20})
    for _ in range(3):
        assert lim.allow("t1", "frobnicate") is True
    assert lim.allow("t1", "frobnicate") is False


def test_limit_for_helper() -> None:
    from app.middleware.model_mode_rate_limit import InMemoryModelModeRateLimiter
    lim = InMemoryModelModeRateLimiter({"lite": 60, "normal": 30, "external": 20})
    assert lim.limit_for("lite") == 60
    assert lim.limit_for("normal") == 30
    assert lim.limit_for("external") == 20


def test_factory_returns_in_memory_when_no_redis(monkeypatch) -> None:
    from app.middleware import model_mode_rate_limit as mod
    monkeypatch.delenv("REDIS_URL", raising=False)
    lim = mod.make_model_mode_rate_limiter()
    assert isinstance(lim, mod.InMemoryModelModeRateLimiter)


# ──────────────────────────────────────────────────────────────────────
# Integration: /v1/chat rejects when over the per-mode cap
# ──────────────────────────────────────────────────────────────────────


def test_chat_route_rejects_when_model_mode_cap_exceeded(client) -> None:
    """End-to-end: 3 'external' requests with cap=2 → 3rd gets 429."""
    from app.middleware.model_mode_rate_limit import InMemoryModelModeRateLimiter
    # Force a tiny cap so the test runs fast
    client.app.state.model_mode_rate_limiter = InMemoryModelModeRateLimiter(
        {"lite": 60, "normal": 30, "external": 2}
    )

    body = {
        "project_id": "rltest",
        "user_message": "hello",
        "model_mode": "external",
    }

    # First 2 should NOT be 429
    for i in range(2):
        resp = client.post("/v1/chat", json=body)
        assert resp.status_code != 429, f"unexpected 429 on call {i+1}: {resp.text}"

    # 3rd should be 429
    resp = client.post("/v1/chat", json=body)
    assert resp.status_code == 429
    assert "external" in resp.json()["detail"]
    assert resp.headers.get("Retry-After") == "60"
