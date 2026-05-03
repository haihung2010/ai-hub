"""CRUD for users and user→session lookups for the resume-chat flow."""

from __future__ import annotations

import logging
import uuid

from app.core.database import DEFAULT_TENANT_ID, get_db_connection
from app.models.user import SessionRecord, UserRecord

logger = logging.getLogger(__name__)

PREVIEW_MAX_CHARS = 120
SESSIONS_LIMIT = 20


class UserService:
    def get_or_create_user(
        self,
        name: str,
        tenant_id: str = DEFAULT_TENANT_ID,
    ) -> UserRecord:
        with get_db_connection() as conn:
            row = conn.execute(
                "SELECT id, tenant_id, name FROM users WHERE tenant_id = %s AND name = %s",
                (tenant_id, name),
            ).fetchone()
            if row is not None:
                return UserRecord(id=row["id"], tenant_id=row["tenant_id"], name=row["name"])

            new_id = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO users (id, tenant_id, name) VALUES (%s, %s, %s)",
                (new_id, tenant_id, name),
            )
            conn.commit()
            logger.info("Created user tenant=%s name=%s id=%s", tenant_id, name, new_id)
            return UserRecord(id=new_id, tenant_id=tenant_id, name=name)

    def find_by_name(
        self,
        name: str,
        tenant_id: str = DEFAULT_TENANT_ID,
    ) -> UserRecord | None:
        with get_db_connection() as conn:
            row = conn.execute(
                "SELECT id, tenant_id, name FROM users WHERE tenant_id = %s AND name = %s",
                (tenant_id, name),
            ).fetchone()
        if row is None:
            return None
        return UserRecord(id=row["id"], tenant_id=row["tenant_id"], name=row["name"])

    def find_sessions_for_user(
        self,
        user_id: str,
        tenant_id: str = DEFAULT_TENANT_ID,
        project_id: str | None = None,
        limit: int = SESSIONS_LIMIT,
    ) -> list[SessionRecord]:
        query = (
            "SELECT s.id, s.project_id, s.user_id, s.created_at, "
            "(SELECT m.content FROM messages m WHERE m.tenant_id = %s AND m.session_id = s.id "
            "ORDER BY m.id DESC LIMIT 1) AS last_content "
            "FROM sessions s WHERE s.tenant_id = %s AND s.user_id = %s"
        )
        params: list = [tenant_id, tenant_id, user_id]
        if project_id is not None:
            query += " AND s.project_id = %s"
            params.append(project_id)
        query += " ORDER BY s.created_at DESC LIMIT %s"
        params.append(limit)

        with get_db_connection() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()

        result: list[SessionRecord] = []
        for row in rows:
            preview = row["last_content"]
            if preview is not None and len(preview) > PREVIEW_MAX_CHARS:
                preview = preview[:PREVIEW_MAX_CHARS] + "…"
            result.append(
                SessionRecord(
                    id=row["id"],
                    project_id=row["project_id"],
                    user_id=row["user_id"],
                    created_at=str(row["created_at"]),
                    last_message_preview=preview,
                )
            )
        return result
