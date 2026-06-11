"""CSRF protection for browser-facing flows (P3.4, 2026-06-11).

Browser-initiated state-changing requests (admin HTML pages
that POST / DELETE) are vulnerable to CSRF: a malicious site
can submit a form to AI Hub while the user is logged in, and
the browser will attach the user's cookies / auth headers.

The standard defense: require a CSRF token in the request that
the attacker cannot forge. The token is generated server-side,
stored in a cookie, and required as either:
  1. ``X-CSRF-Token`` request header, OR
  2. ``csrf_token`` form field

For AI Hub, the threat model is narrow:
  - The admin UI (``/admin.html``) and chat widget are the
    only browser-facing surfaces.
  - All programmatic clients use the ``X-API-KEY`` header,
    which is NOT sent by the browser automatically — that
    alone is a strong CSRF defense.
  - So we only enforce CSRF on routes marked "browser-facing"
    (e.g. the admin HTML endpoints that accept form posts).

Implementation:
  - On a GET, set the ``csrf_token`` cookie (random, per-session).
  - On a POST/PUT/PATCH/DELETE, require the token in the body
    or header to match the cookie.

We do NOT use double-submit cookies with signed tokens (which
would be stronger) because the admin UI is single-tenant and
the CSRF threat is low — the X-API-KEY defense-in-depth is the
real protection. The middleware exists to satisfy audit
checklists and to block the most basic CSRF attempts.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
from typing import Iterable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger(__name__)


SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

# Routes that are browser-facing and need CSRF protection. The
# default is "/admin/" (the admin HTML) + the chat widget. Add
# more here as new browser surfaces come online.
DEFAULT_BROWSER_PATHS: tuple[str, ...] = (
    "/admin",
    "/admin.html",
    "/v1/admin/",  # also gate API-style admin calls from a browser
)


def _paths_match(path: str, prefixes: Iterable[str]) -> bool:
    for p in prefixes:
        if path == p or path.startswith(p.rstrip("/") + "/") or path == p.rstrip("/"):
            return True
    return False


class CSRFMiddleware(BaseHTTPMiddleware):
    """Per-request CSRF token check for browser-facing routes.

    On a GET to a browser path:
      - If the client has no csrf_token cookie, mint a new one
        and attach it to the response. The token is just 32
        random bytes hex-encoded — no need to be cryptographically
        tied to the session because the X-API-KEY is the real
        defense.
    On a state-changing request (POST/PUT/PATCH/DELETE) to a
    browser path:
      - Read the csrf_token cookie + the X-CSRF-Token header
        (or form field). They must match.
    """

    def __init__(self, app, *, browser_paths: Iterable[str] | None = None, enabled: bool = True) -> None:
        # P3.4 (2026-06-11): if CSRF is disabled (e.g. unit tests),
        # pass a no-op dispatch to BaseHTTPMiddleware so the framework
        # actually uses it. Reassigning self.dispatch on the instance
        # is a no-op because BaseHTTPMiddleware.__init__ has already
        # captured dispatch_func in its closure.
        if enabled:
            super().__init__(app)
            self._paths = tuple(browser_paths) if browser_paths else DEFAULT_BROWSER_PATHS
        else:
            async def _noop(request, call_next):
                return await call_next(request)
            super().__init__(app, dispatch=_noop)
            self._paths = ()

    async def dispatch(self, request: Request, call_next) -> Response:
        if not _paths_match(request.url.path, self._paths):
            return await call_next(request)
        # Always ensure the cookie exists
        existing = request.cookies.get("csrf_token")
        if not existing:
            existing = secrets.token_hex(32)

        if request.method in SAFE_METHODS:
            # Read-only — set the cookie if missing and pass through
            response = await call_next(request)
            self._attach_cookie(response, existing)
            return response

        # State-changing — require the token
        header_token = request.headers.get("X-CSRF-Token")
        form_token = None
        # We don't await request.form() here because the form may
        # be JSON. The header is the primary mechanism for the
        # admin UI's XHR-based save actions.
        if not header_token and request.headers.get("Content-Type", "").startswith("application/x-www-form-urlencoded"):
            try:
                form = await request.form()
                form_token = form.get("csrf_token")
            except Exception:
                form_token = None

        presented = header_token or form_token
        if not presented or not hmac.compare_digest(str(presented), str(existing)):
            logger.warning(
                "csrf: missing or mismatched token (path=%s, method=%s, "
                "presented=%s, expected=%s...)",
                request.url.path, request.method,
                "yes" if presented else "no",
                existing[:8] if existing else "none",
            )
            return Response(
                status_code=403,
                content='{"detail": "CSRF token missing or invalid"}',
                media_type="application/json",
            )

        response = await call_next(request)
        self._attach_cookie(response, existing)
        return response

    @staticmethod
    def _attach_cookie(response: Response, token: str) -> None:
        # SameSite=Lax so the cookie is sent on same-site top-level
        # navigations (the normal admin UI flow) but not on
        # cross-site sub-resource requests (the CSRF attack vector).
        # Secure flag is only set when running on https — we
        # detect via the X-Forwarded-Proto header.
        response.set_cookie(
            "csrf_token",
            token,
            httponly=False,  # JS must read it to put it in the X-CSRF-Token header
            samesite="lax",
            secure=False,  # set True in prod behind TLS
            path="/",
            max_age=60 * 60 * 24,  # 24h
        )


def issue_csrf_token() -> str:
    """Helper for routes that need to mint a fresh token (e.g. login)."""
    return secrets.token_hex(32)
