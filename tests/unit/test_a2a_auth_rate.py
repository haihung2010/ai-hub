"""Unit tests for A2A auth + rate limit (P0.5, 2026-06-10).

A2A endpoints (POST /v1/a2a/jsonrpc, GET /v1/a2a/agent-card) live behind
the global X-API-KEY middleware and the Redis/InMemory rate limiter.
These tests pin both behaviors down so that future refactors can't
accidentally make A2A unauthenticated or unlimited.

The default test fixture sets RATE_LIMIT_PER_MINUTE=5 (see conftest),
so a 6th request from the same key should 429.
"""
from __future__ import annotations

import json

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.no_isolated_db]


# ──────────────────────────────────────────────────────────────────────
# Auth: X-API-KEY is mandatory
# ──────────────────────────────────────────────────────────────────────


def test_agent_card_requires_api_key(client) -> None:
    """GET /v1/a2a/agent-card without X-API-KEY must be rejected."""
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app.core.config import Settings
    from app.middleware.security import InMemoryRateLimiter, AuthFailureTracker
    from app.core.database import get_db_connection, init_db

    init_db()
    settings = Settings(
        APP_PORT=8000,
        LOG_LEVEL="WARNING",
        LLAMA_CPP_BASE_URL="http://llama.test",
        DEFAULT_MODEL="test-model",
        LITE_MODEL="test-lite",
        REQUEST_TIMEOUT_SECONDS=5.0,
        MAX_HISTORY_MESSAGES=5,
        LITE_MAX_HISTORY_MESSAGES=5,
        API_KEY="real-api-key-zzzzzzzzzzzz",
        RATE_LIMIT_PER_MINUTE=5,
        ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"],
        BACKGROUND_LLAMA_CPP_ENABLED="false",
        ENABLE_KNOWLEDGE_RAG=False,
    )
    app = create_app(settings=settings, limiter=InMemoryRateLimiter(5), failure_tracker=AuthFailureTracker(5, 60))
    bare = TestClient(app)
    resp = bare.get("/v1/a2a/agent-card")
    assert resp.status_code in (401, 403), f"unauthenticated should be rejected, got {resp.status_code}"


def test_jsonrpc_requires_api_key(client) -> None:
    """POST /v1/a2a/jsonrpc without X-API-KEY must be rejected."""
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app.core.config import Settings
    from app.middleware.security import InMemoryRateLimiter, AuthFailureTracker
    from app.core.database import init_db

    init_db()
    settings = Settings(
        APP_PORT=8000, LOG_LEVEL="WARNING",
        LLAMA_CPP_BASE_URL="http://llama.test",
        DEFAULT_MODEL="test-model", LITE_MODEL="test-lite",
        REQUEST_TIMEOUT_SECONDS=5.0, MAX_HISTORY_MESSAGES=5,
        LITE_MAX_HISTORY_MESSAGES=5,
        API_KEY="real-api-key-zzzzzzzzzzzz", RATE_LIMIT_PER_MINUTE=5,
        ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"],
        BACKGROUND_LLAMA_CPP_ENABLED="false",
        ENABLE_KNOWLEDGE_RAG=False,
    )
    app = create_app(settings=settings, limiter=InMemoryRateLimiter(5), failure_tracker=AuthFailureTracker(5, 60))
    bare = TestClient(app)
    resp = bare.post(
        "/v1/a2a/jsonrpc",
        json={"jsonrpc": "2.0", "id": "1", "method": "ListTasks", "params": {}},
    )
    assert resp.status_code in (401, 403), f"unauthenticated should be rejected, got {resp.status_code}"


# ──────────────────────────────────────────────────────────────────────
# Auth: wrong key is rejected
# ──────────────────────────────────────────────────────────────────────


def test_agent_card_rejects_wrong_api_key(client) -> None:
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app.core.config import Settings
    from app.middleware.security import InMemoryRateLimiter, AuthFailureTracker
    from app.core.database import init_db

    init_db()
    settings = Settings(
        APP_PORT=8000, LOG_LEVEL="WARNING",
        LLAMA_CPP_BASE_URL="http://llama.test",
        DEFAULT_MODEL="test-model", LITE_MODEL="test-lite",
        REQUEST_TIMEOUT_SECONDS=5.0, MAX_HISTORY_MESSAGES=5,
        LITE_MAX_HISTORY_MESSAGES=5,
        API_KEY="real-api-key-zzzzzzzzzzzz", RATE_LIMIT_PER_MINUTE=5,
        ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"],
        BACKGROUND_LLAMA_CPP_ENABLED="false",
        ENABLE_KNOWLEDGE_RAG=False,
    )
    app = create_app(settings=settings, limiter=InMemoryRateLimiter(5), failure_tracker=AuthFailureTracker(5, 60))
    bad = TestClient(app)
    bad.headers.update({"X-API-KEY": "WRONG-KEY"})
    resp = bad.get("/v1/a2a/agent-card")
    assert resp.status_code in (401, 403)


# ──────────────────────────────────────────────────────────────────────
# Auth: valid key works
# ──────────────────────────────────────────────────────────────────────


def test_agent_card_with_valid_key(client) -> None:
    resp = client.get("/v1/a2a/agent-card")
    assert resp.status_code == 200
    data = resp.json()
    # A2A AgentCard shape
    assert "name" in data
    assert "skills" in data
    assert "url" in data


def test_jsonrpc_list_tasks_with_valid_key(client) -> None:
    """ListTasks is a no-op-friendly smoke test for the auth path."""
    resp = client.post(
        "/v1/a2a/jsonrpc",
        json={"jsonrpc": "2.0", "id": "1", "method": "ListTasks", "params": {}},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["jsonrpc"] == "2.0"
    assert body["id"] == "1"
    assert "result" in body
    assert "tasks" in body["result"]


# ──────────────────────────────────────────────────────────────────────
# Rate limit: 60 RPM documented; in tests the fixture sets it to 5
# ──────────────────────────────────────────────────────────────────────


def test_jsonrpc_rate_limit_kicks_in_after_quota(client) -> None:
    """After RATE_LIMIT_PER_MINUTE requests from the same key, return 429.

    The conftest sets RATE_LIMIT_PER_MINUTE=5 in the test settings. So
    the 6th call should be rejected. (Refreshes on Redis fallback OK.)
    """
    # First N calls should pass
    for i in range(5):
        resp = client.post(
            "/v1/a2a/jsonrpc",
            json={"jsonrpc": "2.0", "id": str(i), "method": "ListTasks", "params": {}},
        )
        assert resp.status_code == 200, f"call {i+1}: {resp.text}"
    # 6th call should be rate-limited
    resp = client.post(
        "/v1/a2a/jsonrpc",
        json={"jsonrpc": "2.0", "id": "overflow", "method": "ListTasks", "params": {}},
    )
    assert resp.status_code == 429, resp.text


# ──────────────────────────────────────────────────────────────────────
# P1.4 — A2A error data redaction (2026-06-10)
# ──────────────────────────────────────────────────────────────────────


def test_a2a_internal_error_is_redacted(client, monkeypatch) -> None:
    """When a handler raises, the public response must NOT echo the
    exception text. It must include an err_id for support correlation.
    """
    # Force _handle_list_tasks (or any handler) to raise
    import app.routes.a2a as a2a_routes
    def _boom(rpc):  # noqa: ARG001
        raise RuntimeError("SECRET-PATH-/home/admin/secrets.txt")
    monkeypatch.setattr(a2a_routes, "_handle_list_tasks", _boom)
    resp = client.post(
        "/v1/a2a/jsonrpc",
        json={"jsonrpc": "2.0", "id": "x", "method": "ListTasks", "params": {}},
    )
    assert resp.status_code == 200  # JSON-RPC errors are 200 with error envelope
    body = resp.json()
    assert body.get("error") is not None
    msg = body["error"]["message"]
    # Public message must NOT contain the raw exception
    assert "SECRET-PATH" not in msg
    assert "/home/admin" not in msg
    # Must include an err_id for support correlation
    assert "err_id=" in msg
    # The err_id is 8 hex chars after err_id=
    import re
    m = re.search(r"err_id=([0-9a-f]{8})", msg)
    assert m, f"err_id missing from message: {msg!r}"
