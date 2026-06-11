"""GDPR data export + erasure service (P2.6, 2026-06-10).

Implements the rights from GDPR Articles 15 (right of access) and
17 (right to erasure), and the analogous rights under Vietnamese
PDPA (Nghị định 13/2023/NĐ-CP).

The flow:
  1. User (or admin) requests deletion → soft-delete scheduled for
     +30 days (configurable). The user can cancel any time before
     the deadline.
  2. Each day, the scheduler picks up rows where
     gdpr_delete_scheduled_for < NOW() AND gdpr_deleted_at IS NULL
     and runs hard_delete_user().
  3. hard_delete_user() DELETEs from 12 user-scoped tables in
     order, then DELETEs the user row. usage_events and
     failure_risk_events keep api_key_id for billing but the
     user_id is NULLed out (GDPR Art. 17(3)(e) — "establishment,
     exercise or defence of legal claims" lets us keep billing
     records with the api_key still linked).
  4. data_export_user() returns a JSON dict with every row that
     references user_id, so the user can see what we'd erase.

Reference: docs/security/gdpr.md (operator runbook).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.database import get_db_connection

logger = logging.getLogger(__name__)


# Tables to wipe during hard_delete_user(). Order matters because
# the FKs are NOT all CASCADE. We delete child rows first, then
# the user row last. If a future schema change adds new user-scoped
# tables, add them here.
_USER_SCOPED_TABLES_FOR_HARD_DELETE: list[tuple[str, str]] = [
    # (table, column) — table has a user_id column we wipe by value
    ("memory_boundaries", "user_id"),       # CASCADE in schema, but explicit is safer
    ("memory_episodes", "user_id"),
    ("memory_items", "user_id"),
    ("memory_consolidations", "user_id"),
    ("pinned_memories", "user_id"),
    ("summaries", "user_id"),
    ("prediction_records", "user_id"),
    ("fanpage_facts", "user_id"),
    ("usage_events", "user_id"),            # see special note in hard_delete_user
    ("failure_risk_events", "user_id"),
    ("messages", "session_id"),             # via session_id (we resolve sessions first)
    ("sessions", "user_id"),
]


DEFAULT_GRACE_DAYS = 30


def _now() -> datetime:
    return datetime.now(timezone.utc)


def get_user_gdpr_status(user_id: str) -> dict[str, Any] | None:
    """Return the GDPR state of a user, or None if the user doesn't exist."""
    with get_db_connection() as conn:
        row = conn.execute(
            "SELECT id, name, gdpr_delete_requested_at, gdpr_delete_scheduled_for, gdpr_deleted_at "
            "FROM users WHERE id = %s",
            (user_id,),
        ).fetchone()
    if row is None:
        return None
    return {
        "user_id": row["id"],
        "name": row["name"],
        "gdpr_delete_requested_at": row["gdpr_delete_requested_at"].isoformat() if row["gdpr_delete_requested_at"] else None,
        "gdpr_delete_scheduled_for": row["gdpr_delete_scheduled_for"].isoformat() if row["gdpr_delete_scheduled_for"] else None,
        "gdpr_deleted_at": row["gdpr_deleted_at"].isoformat() if row["gdpr_deleted_at"] else None,
        "is_pending_deletion": row["gdpr_delete_scheduled_for"] is not None and row["gdpr_deleted_at"] is None,
        "is_deleted": row["gdpr_deleted_at"] is not None,
    }


def request_deletion(
    user_id: str, grace_days: int = DEFAULT_GRACE_DAYS
) -> dict[str, Any] | None:
    """Schedule a hard-delete for the user in `grace_days` days.

    Idempotent: calling it twice does not extend the deadline —
    the FIRST request wins. The user can call cancel_deletion() any
    time before the deadline.
    """
    with get_db_connection() as conn:
        row = conn.execute(
            "SELECT id, gdpr_delete_scheduled_for FROM users WHERE id = %s",
            (user_id,),
        ).fetchone()
        if row is None:
            return None
        if row["gdpr_delete_scheduled_for"] is not None:
            # Already scheduled — return current state
            return get_user_gdpr_status(user_id)
        scheduled = _now() + timedelta(days=grace_days)
        conn.execute(
            "UPDATE users SET gdpr_delete_requested_at = COALESCE(gdpr_delete_requested_at, %s), "
            "gdpr_delete_scheduled_for = %s WHERE id = %s",
            (_now(), scheduled, user_id),
        )
        conn.commit()
    return get_user_gdpr_status(user_id)


def cancel_deletion(user_id: str) -> dict[str, Any] | None:
    """Cancel a pending deletion. No-op if no deletion is pending."""
    with get_db_connection() as conn:
        conn.execute(
            "UPDATE users SET gdpr_delete_scheduled_for = NULL "
            "WHERE id = %s AND gdpr_delete_scheduled_for IS NOT NULL AND gdpr_deleted_at IS NULL",
            (user_id,),
        )
        conn.commit()
    return get_user_gdpr_status(user_id)


def list_pending_deletions(limit: int = 100) -> list[dict[str, Any]]:
    """List users whose gdpr_delete_scheduled_for is in the past
    OR in the next 7 days. Used by the scheduler to pick up work."""
    with get_db_connection() as conn:
        rows = conn.execute(
            "SELECT id, name, gdpr_delete_requested_at, gdpr_delete_scheduled_for "
            "FROM users "
            "WHERE gdpr_delete_scheduled_for IS NOT NULL "
            "  AND gdpr_deleted_at IS NULL "
            "  AND gdpr_delete_scheduled_for < (NOW() + INTERVAL '7 days') "
            "ORDER BY gdpr_delete_scheduled_for ASC "
            "LIMIT %s",
            (int(limit),),
        ).fetchall()
    return [
        {
            "user_id": r["id"],
            "name": r["name"],
            "gdpr_delete_requested_at": r["gdpr_delete_requested_at"].isoformat() if r["gdpr_delete_requested_at"] else None,
            "gdpr_delete_scheduled_for": r["gdpr_delete_scheduled_for"].isoformat() if r["gdpr_delete_scheduled_for"] else None,
        }
        for r in rows
    ]


def hard_delete_user(user_id: str) -> dict[str, Any]:
    """Permanently delete a user and all their data.

    This is IRREVERSIBLE. Caller must confirm the soft-delete
    grace period has passed (or admin is forcing it).

    Returns a summary of how many rows were deleted per table.
    """
    summary: dict[str, int] = {}
    with get_db_connection() as conn:
        # 1. Resolve sessions for this user so we can wipe their messages
        sess_rows = conn.execute(
            "SELECT id FROM sessions WHERE user_id = %s", (user_id,)
        ).fetchall()
        session_ids = [r["id"] for r in sess_rows]
        summary["sessions"] = len(session_ids)

        # 2. Wipe messages by session
        if session_ids:
            cur = conn.execute(
                "DELETE FROM messages WHERE session_id = ANY(%s)",
                (session_ids,),
            )
            summary["messages"] = cur.rowcount

        # 3. Wipe everything else by user_id
        for table, column in _USER_SCOPED_TABLES_FOR_HARD_DELETE:
            if table == "sessions":
                # already counted above
                cur = conn.execute(
                    f"DELETE FROM sessions WHERE user_id = %s", (user_id,)
                )
                summary["sessions_deleted"] = cur.rowcount
                continue
            if table == "messages":
                continue  # handled above
            try:
                cur = conn.execute(
                    f"DELETE FROM {table} WHERE {column} = %s", (user_id,)
                )
                summary[table] = cur.rowcount
            except Exception as exc:
                logger.warning(
                    "hard_delete_user: %s.%s delete failed: %s", table, column, exc
                )
                summary[f"{table}_error"] = str(exc)

        # 4. Null out user_id on usage_events and failure_risk_events
        # (we want to keep api_key_id for billing, but erase PII)
        for table in ("usage_events", "failure_risk_events"):
            try:
                cur = conn.execute(
                    f"UPDATE {table} SET user_id = NULL WHERE user_id = %s",
                    (user_id,),
                )
                summary[f"{table}_nulled"] = cur.rowcount
            except Exception as exc:
                logger.warning(
                    "hard_delete_user: %s user_id null-out failed: %s", table, exc
                )

        # 5. Disable any api_keys owned by this user (do NOT delete,
        # because usage_events.api_key_id still references them and
        # we want the billing history to remain intact).
        cur = conn.execute(
            "UPDATE api_keys SET enabled = 0 WHERE owner_user_id = %s", (user_id,)
        )
        summary["api_keys_disabled"] = cur.rowcount

        # 6. Finally: delete the user row itself
        cur = conn.execute("DELETE FROM users WHERE id = %s", (user_id,))
        summary["users_deleted"] = cur.rowcount
        conn.commit()
    logger.info("hard_delete_user: user_id=%s summary=%s", user_id, summary)
    return summary


def data_export_user(user_id: str) -> dict[str, Any] | None:
    """Return all data associated with ``user_id`` as a JSON dict.

    Implements GDPR Art. 15 (right of access). The shape is:
      {
        "user": {...},
        "sessions": [...],
        "messages": [...],
        "memory_items": [...],
        "summaries": [...],
        "pinned_memories": [...],
        "knowledge_cards": [],   # tenant-scoped, not user-scoped
        "usage_events": [...],
        "prediction_records": [...],
        ...
      }
    For users with thousands of messages this can be large; the
    caller may want to stream or paginate.
    """
    with get_db_connection() as conn:
        user = conn.execute(
            "SELECT id, tenant_id, name, created_at, gdpr_delete_requested_at, "
            "gdpr_delete_scheduled_for, gdpr_deleted_at FROM users WHERE id = %s",
            (user_id,),
        ).fetchone()
        if user is None:
            return None

        result: dict[str, Any] = {
            "user": {
                "id": user["id"],
                "tenant_id": user["tenant_id"],
                "name": user["name"],
                "created_at": user["created_at"].isoformat() if user["created_at"] else None,
                "gdpr_delete_requested_at": user["gdpr_delete_requested_at"].isoformat() if user["gdpr_delete_requested_at"] else None,
                "gdpr_delete_scheduled_for": user["gdpr_delete_scheduled_for"].isoformat() if user["gdpr_delete_scheduled_for"] else None,
                "gdpr_deleted_at": user["gdpr_deleted_at"].isoformat() if user["gdpr_deleted_at"] else None,
            },
        }

        # Per-table SELECT
        queries = [
            ("sessions", "SELECT * FROM sessions WHERE user_id = %s"),
            ("memory_items", "SELECT * FROM memory_items WHERE user_id = %s"),
            ("summaries", "SELECT * FROM summaries WHERE user_id = %s"),
            ("pinned_memories", "SELECT * FROM pinned_memories WHERE user_id = %s"),
            ("prediction_records", "SELECT * FROM prediction_records WHERE user_id = %s"),
            ("usage_events", "SELECT id, tenant_id, api_key_id, project_id, session_id, provider, model, route_alias, prompt_tokens, completion_tokens, total_tokens, cost_usd, latency_ms, status_code, error_type, fallback_used, queue_wait_ms, route_reason, created_at FROM usage_events WHERE user_id = %s"),
            ("failure_risk_events", "SELECT id, tenant_id, project_id, session_id, risk_level, risk_score, risk_types_json, reasons_json, recommended_action, applied_action, action_applied, created_at FROM failure_risk_events WHERE user_id = %s"),
        ]
        for key, sql in queries:
            rows = conn.execute(sql, (user_id,)).fetchall()
            # Serialize datetimes to ISO
            result[key] = [_serialize_row(dict(r)) for r in rows]

        # Messages via session_ids
        sess_ids = [r["id"] for r in result["sessions"]]
        if sess_ids:
            rows = conn.execute(
                "SELECT id, tenant_id, session_id, role, content, user_id, is_summarized, created_at "
                "FROM messages WHERE session_id = ANY(%s) ORDER BY created_at",
                (sess_ids,),
            ).fetchall()
            result["messages"] = [_serialize_row(dict(r)) for r in rows]
        else:
            result["messages"] = []
    return result


def _serialize_row(row: dict) -> dict:
    """Convert datetimes to ISO strings for JSON output."""
    out = {}
    for k, v in row.items():
        if isinstance(v, datetime):
            out[k] = v.isoformat()
        else:
            out[k] = v
    return out
