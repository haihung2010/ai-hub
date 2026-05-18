import logging
import uuid

from app.core.database import DEFAULT_TENANT_ID, get_db_connection
from app.models.chat import Message

logger = logging.getLogger(__name__)


class HistoryService:
    def create_session(
        self,
        project_id: str,
        user_id: str | None = None,
        tenant_id: str = DEFAULT_TENANT_ID,
        session_id: str | None = None,
    ) -> str:
        sid = session_id or str(uuid.uuid4())
        with get_db_connection() as conn:
            conn.execute(
                "INSERT INTO sessions (id, tenant_id, project_id, user_id) VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (id) DO NOTHING",
                (sid, tenant_id, project_id, user_id),
            )
            conn.commit()
        return sid

    def session_belongs_to(
        self,
        session_id: str,
        project_id: str,
        tenant_id: str = DEFAULT_TENANT_ID,
        user_id: str | None = None,
    ) -> bool:
        query = "SELECT id FROM sessions WHERE id = %s AND tenant_id = %s AND project_id = %s"
        params: list[str] = [session_id, tenant_id, project_id]
        if user_id is not None:
            query += " AND user_id = %s"
            params.append(user_id)
        with get_db_connection() as conn:
            row = conn.execute(query, tuple(params)).fetchone()
        return row is not None

    def save_message(
        self,
        session_id: str,
        role: str,
        content: str,
        tenant_id: str = DEFAULT_TENANT_ID,
        user_id: str | None = None,
        is_summarized: bool = False,
    ) -> None:
        with get_db_connection() as conn:
            conn.execute(
                "INSERT INTO messages (tenant_id, session_id, role, content, user_id, is_summarized) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (tenant_id, session_id, role, content, user_id, int(is_summarized)),
            )
            conn.commit()

    def get_session_messages(
        self,
        session_id: str,
        tenant_id: str = DEFAULT_TENANT_ID,
        limit: int = 20,
        only_unsummarized: bool = False,
    ) -> list[Message]:
        query = (
            "SELECT role, content FROM messages "
            "WHERE tenant_id = %s AND session_id = %s"
        )
        params: list[str | int] = [tenant_id, session_id]
        if only_unsummarized:
            query += " AND is_summarized = 0"
        query += " ORDER BY id DESC"
        if limit > 0:
            query += " LIMIT %s"
            params.append(limit)

        with get_db_connection() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [Message(role=row["role"], content=row["content"] or "...") for row in reversed(rows)]

    def get_recent_messages_for_user(
        self,
        user_id: str,
        project_id: str,
        tenant_id: str = DEFAULT_TENANT_ID,
        limit: int = 20,
    ) -> list[Message]:
        """Cross-session history: latest N messages for a user in a project,
        bounded by the user's memory boundary if one is set."""
        query = (
            "SELECT m.role, m.content FROM messages m "
            "JOIN sessions s ON m.session_id = s.id "
            "LEFT JOIN memory_boundaries b ON b.tenant_id = m.tenant_id "
            "  AND b.user_id = m.user_id AND b.project_id = s.project_id "
            "WHERE m.tenant_id = %s AND s.tenant_id = %s "
            "AND m.user_id = %s AND s.project_id = %s "
            "AND (b.boundary_at IS NULL OR m.created_at >= b.boundary_at) "
            "ORDER BY m.id DESC"
        )
        params: list[str | int] = [tenant_id, tenant_id, user_id, project_id]
        if limit > 0:
            query += " LIMIT %s"
            params.append(limit)
        with get_db_connection() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [Message(role=row["role"], content=row["content"] or "...") for row in reversed(rows)]

    def mark_memory_boundary(
        self,
        user_id: str,
        project_id: str,
        tenant_id: str = DEFAULT_TENANT_ID,
    ) -> None:
        """Set the memory boundary for a user+project to NOW(). Existing data is preserved;
        future memory loads will only include rows after this timestamp."""
        with get_db_connection() as conn:
            conn.execute(
                "INSERT INTO memory_boundaries (tenant_id, user_id, project_id, boundary_at) "
                "VALUES (%s, %s, %s, CURRENT_TIMESTAMP) "
                "ON CONFLICT (tenant_id, user_id, project_id) "
                "DO UPDATE SET boundary_at = CURRENT_TIMESTAMP",
                (tenant_id, user_id, project_id),
            )
            conn.commit()

    def get_unsummarized_messages(
        self,
        user_id: str,
        project_id: str,
        tenant_id: str = DEFAULT_TENANT_ID,
    ) -> list[tuple[int, Message]]:
        query = (
            "SELECT m.id, m.role, m.content FROM messages m "
            "JOIN sessions s ON m.session_id = s.id "
            "WHERE m.tenant_id = %s AND s.tenant_id = %s AND m.user_id = %s "
            "AND s.project_id = %s AND m.is_summarized = 0 "
            "ORDER BY m.id ASC"
        )
        with get_db_connection() as conn:
            rows = conn.execute(query, (tenant_id, tenant_id, user_id, project_id)).fetchall()
        return [(row["id"], Message(role=row["role"], content=row["content"] or "...")) for row in rows]

    def count_unsummarized_messages(
        self,
        user_id: str,
        project_id: str,
        tenant_id: str = DEFAULT_TENANT_ID,
    ) -> int:
        query = (
            "SELECT COUNT(*) as cnt FROM messages m "
            "JOIN sessions s ON m.session_id = s.id "
            "WHERE m.tenant_id = %s AND s.tenant_id = %s AND m.user_id = %s "
            "AND s.project_id = %s AND m.is_summarized = 0"
        )
        with get_db_connection() as conn:
            row = conn.execute(query, (tenant_id, tenant_id, user_id, project_id)).fetchone()
        return row["cnt"] if row else 0

    def clear_user_history(
        self,
        user_id: str,
        project_id: str,
        tenant_id: str = DEFAULT_TENANT_ID,
    ) -> int:
        """Delete all messages, sessions, and summaries for a user in a project. Returns deleted session count."""
        with get_db_connection() as conn:
            conn.execute(
                "DELETE FROM messages WHERE tenant_id = %s AND session_id IN "
                "(SELECT id FROM sessions WHERE tenant_id = %s AND user_id = %s AND project_id = %s)",
                (tenant_id, tenant_id, user_id, project_id),
            )
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM sessions WHERE tenant_id = %s AND user_id = %s AND project_id = %s",
                (tenant_id, user_id, project_id),
            ).fetchone()
            count = row["cnt"] if row else 0
            conn.execute(
                "DELETE FROM sessions WHERE tenant_id = %s AND user_id = %s AND project_id = %s",
                (tenant_id, user_id, project_id),
            )
            conn.execute(
                "DELETE FROM summaries WHERE tenant_id = %s AND user_id = %s AND project_id = %s",
                (tenant_id, user_id, project_id),
            )
            conn.commit()
        return count

    def mark_messages_summarized(
        self,
        user_id: str,
        project_id: str,
        up_to_id: int,
        tenant_id: str = DEFAULT_TENANT_ID,
    ) -> None:
        query = (
            "UPDATE messages SET is_summarized = 1 "
            "WHERE tenant_id = %s AND user_id = %s AND is_summarized = 0 AND id <= %s "
            "AND session_id IN (SELECT id FROM sessions WHERE tenant_id = %s AND project_id = %s)"
        )
        with get_db_connection() as conn:
            conn.execute(query, (tenant_id, user_id, up_to_id, tenant_id, project_id))
            conn.commit()
