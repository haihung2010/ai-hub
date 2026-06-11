"""Unit tests for GDPR service (P2.6, 2026-06-10)."""
from __future__ import annotations

import uuid

import pytest

from tests.conftest import ensure_user

pytestmark = [pytest.mark.unit, pytest.mark.no_isolated_db]


def _uniq(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


# ──────────────────────────────────────────────────────────────────────
# request_deletion
# ──────────────────────────────────────────────────────────────────────


def test_request_deletion_schedules_30_days_out(client) -> None:
    from app.services.gdpr_service import get_user_gdpr_status, request_deletion
    uid = _uniq("gdpr-req")
    ensure_user(uid, "default", uid)
    result = request_deletion(uid)
    assert result is not None
    assert result["is_pending_deletion"] is True
    assert result["gdpr_delete_scheduled_for"] is not None
    assert result["gdpr_deleted_at"] is None


def test_request_deletion_idempotent(client) -> None:
    from app.services.gdpr_service import request_deletion
    uid = _uniq("gdpr-idem")
    ensure_user(uid, "default", uid)
    first = request_deletion(uid)
    second = request_deletion(uid)
    assert first["gdpr_delete_scheduled_for"] == second["gdpr_delete_scheduled_for"]


def test_request_deletion_unknown_user_returns_none(client) -> None:
    from app.services.gdpr_service import request_deletion
    assert request_deletion("u_does-not-exist") is None


# ──────────────────────────────────────────────────────────────────────
# cancel_deletion
# ──────────────────────────────────────────────────────────────────────


def test_cancel_deletion_clears_scheduled_for(client) -> None:
    from app.services.gdpr_service import cancel_deletion, request_deletion
    uid = _uniq("gdpr-cancel")
    ensure_user(uid, "default", uid)
    request_deletion(uid)
    cancelled = cancel_deletion(uid)
    assert cancelled is not None
    assert cancelled["is_pending_deletion"] is False
    assert cancelled["gdpr_delete_scheduled_for"] is None
    assert cancelled["gdpr_delete_requested_at"] is not None


def test_cancel_deletion_no_op_when_none_pending(client) -> None:
    from app.services.gdpr_service import cancel_deletion
    uid = _uniq("gdpr-cancel-noop")
    ensure_user(uid, "default", uid)
    result = cancel_deletion(uid)
    assert result is not None
    assert result["gdpr_delete_scheduled_for"] is None


# ──────────────────────────────────────────────────────────────────────
# hard_delete_user
# ──────────────────────────────────────────────────────────────────────


def test_hard_delete_user_wipes_messages_and_sessions(client) -> None:
    from app.core.database import get_db_connection
    from app.services.gdpr_service import hard_delete_user
    from app.services.history_service import HistoryService

    uid = _uniq("gdpr-wipe")
    ensure_user(uid, "default", uid)
    session_id = _uniq("gdpr-wipe-sess")
    with get_db_connection() as conn:
        conn.execute(
            "INSERT INTO sessions (id, tenant_id, project_id, user_id) "
            "VALUES (%s, 'default', 'gdpr-test', %s) ON CONFLICT (id) DO NOTHING",
            (session_id, uid),
        )
        conn.commit()
    HistoryService().save_message(session_id, "user", "hello", "default")
    HistoryService().save_message(session_id, "assistant", "hi back", "default")

    summary = hard_delete_user(uid)
    assert summary["users_deleted"] == 1
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM messages WHERE session_id = %s", (session_id,))
            n = cur.fetchone()["n"]
        conn.commit()
    assert n == 0


def test_hard_delete_user_unknown_user_returns_zero(client) -> None:
    from app.services.gdpr_service import hard_delete_user
    summary = hard_delete_user("u_does-not-exist")
    assert summary.get("users_deleted", 0) == 0


# ──────────────────────────────────────────────────────────────────────
# data_export_user
# ──────────────────────────────────────────────────────────────────────


def test_data_export_user_returns_full_dict(client) -> None:
    from app.services.gdpr_service import data_export_user
    uid = _uniq("gdpr-export")
    ensure_user(uid, "default", uid)
    data = data_export_user(uid)
    assert data is not None
    assert "user" in data
    assert data["user"]["id"] == uid
    assert "sessions" in data
    assert "messages" in data
    assert "memory_items" in data
    assert "summaries" in data


def test_data_export_user_unknown_user_returns_none(client) -> None:
    from app.services.gdpr_service import data_export_user
    assert data_export_user("u_does-not-exist") is None


# ──────────────────────────────────────────────────────────────────────
# Admin endpoints
# ──────────────────────────────────────────────────────────────────────


def test_admin_data_export_endpoint(client) -> None:
    uid = _uniq("gdpr-admin-export")
    ensure_user(uid, "default", uid)
    resp = client.get(f"/v1/admin/users/{uid}/data-export")
    assert resp.status_code == 200
    data = resp.json()
    assert data["user"]["id"] == uid


def test_admin_gdpr_delete_endpoint(client) -> None:
    uid = _uniq("gdpr-admin-del")
    ensure_user(uid, "default", uid)
    resp = client.post(f"/v1/admin/users/{uid}/gdpr-delete?grace_days=7")
    assert resp.status_code == 200
    data = resp.json()
    assert data["is_pending_deletion"] is True


def test_admin_force_delete_requires_confirm(client) -> None:
    uid = _uniq("gdpr-force")
    ensure_user(uid, "default", uid)
    resp = client.delete(f"/v1/admin/users/{uid}")
    assert resp.status_code == 400
    resp = client.delete(f"/v1/admin/users/{uid}?confirm=true")
    assert resp.status_code == 200
    assert resp.json()["deleted"] is True


def test_admin_gdpr_status_endpoint(client) -> None:
    uid = _uniq("gdpr-status")
    ensure_user(uid, "default", uid)
    resp = client.get(f"/v1/admin/users/{uid}/gdpr-status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["user_id"] == uid
    assert data["is_pending_deletion"] is False


# ──────────────────────────────────────────────────────────────────────
# Scheduler job
# ──────────────────────────────────────────────────────────────────────


def test_gdpr_sweep_job_runs_without_error(client) -> None:
    import asyncio
    from app.main import _gdpr_hard_delete_sweep
    asyncio.run(_gdpr_hard_delete_sweep())
