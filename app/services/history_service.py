import logging
import uuid

from app.core.database import DEFAULT_TENANT_ID, get_db_connection
from app.models.chat import Message

logger = logging.getLogger(__name__)


class HistoryService:
    def create_session(self, project_id: str, user_id: str | None = None) -> str:
        session_id = str(uuid.uuid4())
        with get_db_connection() as conn:
            conn.execute(
                "INSERT INTO sessions (id, project_id, user_id) VALUES (?, ?, ?)",
                (session_id, project_id, user_id),
            )
            conn.commit()
        return session_id

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
                "VALUES (?, ?, ?, ?, ?, ?)",
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
            "WHERE tenant_id = ? AND session_id = ?"
        )
        params: list[str | int] = [tenant_id, session_id]
        if only_unsummarized:
            query += " AND is_summarized = 0"
        query += " ORDER BY id ASC"
        if limit > 0:
            query += " LIMIT ?"
            params.append(limit)

        with get_db_connection() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [Message(role=row["role"], content=row["content"] or "...") for row in rows]

    def get_unsummarized_messages(
        self,
        user_id: str,
        project_id: str,
    ) -> list[tuple[int, Message]]:
        query = (
            "SELECT m.id, m.role, m.content FROM messages m "
            "JOIN sessions s ON m.session_id = s.id "
            "WHERE m.user_id = ? AND s.project_id = ? AND m.is_summarized = 0 "
            "ORDER BY m.id ASC"
        )
        with get_db_connection() as conn:
            rows = conn.execute(query, (user_id, project_id)).fetchall()
        return [(row["id"], Message(role=row["role"], content=row["content"] or "...")) for row in rows]

    def count_unsummarized_messages(
        self,
        user_id: str,
        project_id: str,
    ) -> int:
        query = (
            "SELECT COUNT(*) as cnt FROM messages m "
            "JOIN sessions s ON m.session_id = s.id "
            "WHERE m.user_id = ? AND s.project_id = ? AND m.is_summarized = 0"
        )
        with get_db_connection() as conn:
            row = conn.execute(query, (user_id, project_id)).fetchone()
        return row["cnt"] if row else 0

    def mark_messages_summarized(
        self,
        user_id: str,
        project_id: str,
        up_to_id: int,
    ) -> None:
        query = (
            "UPDATE messages SET is_summarized = 1 "
            "WHERE user_id = ? AND is_summarized = 0 AND id <= ? "
            "AND session_id IN (SELECT id FROM sessions WHERE project_id = ?)"
        )
        with get_db_connection() as conn:
            conn.execute(query, (user_id, up_to_id, project_id))
            conn.commit()
