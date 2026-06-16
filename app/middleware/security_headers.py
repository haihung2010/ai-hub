"""Security headers middleware (P1.5, 2026-06-10).

Adds OWASP-recommended security headers to every response:

- ``X-Content-Type-Options: nosniff`` — block MIME sniffing.
- ``X-Frame-Options: DENY`` — block clickjacking.
- ``Referrer-Policy: no-referrer`` — never leak the API URL to 3rd
  parties via the Referer header.
- ``Permissions-Policy: geolocation=(), microphone=()`` — disable
  powerful browser features that AI Hub never uses.
- ``Strict-Transport-Security: max-age=31536000; includeSubDomains`` —
  only on HTTPS responses. Tells browsers to refuse to talk to us
  over plain HTTP for the next year.
- ``Content-Security-Policy: default-src 'self'`` — minimal CSP for
  the static admin UI. API responses get ``default-src 'none'``.

Reference: OWASP Secure Headers Project, RFC 6797 (HSTS).
"""
from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

# Static-file paths that need a relaxed CSP so the admin UI's inline
# styles and the chat widget can load. The API responses themselves
# stay locked down to ``default-src 'none'``.
# IMPORTANT: order matters — exact-match roots ("/", "/index.html")
# are checked before prefix matches.
_RELAXED_CSP_EXACT = {"/", "/index.html", "/admin.html", "/manifest.json"}
_RELAXED_CSP_PREFIXES = ("/admin/", "/static", "/icon-", "/sw")


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        # Always-on headers
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
        # HSTS only over HTTPS — sending it over HTTP is meaningless
        # and confuses some browsers
        if request.url.scheme == "https":
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )
        # CSP: API responses get a hard lock; static/admin gets a relaxed
        # one so the UI can run inline styles + the chat widget can talk
        # to the API.
        path = request.url.path
        if path in _RELAXED_CSP_EXACT or any(
            path == p or path.startswith(p + "/") for p in _RELAXED_CSP_PREFIXES
        ):
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; "
                "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
                "font-src 'self' https://fonts.gstatic.com; "
                "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
                "img-src 'self' data: https:; "
                "connect-src 'self' https://cdn.jsdelivr.net; "
                "manifest-src 'self'; "
                "worker-src 'self'"
            )
        else:
            # API / JSON responses — no scripts, no media
            response.headers["Content-Security-Policy"] = "default-src 'none'"
        return response
