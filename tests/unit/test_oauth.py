"""Unit tests for OAuth 2.1 Client Credentials grant (P2.1, 2026-06-10)."""
from __future__ import annotations

import time
import uuid

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.no_isolated_db]


def _uniq(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


# ──────────────────────────────────────────────────────────────────────
# Pure unit tests — issue / verify
# ──────────────────────────────────────────────────────────────────────


def test_issue_and_verify_round_trip(client) -> None:
    from app.services.oauth_service import issue_token, verify_token
    tok = issue_token(api_key_id="ak_test", tenant_id="t1", scopes=["chat", "a2a"])
    claims = verify_token(tok.access_token)
    assert claims is not None
    assert claims.sub == "ak_test"
    assert claims.tenant_id == "t1"
    assert "chat" in claims.scopes
    assert "a2a" in claims.scopes


def test_verify_token_rejects_tampered_token(client) -> None:
    from app.services.oauth_service import issue_token, verify_token
    tok = issue_token(api_key_id="ak_x", tenant_id="t1")
    # Flip a character in the signature segment
    parts = tok.access_token.split(".")
    parts[2] = parts[2][:-1] + ("A" if parts[2][-1] != "A" else "B")
    bad = ".".join(parts)
    assert verify_token(bad) is None


def test_verify_token_rejects_expired(client) -> None:
    """Craft an already-expired token directly and verify it's rejected.

    We bypass issue_token() because the service clamps ttl_seconds
    to MIN_TOKEN_TTL_SECONDS (60s) to discourage pathologically
    short tokens. Building the JWT by hand is the only way to test
    the expiry code path.
    """
    import jwt as _jwt
    from app.services.oauth_service import _signing_key, verify_token
    now = int(time.time()) - 10  # 10s ago
    payload = {
        "iss": "ai-hub",
        "sub": "ak_x",
        "tenant_id": "t1",
        "scope": "chat",
        "iat": now - 100,
        "exp": now,  # already expired
        "aud": "ai-hub-api",
    }
    expired_token = _jwt.encode(payload, _signing_key(), algorithm="HS256")
    assert verify_token(expired_token) is None


def test_verify_token_rejects_garbage() -> None:
    from app.services.oauth_service import verify_token
    assert verify_token("not-a-jwt") is None
    assert verify_token("") is None
    assert verify_token("a.b.c") is None  # 3 parts but not a JWT


def test_issue_token_unknown_scope_raises(client) -> None:
    from app.services.oauth_service import issue_token
    try:
        issue_token(api_key_id="ak_x", tenant_id="t1", scopes=["bogus"])
    except ValueError as e:
        assert "bogus" in str(e)
    else:
        raise AssertionError("expected ValueError for unknown scope")


def test_issue_token_ttl_clamped_to_max(client) -> None:
    """ttl_seconds > MAX (24h) gets clamped, not rejected."""
    from app.services.oauth_service import MAX_TOKEN_TTL_SECONDS, issue_token
    tok = issue_token(api_key_id="ak_x", tenant_id="t1", ttl_seconds=999999)
    assert tok.expires_in == MAX_TOKEN_TTL_SECONDS


# ──────────────────────────────────────────────────────────────────────
# Client Credentials — authenticate_client
# ──────────────────────────────────────────────────────────────────────


def test_authenticate_client_with_valid_key(client) -> None:
    from app.services.api_key_service import ApiKeyService
    from app.services.oauth_service import authenticate_client
    svc = ApiKeyService()
    kid, raw = svc.create_key(name=_uniq("oauth"), tenant_id=_uniq("oauth-t"))
    info = authenticate_client(kid, raw)
    assert info is not None
    assert info["api_key_id"] == kid
    assert info["tenant_id"].startswith("oauth-t-")


def test_authenticate_client_with_bad_secret(client) -> None:
    from app.services.api_key_service import ApiKeyService
    from app.services.oauth_service import authenticate_client
    svc = ApiKeyService()
    kid, _raw = svc.create_key(name=_uniq("oauth-bad"), tenant_id=_uniq("oauth-t"))
    assert authenticate_client(kid, "definitely-wrong") is None


def test_authenticate_client_unknown_id(client) -> None:
    from app.services.oauth_service import authenticate_client
    assert authenticate_client("ak_does-not-exist", "anything") is None


# ──────────────────────────────────────────────────────────────────────
# /v1/oauth/token endpoint
# ──────────────────────────────────────────────────────────────────────


def test_oauth_token_endpoint_form_params(client) -> None:
    from app.services.api_key_service import ApiKeyService
    svc = ApiKeyService()
    kid, raw = svc.create_key(name=_uniq("oauth-endpoint"), tenant_id=_uniq("oauth-t"))
    resp = client.post(
        "/v1/oauth/token",
        data={
            "grant_type": "client_credentials",
            "client_id": kid,
            "client_secret": raw,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["token_type"] == "Bearer"
    assert body["expires_in"] > 0
    assert body["access_token"]
    # Scope must include chat at minimum
    assert "chat" in body["scope"].split()


def test_oauth_token_endpoint_basic_auth(client) -> None:
    import base64
    from app.services.api_key_service import ApiKeyService
    svc = ApiKeyService()
    kid, raw = svc.create_key(name=_uniq("oauth-basic"), tenant_id=_uniq("oauth-t"))
    creds = base64.b64encode(f"{kid}:{raw}".encode()).decode()
    resp = client.post(
        "/v1/oauth/token",
        data={"grant_type": "client_credentials"},
        headers={"Authorization": f"Basic {creds}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["access_token"]


def test_oauth_token_endpoint_rejects_unknown_grant_type(client) -> None:
    resp = client.post(
        "/v1/oauth/token",
        data={"grant_type": "password", "client_id": "x", "client_secret": "y"},
    )
    assert resp.status_code == 400
    assert "unsupported_grant_type" in resp.text


def test_oauth_token_endpoint_rejects_bad_credentials(client) -> None:
    resp = client.post(
        "/v1/oauth/token",
        data={"grant_type": "client_credentials", "client_id": "ak_nope", "client_secret": "nope"},
    )
    assert resp.status_code == 401
    assert "invalid_client" in resp.text


def test_oauth_token_endpoint_honours_requested_scope(client) -> None:
    from app.services.api_key_service import ApiKeyService
    svc = ApiKeyService()
    kid, raw = svc.create_key(name=_uniq("oauth-scope"), tenant_id=_uniq("oauth-t"))
    resp = client.post(
        "/v1/oauth/token",
        data={
            "grant_type": "client_credentials",
            "client_id": kid,
            "client_secret": raw,
            "scope": "chat",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["scope"] == "chat"


def test_oauth_token_endpoint_rejects_scope_outside_allowed(client) -> None:
    """A non-admin client asking for the admin scope gets 400."""
    from app.services.api_key_service import ApiKeyService
    svc = ApiKeyService()
    kid, raw = svc.create_key(name=_uniq("oauth-noadmin"), tenant_id=_uniq("oauth-t"), is_admin=False)
    resp = client.post(
        "/v1/oauth/token",
        data={
            "grant_type": "client_credentials",
            "client_id": kid,
            "client_secret": raw,
            "scope": "admin",
        },
    )
    assert resp.status_code == 400
    assert "invalid_scope" in resp.text


# ──────────────────────────────────────────────────────────────────────
# Round-trip: issued token verifies + has correct claims
# ──────────────────────────────────────────────────────────────────────


def test_issued_token_verifies_with_correct_tenant(client) -> None:
    from app.services.api_key_service import ApiKeyService
    from app.services.oauth_service import verify_token
    svc = ApiKeyService()
    kid, raw = svc.create_key(name=_uniq("oauth-roundtrip"), tenant_id=_uniq("oauth-rt-t"))
    import base64
    creds = base64.b64encode(f"{kid}:{raw}".encode()).decode()
    resp = client.post(
        "/v1/oauth/token",
        data={"grant_type": "client_credentials"},
        headers={"Authorization": f"Basic {creds}"},
    )
    token = resp.json()["access_token"]
    claims = verify_token(token)
    assert claims is not None
    assert claims.sub == kid
    assert claims.tenant_id.startswith("oauth-rt-t-")
