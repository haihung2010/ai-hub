"""Cross-session memory service.

Queries structmem + messages by user_id (not session_id), so a user
returning days later gets context from their previous sessions.

Sync implementation: ai-hub's psycopg3 pool is sync (ConnectionPool,
not AsyncConnectionPool). Matches the rest of the codebase.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class CrossSessionMemory:
    """Reads memory across all sessions for a user.

    Multi-tenant: every query filters by tenant_id.
    """

    def __init__(self, db_pool):
        self.db = db_pool

    def get_recent_messages(
        self, tenant_id: str, user_id: str, limit: int = 20
    ) -> list[dict]:
        """Get recent messages for user across ALL their sessions (not just current)."""
        with self.db.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT session_id, role, content, created_at FROM messages "
                    "WHERE tenant_id = %s AND user_id = %s "
                    "ORDER BY created_at DESC LIMIT %s",
                    (tenant_id, user_id, limit),
                )
                rows = cur.fetchall()
        return [
            {
                "session_id": r["session_id"],
                "role": r["role"],
                "content": r["content"],
                "ts": str(r["created_at"]),
            }
            for r in rows
        ]

    def get_structmem_for_user(
        self, tenant_id: str, user_id: str, limit: int = 50
    ) -> list[dict]:
        """Get structmem items for user across ALL sessions."""
        with self.db.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT memory_type, subject, predicate, object, content, created_at "
                    "FROM memory_items WHERE tenant_id = %s AND user_id = %s "
                    "ORDER BY created_at DESC LIMIT %s",
                    (tenant_id, user_id, limit),
                )
                rows = cur.fetchall()
        return [
            {
                "memory_type": r["memory_type"],
                "subject": r["subject"],
                "predicate": r["predicate"],
                "object": r["object"],
                "content": r["content"],
                "ts": str(r["created_at"]),
            }
            for r in rows
        ]

    @staticmethod
    def format_for_context(items: list[dict], max_items: int = 10) -> str:
        """Render structmem items as a <cross_session_memory> block.

        Returns empty string if no items.
        """
        if not items:
            return ""
        lines = ["<cross_session_memory>"]
        for it in items[:max_items]:
            lines.append(
                f"[{it['memory_type']}] {it['subject']} | {it['predicate']} | {it['object']}"
            )
        lines.append("</cross_session_memory>")
        return "\n".join(lines)
