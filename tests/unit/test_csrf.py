"""Unit tests for CSRF middleware (P3.4, 2026-06-11)."""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.no_isolated_db]


# ──────────────────────────────────────────────────────────────────────
# GET sets the cookie
# ──────────────────────────────────────────────────────────────────────


def test_get_admin_sets_csrf_cookie(client) -> None:
    resp = client.get("/admin.html")
    assert "csrf_token" in resp.cookies
    # 32 random bytes hex = 64 chars
    assert len(resp.cookies["csrf_token"]) == 64


def test_get_admin_existing_cookie_is_preserved(client) -> None:
    """If the client already has a csrf_token cookie, GET keeps it
    and does NOT mint a new one (prevents session-fixation via
    cookie rotation)."""
    pre_existing = "a" * 64
    client.cookies.set("csrf_token", pre_existing)
    resp = client.get("/admin.html")
    assert resp.cookies.get("csrf_token", pre_existing) == pre_existing


# ──────────────────────────────────────────────────────────────────────
# State-changing without token → 403
# ──────────────────────────────────────────────────────────────────────


def test_post_admin_without_token_rejected(client) -> None:
    resp = client.post(
        "/v1/admin/keys",
        json={"name": "hijack"},
    )
    assert resp.status_code == 403
    assert "CSRF" in resp.json()["detail"]


def test_post_admin_with_mismatched_token_rejected(client) -> None:
    client.cookies.set("csrf_token", "a" * 64)
    resp = client.post(
        "/v1/admin/keys",
        json={"name": "hijack"},
        headers={"X-CSRF-Token": "b" * 64},
    )
    assert resp.status_code == 403


# ──────────────────────────────────────────────────────────────────────
# State-changing with matching token → passes through
# ──────────────────────────────────────────────────────────────────────


def test_post_admin_with_matching_header_token_passes(client) -> None:
    token = "a" * 64
    client.cookies.set("csrf_token", token)
    resp = client.post(
        "/v1/admin/keys",
        json={"name": "ok"},
        headers={"X-CSRF-Token": token},
    )
    # The actual admin endpoint may 200, 4xx, or 5xx — what
    # matters is the CSRF middleware didn't block it.
    assert resp.status_code != 403, f"CSRF middleware blocked a valid request: {resp.text}"


# ──────────────────────────────────────────────────────────────────────
# API-only routes are NOT subject to CSRF
# ──────────────────────────────────────────────────────────────────────


def test_api_only_route_skips_csrf(client) -> None:
    """Non-browser-facing routes (e.g. /v1/chat) are not protected
    by CSRF — the X-API-KEY header is the defense there."""
    # No cookie, no header — POST should NOT 403
    resp = client.post(
        "/v1/chat",
        json={
            "project_id": "csrftest",
            "user_message": "hi",
        },
    )
    # The CSRF middleware should not have blocked this.
    # (May still 422 / 4xx for other reasons.)
    assert resp.status_code != 403 or "CSRF" not in resp.text, (
        f"CSRF middleware blocked an API call: {resp.text}"
    )


# ──────────────────────────────────────────────────────────────────────
# issue_csrf_token helper
# ──────────────────────────────────────────────────────────────────────


def test_issue_csrf_token_returns_64_hex_chars() -> None:
    from app.middleware.csrf import issue_csrf_token
    t1 = issue_csrf_token()
    t2 = issue_csrf_token()
    assert t1 != t2  # unique
    assert len(t1) == 64
    int(t1, 16)  # parseable as hex
    int(t2, 16)
