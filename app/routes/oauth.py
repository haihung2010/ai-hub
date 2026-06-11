"""OAuth 2.1 endpoints (P2.1, 2026-06-10).

Implements the Client Credentials grant (RFC 6749 §4.4, replaced
by OAuth 2.1 §4.2 — IETF draft-15, 2026-03-02).

POST /v1/oauth/token
  Body: grant_type=client_credentials&client_id=...&client_secret=...
        (or Basic auth header with client_id:client_secret)
  Response: {"access_token": "...", "token_type": "Bearer",
             "expires_in": 3600, "scope": "chat a2a tools"}

This endpoint does NOT require an existing API key. The
client_id IS the API key id, the client_secret IS the raw API
key. So a tenant who already has an api_key can just
client_credentials-grant their way to a bearer token.
"""
from __future__ import annotations

import base64
import logging
from typing import Any

from fastapi import APIRouter, Form, HTTPException, Request

from app.services.oauth_service import (
    DEFAULT_TOKEN_TTL_SECONDS,
    SCOPE_ADMIN,
    SCOPE_CHAT,
    authenticate_client,
    issue_token,
    scopes_for_client,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/oauth", tags=["oauth"])


def _parse_basic_auth(header_value: str | None) -> tuple[str | None, str | None]:
    """Parse `Authorization: Basic <b64(client_id:client_secret)>`."""
    if not header_value or not header_value.lower().startswith("basic "):
        return None, None
    try:
        b64 = header_value.split(None, 1)[1]
        decoded = base64.b64decode(b64).decode("utf-8")
        if ":" not in decoded:
            return None, None
        cid, _, csec = decoded.partition(":")
        return cid, csec
    except Exception:
        return None, None


@router.post(
    "/token",
    summary="OAuth 2.1 Client Credentials grant",
    description=(
        "Exchange an API key (client_id + client_secret) for a "
        "short-lived bearer access token. The bearer token can "
        "be used in the Authorization: Bearer header on subsequent "
        "requests. Per OAuth 2.1 §4.2 the grant is mandatory-PKCE-free "
        "for M2M (the client IS the resource server, no user agent)."
    ),
)
async def oauth_token(
    request: Request,
    grant_type: str = Form(...),
    client_id: str | None = Form(default=None),
    client_secret: str | None = Form(default=None),
    scope: str | None = Form(default=None),
) -> dict[str, Any]:
    if grant_type != "client_credentials":
        # RFC 6749 §5.2: unsupported_grant_type
        raise HTTPException(
            status_code=400,
            detail={"error": "unsupported_grant_type",
                    "error_description": f"only 'client_credentials' supported, got {grant_type!r}"},
        )

    # Form params take precedence; Basic auth header is a fallback
    # (RFC 6749 §2.3.1).
    if not client_id or not client_secret:
        auth = request.headers.get("Authorization", "")
        h_cid, h_csec = _parse_basic_auth(auth)
        client_id = client_id or h_cid
        client_secret = client_secret or h_csec

    if not client_id or not client_secret:
        raise HTTPException(
            status_code=401,
            detail={"error": "invalid_client",
                    "error_description": "client_id and client_secret required (form params or Basic auth)"},
        )

    client_info = authenticate_client(client_id, client_secret)
    if client_info is None:
        # RFC 6749 §5.2: invalid_client (or 401, both acceptable)
        raise HTTPException(
            status_code=401,
            detail={"error": "invalid_client",
                    "error_description": "bad client_id or client_secret"},
        )

    # Honour the requested scope if it's a subset of the client's
    # allowed scopes; otherwise default to all allowed.
    allowed = set(scopes_for_client(client_info))
    if scope:
        requested = set(scope.split())
        if not requested.issubset(allowed):
            raise HTTPException(
                status_code=400,
                detail={"error": "invalid_scope",
                        "error_description": f"requested scopes {requested} not a subset of {allowed}"},
            )
        granted = sorted(requested)
    else:
        granted = sorted(allowed)

    token = issue_token(
        api_key_id=client_info["api_key_id"],
        tenant_id=client_info["tenant_id"],
        scopes=granted,
        ttl_seconds=DEFAULT_TOKEN_TTL_SECONDS,
    )
    return {
        "access_token": token.access_token,
        "token_type": token.token_type,
        "expires_in": token.expires_in,
        "scope": token.scope,
    }
