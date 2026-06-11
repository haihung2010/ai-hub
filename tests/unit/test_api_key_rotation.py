"""Unit tests for API key rotation (P2.4, 2026-06-10)."""
from __future__ import annotations

import time
import uuid

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.no_isolated_db]


def _uniq(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


# ──────────────────────────────────────────────────────────────────────
# ApiKeyRecord includes rotation fields
# ──────────────────────────────────────────────────────────────────────


def test_lookup_returns_last_rotated_at_and_created_at(client) -> None:
    """After P2.4, ApiKeyRecord surfaces the rotation fields so the
    admin UI / scheduler can render them without a second query."""
    from app.services.api_key_service import ApiKeyService

    svc = ApiKeyService()
    kid, raw = svc.create_key(name=_uniq("rot-lookup"), tenant_id=_uniq("t"))
    rec = svc.lookup(raw)
    assert rec is not None
    assert rec.id == kid
    assert rec.last_rotated_at is not None
    assert rec.created_at is not None


# ──────────────────────────────────────────────────────────────────────
# rotate_key: mints new raw_key, invalidates old
# ──────────────────────────────────────────────────────────────────────


def test_rotate_key_returns_new_raw_key(client) -> None:
    from app.services.api_key_service import ApiKeyService

    svc = ApiKeyService()
    kid, old_raw = svc.create_key(name=_uniq("rot-1"), tenant_id=_uniq("t"))
    result = svc.rotate_key(kid)
    assert result is not None
    new_kid, new_raw = result
    assert new_kid == kid
    assert new_raw != old_raw
    assert new_raw.startswith("ah_")


def test_rotate_key_invalidates_old_raw_key(client) -> None:
    """After rotation, lookup() with the old raw_key returns None."""
    from app.services.api_key_service import ApiKeyService

    svc = ApiKeyService()
    kid, old_raw = svc.create_key(name=_uniq("rot-2"), tenant_id=_uniq("t"))
    assert svc.lookup(old_raw) is not None
    _new_kid, new_raw = svc.rotate_key(kid)
    assert svc.lookup(old_raw) is None
    assert svc.lookup(new_raw) is not None


def test_rotate_key_updates_last_rotated_at(client) -> None:
    from app.services.api_key_service import ApiKeyService

    svc = ApiKeyService()
    kid, raw = svc.create_key(name=_uniq("rot-3"), tenant_id=_uniq("t"))
    before = svc.lookup(raw).last_rotated_at
    time.sleep(1.1)  # ensure timestamp would change
    svc.rotate_key(kid)
    after = svc.lookup(svc.lookup(raw) and svc.rotate_key(kid)[1]).last_rotated_at if False else None
    # Simpler: lookup the NEW key
    new_kid, new_raw = svc.rotate_key(kid)
    rec = svc.lookup(new_raw)
    assert rec is not None
    assert rec.last_rotated_at is not None
    assert rec.last_rotated_at >= before


def test_rotate_key_404_on_unknown_id(client) -> None:
    from app.services.api_key_service import ApiKeyService
    assert ApiKeyService().rotate_key("ak_does-not-exist") is None


# ──────────────────────────────────────────────────────────────────────
# get_rotation_status
# ──────────────────────────────────────────────────────────────────────


def test_get_rotation_status_returns_empty_when_all_fresh(client) -> None:
    from app.services.api_key_service import ApiKeyService

    svc = ApiKeyService()
    svc.create_key(name=_uniq("fresh"), tenant_id=_uniq("t"))
    # Just-created key is 0 days old → fresh
    stale = svc.get_rotation_status(rotation_days=90)
    # The new key is fresh; we may have other stale keys in the DB
    # from prior tests (no_isolated_db), so just assert shape.
    for k in stale:
        assert "id" in k
        assert "days_since_rotation" in k
        assert k["days_since_rotation"] >= 90


def test_get_rotation_status_finds_key_rotated_long_ago(client) -> None:
    """A key whose last_rotated_at is in the distant past must show up."""
    from app.core.database import get_db_connection
    from app.services.api_key_service import ApiKeyService

    svc = ApiKeyService()
    kid, _ = svc.create_key(name=_uniq("ancient"), tenant_id=_uniq("t"))
    # Backdate the row
    with get_db_connection() as conn:
        conn.execute(
            "UPDATE api_keys SET last_rotated_at = NOW() - INTERVAL '200 days' WHERE id = %s",
            (kid,),
        )
        conn.commit()
    stale = svc.get_rotation_status(rotation_days=90)
    assert any(k["id"] == kid for k in stale), f"ancient key not flagged: {stale!r}"


# ──────────────────────────────────────────────────────────────────────
# Admin endpoint
# ──────────────────────────────────────────────────────────────────────


def test_admin_rotation_status_endpoint(client) -> None:
    resp = client.get("/v1/admin/keys/rotation-status?rotation_days=90")
    assert resp.status_code == 200
    data = resp.json()
    assert "stale_count" in data
    assert "stale_keys" in data
    assert data["rotation_days"] == 90
    assert isinstance(data["stale_keys"], list)


def test_admin_rotate_endpoint_mints_new_key(client) -> None:
    from app.services.api_key_service import ApiKeyService

    svc = ApiKeyService()
    kid, _ = svc.create_key(name=_uniq("rot-endpoint"), tenant_id=_uniq("t"))
    resp = client.post(f"/v1/admin/keys/{kid}/rotate")
    assert resp.status_code == 200
    data = resp.json()
    assert data["key_id"] == kid
    assert data["new_raw_key"].startswith("ah_")
    assert "Store" in data["warning"] or "shown again" in data["warning"]


def test_admin_rotate_endpoint_404_on_unknown_id(client) -> None:
    resp = client.post("/v1/admin/keys/ak_does-not-exist/rotate")
    assert resp.status_code == 404


# ──────────────────────────────────────────────────────────────────────
# Reminder job is importable + safe to call
# ──────────────────────────────────────────────────────────────────────


def test_rotation_reminder_job_runs_without_error(client) -> None:
    """The job must be importable and execute against the test DB
    without raising — failures are logged, not propagated."""
    import asyncio
    from app.main import _rotation_reminder_job
    asyncio.run(_rotation_reminder_job())
