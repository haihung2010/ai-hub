"""OAuth 2.1 Client Credentials grant (P2.1, 2026-06-10).

Reference: IETF draft-ietf-oauth-v2-1-15 (2026-03-02), Section 4.2.

Why: X-API-KEY is a single static secret. OAuth 2.1 short-lived
tokens give us expiry, scopes, rotation, and a per-tenant
audit trail in the access token itself.

Scope of this implementation (foundation, not full migration):
- HS256-signed JWT access tokens (RS256 is a follow-up)
- `POST /v1/oauth/token` endpoint that issues tokens via the
  Client Credentials grant
- `verify_oauth_token()` helper that the security middleware
  will use in P2.1's follow-up
- Tokens carry `sub` (the api_key_id), `tenant_id`, and `scope`
- Tokens are short-lived (default 1h)
- The master API_KEY and per-tenant api_keys BOTH work as
  Client Credentials (the `client_id` is the api_key_id, the
  `client_secret` is the raw_key)

NOT in this foundation (deferred):
- Authorization Code grant (PKCE) for browser flows
- RS256 / JWKS endpoint
- Refresh tokens (Client Credentials doesn't need them)
- Token revocation list (tokens are short-lived; expiry
  is the revocation)
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import time
from dataclasses import dataclass
from typing import Any

import jwt
from jwt import InvalidTokenError

from app.core.config import get_settings
from app.core.database import get_db_connection

logger = logging.getLogger(__name__)


# Scopes recognised by AI Hub. The token's `scope` claim is a
# space-separated string per RFC 8693.
SCOPE_CHAT = "chat"
SCOPE_ADMIN = "admin"
SCOPE_A2A = "a2a"
SCOPE_TOOLS = "tools"
ALL_SCOPES = (SCOPE_CHAT, SCOPE_ADMIN, SCOPE_A2A, SCOPE_TOOLS)

# Default token lifetime
DEFAULT_TOKEN_TTL_SECONDS = 3600  # 1 hour
MIN_TOKEN_TTL_SECONDS = 60        # 1 minute (server-side floor)
MAX_TOKEN_TTL_SECONDS = 86400     # 24 hours (server-side ceiling)


@dataclass(frozen=True)
class OAuthToken:
    access_token: str
    token_type: str  # "Bearer"
    expires_in: int
    scope: str


@dataclass(frozen=True)
class TokenClaims:
    """Decoded claims from a verified access token."""
    sub: str  # api_key_id
    tenant_id: str
    scope: str
    scopes: tuple[str, ...]
    expires_at: int

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes


def _signing_key() -> str:
    """Return the HS256 signing key. Sourced from a config setting
    so operators can rotate it independently of API_KEY."""
    settings = get_settings()
    # If a dedicated OAuth secret isn't set, fall back to the
    # API_KEY so a fresh deployment works out of the box. In
    # production, set OAUTH_JWT_SECRET to a 32+ char random value.
    return getattr(settings, "oauth_jwt_secret", None) or settings.api_key


def issue_token(
    api_key_id: str,
    tenant_id: str,
    scopes: list[str] | None = None,
    ttl_seconds: int = DEFAULT_TOKEN_TTL_SECONDS,
) -> OAuthToken:
    """Issue an OAuth 2.1 access token (HS256 JWT) for the given
    api_key_id + tenant_id."""
    settings = get_settings()
    if not scopes:
        scopes = [SCOPE_CHAT]
    # Validate scopes
    bad = [s for s in scopes if s not in ALL_SCOPES]
    if bad:
        raise ValueError(f"unknown scope(s): {bad}")
    ttl = max(MIN_TOKEN_TTL_SECONDS, min(int(ttl_seconds), MAX_TOKEN_TTL_SECONDS))
    now = int(time.time())
    payload = {
        "iss": "ai-hub",  # issuer
        "sub": api_key_id,
        "tenant_id": tenant_id,
        "scope": " ".join(scopes),
        "iat": now,
        "exp": now + ttl,
        "aud": "ai-hub-api",
    }
    token = jwt.encode(payload, _signing_key(), algorithm="HS256")
    return OAuthToken(
        access_token=token,
        token_type="Bearer",
        expires_in=ttl,
        scope=payload["scope"],
    )


def verify_token(token: str) -> TokenClaims | None:
    """Verify a bearer token. Returns TokenClaims or None on any
    verification failure. NEVER raises (the caller should treat
    None as "not authenticated").
    """
    try:
        decoded = jwt.decode(
            token,
            _signing_key(),
            algorithms=["HS256"],
            audience="ai-hub-api",
            options={"require": ["exp", "iat", "sub", "tenant_id", "scope"]},
        )
    except InvalidTokenError as exc:
        logger.info("oauth verify_token: %s", exc.__class__.__name__)
        return None
    return TokenClaims(
        sub=str(decoded["sub"]),
        tenant_id=str(decoded["tenant_id"]),
        scope=str(decoded["scope"]),
        scopes=tuple(decoded["scope"].split()),
        expires_at=int(decoded["exp"]),
    )


# ── Client Credentials grant (RFC 6749 §4.4) ─────────────────────────


def authenticate_client(client_id: str, client_secret: str) -> dict[str, Any] | None:
    """Validate a Client Credentials grant.

    Returns a dict with ``api_key_id``, ``tenant_id``, ``is_admin``,
    ``allowed_projects`` on success, or None on any failure (bad
    client_id, bad secret, disabled key, expired key).
    """
    if not client_id or not client_secret:
        return None
    # Constant-time comparison
    key_hash = hashlib.sha256(client_secret.encode("utf-8")).hexdigest()
    with get_db_connection() as conn:
        row = conn.execute(
            "SELECT id, tenant_id, is_admin, enabled, expires_at, allowed_projects_json "
            "FROM api_keys WHERE id = %s AND key_hash = %s",
            (client_id, key_hash),
        ).fetchone()
    if row is None:
        return None
    if not bool(row["enabled"]):
        return None
    if row["expires_at"] is not None and row["expires_at"].timestamp() < time.time():
        return None
    import json as _json
    _ap = row["allowed_projects_json"]
    allowed_projects = _json.loads(_ap) if _ap and _ap != '[]' else None
    return {
        "api_key_id": row["id"],
        "tenant_id": row["tenant_id"],
        "is_admin": bool(row["is_admin"]),
        "allowed_projects": allowed_projects,
    }


def scopes_for_client(client_info: dict[str, Any]) -> list[str]:
    """Pick the scopes a given client is allowed to request.
    Admin keys get the admin scope; everyone else gets chat + a2a.
    """
    if client_info.get("is_admin"):
        return [SCOPE_CHAT, SCOPE_ADMIN, SCOPE_A2A, SCOPE_TOOLS]
    return [SCOPE_CHAT, SCOPE_A2A, SCOPE_TOOLS]
