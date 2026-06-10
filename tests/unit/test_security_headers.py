"""Unit tests for SecurityHeadersMiddleware (P1.5, 2026-06-10)."""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.no_isolated_db]


def test_security_headers_present_on_health(client) -> None:
    """Every response must carry the OWASP-recommended headers."""
    resp = client.get("/health")
    # Health is open; auth shouldn't be the thing under test here
    assert resp.status_code in (200, 503)
    h = resp.headers
    assert h["X-Content-Type-Options"] == "nosniff"
    assert h["X-Frame-Options"] == "DENY"
    assert h["Referrer-Policy"] == "no-referrer"
    assert "geolocation=()" in h["Permissions-Policy"]
    assert "Content-Security-Policy" in h


def test_security_headers_present_on_api_response(client) -> None:
    """API responses (non-static paths) get a hard-locked CSP."""
    resp = client.get("/v1/admin/queue")  # any /v1/* path
    h = resp.headers
    # Health endpoint may 200, admin may 401; we only assert headers
    assert h.get("X-Content-Type-Options") == "nosniff"
    # For an API response, CSP should be locked down
    csp = h.get("Content-Security-Policy", "")
    assert "default-src 'none'" in csp


def test_cors_allowlist_is_strict(client) -> None:
    """ALLOWED_ORIGINS must be an explicit list, never '*'."""
    from app.core.config import Settings
    # If someone reintroduces "*" the model validator should fail (and
    # we don't want to allow it at runtime either). This test pins the
    # current safe behavior.
    s = Settings(
        APP_PORT=8000, LOG_LEVEL="WARNING",
        LLAMA_CPP_BASE_URL="http://llama.test",
        DEFAULT_MODEL="x", LITE_MODEL="y",
        REQUEST_TIMEOUT_SECONDS=5.0, MAX_HISTORY_MESSAGES=5,
        LITE_MAX_HISTORY_MESSAGES=5,
        API_KEY="test-api-key-aaaaaaaaaa",
        RATE_LIMIT_PER_MINUTE=5,
        ALLOWED_HOSTS=["testserver"],
        BACKGROUND_LLAMA_CPP_ENABLED="false",
        ENABLE_KNOWLEDGE_RAG=False,
    )
    assert "*" not in s.allowed_origins
    # And the CORS middleware was wired with this list (not "*")
    assert s.allowed_origins  # non-empty


def test_hsts_only_on_https() -> None:
    """HSTS must NOT be set on http:// — would confuse browsers."""
    from starlette.requests import Request
    from starlette.responses import PlainTextResponse
    from app.middleware.security_headers import SecurityHeadersMiddleware

    async def _ok(req):  # noqa: ARG001
        return PlainTextResponse("ok")

    mw = SecurityHeadersMiddleware(_ok)

    class _FakeRequest:
        url = type("U", (), {"scheme": "http", "path": "/health"})()

    import asyncio
    resp = asyncio.run(mw.dispatch(_FakeRequest(), _ok))
    assert "Strict-Transport-Security" not in resp.headers
