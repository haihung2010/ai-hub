"""Test the Langfuse admin endpoints (cost, latency, traces, health).

When LANGFUSE_ENABLED=false (default), endpoints must return 503 or
informative status, NOT 500. When LANGFUSE_ENABLED=true, the
endpoints return 501 (not yet implemented) or 200 (health).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.middleware.security import AuthFailureTracker, InMemoryRateLimiter

# Pure unit tests — no DB access needed for these endpoint tests.
pytestmark = [pytest.mark.unit, pytest.mark.no_isolated_db]


def _build_test_client(monkeypatch: pytest.MonkeyPatch, langfuse_enabled: bool) -> TestClient:
    """Build a TestClient with isolated env state for LANGFUSE_ENABLED.

    ai-hub's X-API-KEY auth path grants ``is_admin=True`` for the master
    API key (set on Settings.api_key), so the master key is required to
    reach ``/v1/admin/*`` endpoints. The require_admin dependency raises
    403 for any non-admin key.

    The endpoint reads ``os.environ["LANGFUSE_ENABLED"]`` at request time,
    so we must keep the env var set for the lifetime of the TestClient
    (and clean it up via monkeypatch).
    """
    monkeypatch.setenv("LANGFUSE_ENABLED", "true" if langfuse_enabled else "false")
    settings = Settings(
        APP_PORT=8000,
        LOG_LEVEL="WARNING",
        LLAMA_CPP_BASE_URL="http://llama.test",
        LLAMA_CPP_OPENAI_URL="http://llama.test/v1",
        DEFAULT_MODEL="test-model:latest",
        LITE_MODEL="test-lite:latest",
        REQUEST_TIMEOUT_SECONDS=5.0,
        MAX_HISTORY_MESSAGES=5,
        LITE_MAX_HISTORY_MESSAGES=5,
        API_KEY="test-api-key-aaaaaaaaaa",
        RATE_LIMIT_PER_MINUTE=5,
        ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"],
        BACKGROUND_LLAMA_CPP_ENABLED="false",
        ENABLE_KNOWLEDGE_RAG=False,
        LANGFUSE_ENABLED="true" if langfuse_enabled else "false",
    )
    limiter = InMemoryRateLimiter(limit=settings.rate_limit_per_minute)
    tracker = AuthFailureTracker(
        limit=settings.auth_failure_limit,
        block_seconds=settings.auth_failure_block_seconds,
    )
    app = create_app(settings=settings, limiter=limiter, failure_tracker=tracker)
    tc = TestClient(app)
    tc.headers.update({"X-API-KEY": settings.api_key})
    return tc


def test_langfuse_health_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """When LANGFUSE_ENABLED=false, /health returns 200 with status=disabled."""
    client = _build_test_client(monkeypatch, langfuse_enabled=False)
    resp = client.get("/v1/admin/langfuse/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["enabled"] is False
    assert data["status"] == "disabled"


def test_langfuse_cost_when_disabled_returns_503(monkeypatch: pytest.MonkeyPatch) -> None:
    """When LANGFUSE_ENABLED=false, /cost returns 503 with helpful message."""
    client = _build_test_client(monkeypatch, langfuse_enabled=False)
    resp = client.get("/v1/admin/langfuse/cost?days=7")
    assert resp.status_code == 503
    assert "langfuse" in resp.text.lower()


def test_langfuse_latency_when_disabled_returns_503(monkeypatch: pytest.MonkeyPatch) -> None:
    """When LANGFUSE_ENABLED=false, /latency returns 503."""
    client = _build_test_client(monkeypatch, langfuse_enabled=False)
    resp = client.get("/v1/admin/langfuse/latency?days=7")
    assert resp.status_code == 503


def test_langfuse_traces_when_disabled_returns_503(monkeypatch: pytest.MonkeyPatch) -> None:
    """When LANGFUSE_ENABLED=false, /traces/{id} returns 503."""
    client = _build_test_client(monkeypatch, langfuse_enabled=False)
    resp = client.get("/v1/admin/langfuse/traces/test-trace-id")
    assert resp.status_code == 503


def test_langfuse_health_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """When LANGFUSE_ENABLED=true, /health returns 200 with status=ok (no actual ping)."""
    client = _build_test_client(monkeypatch, langfuse_enabled=True)
    resp = client.get("/v1/admin/langfuse/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["enabled"] is True
    # status is "ok" since we don't actually ping Langfuse (TODO Phase 2)
    assert data["status"] in ("ok", "degraded", "down")


def test_langfuse_router_prefix_is_correct() -> None:
    """Router prefix must be /v1/admin/langfuse."""
    from app.routes.admin_langfuse import router
    assert router.prefix == "/v1/admin/langfuse"
