"""Verbatim memory service.

Returns recent raw messages for a user from the messages table.
Used to give the LLM direct access to recent conversation history
without relying on summary/structmem extraction.
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


class VerbatimMemory:
    """Get recent raw messages for a user.

    The ``db_pool`` may be either a synchronous ``psycopg_pool.ConnectionPool``
    (ai-hub's default) or any object exposing ``.connection()`` that returns a
    context manager. We use the sync API to match the rest of the codebase
    (``history_service.py`` uses ``with get_db_connection() as conn:``).
    """

    def __init__(self, db_pool, max_messages: int = 20):
        self.db = db_pool
        self.max_messages = max_messages

    def get_recent(
        self, user_id: str, session_id: str | None = None, limit: int | None = None
    ) -> list[dict]:
        """Return up to ``limit`` recent messages for the user, newest first.

        If ``session_id`` is provided, filter by that session.
        """
        actual_limit = limit or self.max_messages
        with self.db.connection() as conn:
            with conn.cursor() as cur:
                if session_id:
                    cur.execute(
                        "SELECT role, content, created_at FROM messages "
                        "WHERE user_id = %s AND session_id = %s "
                        "ORDER BY created_at DESC LIMIT %s",
                        (user_id, session_id, actual_limit),
                    )
                else:
                    cur.execute(
                        "SELECT role, content, created_at FROM messages "
                        "WHERE user_id = %s "
                        "ORDER BY created_at DESC LIMIT %s",
                        (user_id, actual_limit),
                    )
                rows = cur.fetchall()
        # Pool uses row_factory=dict_row, so rows are dicts not tuples
        return [{"role": r["role"], "content": r["content"], "ts": str(r["created_at"])} for r in rows]

    @staticmethod
    def format_for_context(messages: list[dict], max_chars_per_msg: int = 200) -> str:
        """Render messages as a <verbatim_history> block for system prompt.

        Returns empty string if no messages.
        """
        if not messages:
            return ""
        lines = ["<verbatim_history>"]
        for m in reversed(messages):  # chronological order (oldest first)
            content = m["content"][:max_chars_per_msg]
            lines.append(f"[{m['ts']}] {m['role']}: {content}")
        lines.append("</verbatim_history>")
        return "\n".join(lines)
