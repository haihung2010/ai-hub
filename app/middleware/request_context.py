"""Request context middleware (P1.3, 2026-06-10).

Binds a request-scoped structlog context (request_id, tenant_id,
api_key_id, path, method) to every log line emitted while the
request is being processed. Lets operators grep one request across
dozens of log lines and across services.

Reference: Stripe's Canonical Log Lines pattern.
"""
from __future__ import annotations

import time
import uuid

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Bind request_id + tenant_id + api_key_id to structlog context.

    Uses ``contextvars`` so the binding is async-safe: two concurrent
    requests on the same worker don't bleed context into each other.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        # Pull request_id from the incoming header (so retries from
        # the same client share a trace) or mint a new one.
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:16]
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            method=request.method,
            path=request.url.path,
        )
        start = time.monotonic()
        try:
            response = await call_next(request)
        finally:
            duration_ms = int((time.monotonic() - start) * 1000)
            # Bind tenant/api_key AFTER dispatch — middleware sets them
            # on request.state only if the request authenticated.
            tenant_id = getattr(request.state, "api_key_tenant_id", None)
            api_key_id = getattr(request.state, "api_key_id", None)
            bind = {"duration_ms": duration_ms, "status_code": 0}
            if tenant_id is not None:
                bind["tenant_id"] = str(tenant_id)
            if api_key_id is not None:
                bind["api_key_id"] = str(api_key_id)
            structlog.contextvars.bind_contextvars(**bind)
            structlog.contextvars.unbind_contextvars(
                "method", "path", "duration_ms", "status_code",
                "tenant_id", "api_key_id",
            )
        # Echo request_id back so clients can quote it in support tickets
        response.headers["X-Request-ID"] = request_id
        return response
