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
import uuid

import pytest


def _uniq(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


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


# ──────────────────────────────────────────────────────────────────────
# P1.2 — A2A audit log (2026-06-10)
# ──────────────────────────────────────────────────────────────────────


def test_a2a_jsonrpc_writes_audit_row(client) -> None:
    """Every successful JSON-RPC call must land a row in a2a_audit_log."""
    from app.core.database import get_db_connection

    # Baseline: count rows before. We assert EXACTLY +1 row was added
    # (robust against pytest-repeat which re-runs the test in the
    # same DB session).
    def _count_for(req_id: str) -> int:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT count(*) AS n FROM a2a_audit_log WHERE request_id = %s",
                    (req_id,),
                )
                row = cur.fetchone()
            conn.commit()
        return int(row["n"])

    before = _count_for("audit-1")
    resp = client.post(
        "/v1/a2a/jsonrpc",
        json={"jsonrpc": "2.0", "id": "audit-1", "method": "ListTasks", "params": {}},
    )
    assert resp.status_code == 200
    after = _count_for("audit-1")
    assert after - before == 1, f"expected +1 audit row, got delta={after - before}"
    # Verify the schema
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT rpc_method, request_id, status_code, latency_ms, err_id "
                "FROM a2a_audit_log WHERE request_id = %s LIMIT 1",
                ("audit-1",),
            )
            row = cur.fetchone()
        conn.commit()
    assert row["rpc_method"] == "ListTasks"
    assert row["request_id"] == "audit-1"
    assert row["status_code"] == 200
    assert isinstance(row["latency_ms"], int) and row["latency_ms"] >= 0
    assert row["err_id"] is None  # success path


def test_a2a_jsonrpc_audit_row_records_err_id_on_exception(client, monkeypatch) -> None:
    """When a handler raises (P1.4), the audit row records the err_id."""
    from app.core.database import get_db_connection

    import app.routes.a2a as a2a_routes
    def _boom(rpc):  # noqa: ARG001
        raise RuntimeError("internal blew up")
    monkeypatch.setattr(a2a_routes, "_handle_list_tasks", _boom)

    def _err_for(req_id: str) -> str | None:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT err_id FROM a2a_audit_log WHERE request_id = %s LIMIT 1",
                    (req_id,),
                )
                row = cur.fetchone()
            conn.commit()
        return row["err_id"] if row else None

    resp = client.post(
        "/v1/a2a/jsonrpc",
        json={"jsonrpc": "2.0", "id": "audit-err", "method": "ListTasks", "params": {}},
    )
    assert resp.status_code == 200
    err_id = _err_for("audit-err")
    assert err_id is not None
    assert len(err_id) == 8


# ──────────────────────────────────────────────────────────────────────
# P2.1 follow-up — OAuth bearer token in SecurityMiddleware (2026-06-11)
# ──────────────────────────────────────────────────────────────────────


def test_bearer_token_authenticates(client) -> None:
    """A valid bearer token is accepted by the security middleware
    and the claims are surfaced on request.state."""
    from app.services.api_key_service import ApiKeyService
    from app.services.oauth_service import issue_token
    svc = ApiKeyService()
    kid, _ = svc.create_key(name=_uniq("oauth-mware"), tenant_id=_uniq("oauth-mware-t"))
    tok = issue_token(api_key_id=kid, tenant_id="oauth-mware-t-abc", scopes=["chat", "a2a"])
    resp = client.get(
        "/v1/a2a/agent-card",
        headers={"Authorization": f"Bearer {tok.access_token}"},
    )
    assert resp.status_code == 200


def test_bearer_token_rejected_when_invalid(client) -> None:
    """A bad bearer token returns 401 (NOT 500)."""
    resp = client.get(
        "/v1/a2a/agent-card",
        headers={"Authorization": "Bearer obviously-not-a-jwt"},
    )
    assert resp.status_code == 401
    assert "bearer" in resp.json()["detail"].lower()


def test_bearer_token_takes_precedence_over_x_api_key(client) -> None:
    """When both headers are present, the bearer token wins."""
    from app.services.api_key_service import ApiKeyService
    from app.services.oauth_service import issue_token
    svc = ApiKeyService()
    kid, _ = svc.create_key(name=_uniq("oauth-precedence"), tenant_id=_uniq("oauth-prec-t"))
    tok = issue_token(api_key_id=kid, tenant_id="oauth-prec-t-abc", scopes=["chat"])
    # Pass both a wrong X-API-KEY and a valid bearer — bearer wins
    resp = client.get(
        "/v1/a2a/agent-card",
        headers={
            "X-API-KEY": "wrong-key-deliberately",
            "Authorization": f"Bearer {tok.access_token}",
        },
    )
    assert resp.status_code == 200


def test_x_api_key_still_works_with_deprecation_log(client) -> None:
    """X-API-KEY is still accepted (backward-compat).

    The deprecation log line ("deprecation: X-API-KEY used;
    switch to bearer token") is emitted via structlog to
    stdout; we verify the request still returns 200 here.
    """
    resp = client.get("/v1/a2a/agent-card")
    assert resp.status_code == 200


def test_no_auth_header_still_401(client) -> None:
    """No Authorization header + no X-API-KEY → 401.

    The default ``client`` fixture auto-attaches the master
    X-API-KEY (from conftest). We strip that and assert the
    request is rejected.
    """
    # Strip the auto-attached X-API-KEY. Use a fresh client
    # via TestClient that doesn't get the default header.
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app.core.config import Settings
    from app.middleware.security import InMemoryRateLimiter, AuthFailureTracker
    settings = Settings(
        APP_PORT=8000, LOG_LEVEL="WARNING",
        LLAMA_CPP_BASE_URL="http://llama.test",
        DEFAULT_MODEL="test-model", LITE_MODEL="test-lite",
        REQUEST_TIMEOUT_SECONDS=5.0, MAX_HISTORY_MESSAGES=5,
        LITE_MAX_HISTORY_MESSAGES=5,
        API_KEY="real-api-key-zzzzzzzzzzzz", RATE_LIMIT_PER_MINUTE=100,
        ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"],
        BACKGROUND_LLAMA_CPP_ENABLED="false",
        ENABLE_KNOWLEDGE_RAG=False,
    )
    app = create_app(settings=settings, limiter=InMemoryRateLimiter(100), failure_tracker=AuthFailureTracker(5, 60))
    bare = TestClient(app)
    resp = bare.get("/v1/a2a/agent-card")
    assert resp.status_code in (401, 403)
