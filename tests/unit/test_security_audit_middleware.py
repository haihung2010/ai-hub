"""Verify SecurityMiddleware actually calls the PG audit writer on denials.

We patch `app.services.security_audit` to record the calls without
touching the database, then drive a single request through dispatch
and assert the writer was invoked for both rate-limit and auth-failure
denial paths.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.middleware.security import (
    InMemoryRateLimiter,
    SecurityMiddleware,
)


class _FakeApiKeyService:
    def lookup(self, raw_key: str):
        return None


def _build_middleware(settings):
    limiter = InMemoryRateLimiter(limit=1, window_seconds=60)
    middleware = SecurityMiddleware.__new__(SecurityMiddleware)
    # Skip the BaseHTTPMiddleware.__init__ to keep this hermetic.
    middleware._settings = settings
    middleware._limiter = limiter
    middleware._limiters_by_limit = {settings.rate_limit_per_minute: limiter}
    middleware._failure_tracker = MagicMock()
    middleware._allowed_hosts = {"*"}
    middleware._api_keys = _FakeApiKeyService()
    return middleware


def _make_settings(api_key: str = "primary-key"):
    settings = MagicMock()
    settings.api_key = api_key
    settings.rate_limit_per_minute = 5
    settings.allowed_hosts = ["*"]
    settings.public_health_enabled = False
    settings.public_docs_enabled = False
    settings.auth_failure_limit = 10
    settings.auth_failure_block_seconds = 900
    return settings


def _make_request(headers: dict, path: str = "/v1/chat", method: str = "POST"):
    request = MagicMock()
    request.url.path = path
    request.method = method
    # Starlette Headers are case-insensitive, but our lookup uses the
    # exact constant "X-API-KEY". Use a Starlette Headers object so
    # case-insensitive .get() works the way the real middleware sees it.
    from starlette.datastructures import Headers

    request.headers = Headers(headers)
    request.client.host = "9.9.9.9"  # non-loopback so auth path executes
    return request


@pytest.mark.asyncio
@pytest.mark.unit
async def test_auth_failure_invokes_pg_audit() -> None:
    middleware = _build_middleware(_make_settings(api_key="primary-key"))
    request = _make_request({"host": "x", "x-api-key": "bad-key"})

    with patch("app.services.security_audit", create=True) as audit:
        response = await middleware.dispatch(request, _call_next_ok)
    assert response.status_code == 401
    assert audit.record_auth_failure.called


@pytest.mark.asyncio
@pytest.mark.unit
async def test_rate_limit_invokes_pg_audit() -> None:
    middleware = _build_middleware(_make_settings(api_key="primary-key"))
    # First request passes auth, first limiter call passes (1/1).
    req1 = _make_request({"host": "x", "x-api-key": "primary-key"})
    # Second request for the same client/IP is denied by the limiter.
    req2 = _make_request({"host": "x", "x-api-key": "primary-key"})

    # The rate key is hashed from (ip, provided_key) so the second
    # request needs to land on the same bucket. Compute it.
    from app.middleware.security import SecurityMiddleware as SM

    rate_key = SM._rate_limit_key("9.9.9.9", "primary-key", None)
    # Pre-fill bucket at the limit.
    assert middleware._limiter.allow(rate_key, now=0.0) is True
    assert middleware._limiter.allow(rate_key, now=0.0) is False

    with patch("app.services.security_audit", create=True) as audit:
        response = await middleware.dispatch(req1, _call_next_ok)
    # req1 consumes the last slot, no denial.
    assert response.status_code == 200
    assert not audit.record_rate_limit.called

    # req2 will be denied.
    with patch("app.services.security_audit", create=True) as audit:
        response = await middleware.dispatch(req2, _call_next_ok)
    assert response.status_code == 429
    assert audit.record_rate_limit.called


async def _call_next_ok(request):
    return MagicMock(status_code=200)
