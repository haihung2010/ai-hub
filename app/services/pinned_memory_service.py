"""Deterministic pinned memories for facts the user explicitly asks AI Hub to remember."""

from __future__ import annotations

from dataclasses import dataclass
import re
import uuid

from app.core.database import DEFAULT_TENANT_ID, get_db_connection

REMEMBER_PATTERNS = (
    "hãy nhớ",
    "hay nho",
    "ghi nhớ",
    "ghi nho",
    "nhớ rằng",
    "nho rang",
    "đừng quên",
    "dung quen",
    "remember that",
)


@dataclass(frozen=True)
class PinnedMemoryRecord:
    id: str
    tenant_id: str
    project_id: str
    user_id: str
    scope: str
    key: str
    value: str
    confidence: float
    is_active: bool
    updated_at: str


class PinnedMemoryService:
    def upsert_memory(
        self,
        tenant_id: str,
        project_id: str,
        user_id: str,
        key: str,
        value: str,
        *,
        scope: str = "user",
        confidence: float = 1.0,
        source_session_id: str | None = None,
        source_message_id: int | None = None,
    ) -> PinnedMemoryRecord:
        clean_key = self._clean_key(key)
        clean_value = value.strip()
        with get_db_connection() as conn:
            existing = conn.execute(
                """
                SELECT id FROM pinned_memories
                WHERE tenant_id = %s AND project_id = %s AND user_id = %s AND key = %s
                """,
                (tenant_id, project_id, user_id, clean_key),
            ).fetchone()
            memory_id = existing["id"] if existing else str(uuid.uuid4())
            if existing:
                conn.execute(
                    """
                    UPDATE pinned_memories
                    SET value = %s, scope = %s, confidence = %s, source_session_id = %s,
                        source_message_id = %s, is_active = 1, updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                    """,
                    (clean_value, scope, confidence, source_session_id, source_message_id, memory_id),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO pinned_memories (
                        id, tenant_id, project_id, user_id, scope, key, value,
                        source_session_id, source_message_id, confidence, is_active
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 1)
                    """,
                    (
                        memory_id,
                        tenant_id,
                        project_id,
                        user_id,
                        scope,
                        clean_key,
                        clean_value,
                        source_session_id,
                        source_message_id,
                        confidence,
                    ),
                )
            conn.commit()
        return self.get_memory(memory_id)

    def get_memory(self, memory_id: str) -> PinnedMemoryRecord:
        with get_db_connection() as conn:
            row = conn.execute(
                """
                SELECT id, tenant_id, project_id, user_id, scope, key, value,
                       confidence, is_active, updated_at
                FROM pinned_memories WHERE id = %s
                """,
                (memory_id,),
            ).fetchone()
        if row is None:
            raise KeyError(memory_id)
        return self._row_to_record(row)

    def list_memories(
        self,
        tenant_id: str,
        project_id: str,
        user_id: str,
        *,
        active_only: bool = True,
        limit: int = 20,
    ) -> list[PinnedMemoryRecord]:
        query = """
            SELECT id, tenant_id, project_id, user_id, scope, key, value,
                   confidence, is_active, updated_at
            FROM pinned_memories
            WHERE tenant_id = %s AND project_id = %s AND user_id = %s
        """
        params: list[object] = [tenant_id, project_id, user_id]
        if active_only:
            query += " AND is_active = 1"
        query += " ORDER BY updated_at DESC, created_at DESC LIMIT %s"
        params.append(limit)
        with get_db_connection() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [self._row_to_record(row) for row in rows]

    def deactivate_memory(self, memory_id: str) -> None:
        with get_db_connection() as conn:
            conn.execute(
                "UPDATE pinned_memories SET is_active = 0, updated_at = CURRENT_TIMESTAMP WHERE id = %s",
                (memory_id,),
            )
            conn.commit()

    def format_for_prompt(self, tenant_id: str, project_id: str, user_id: str) -> str:
        memories = self.list_memories(tenant_id, project_id, user_id, active_only=True)
        if not memories:
            return ""
        lines = ["### SYSTEM: PINNED MEMORY ###"]
        for memory in memories:
            lines.append(f"- {memory.value}")
        return "\n".join(lines)

    def maybe_extract_remember_value(self, text: str) -> str | None:
        lowered = text.strip().lower()
        for pattern in REMEMBER_PATTERNS:
            idx = lowered.find(pattern)
            if idx >= 0:
                value = text[idx + len(pattern) :].strip(" :：,-—.\n\t")
                return value or text.strip()
        return None

    def remember_from_message(
        self,
        tenant_id: str,
        project_id: str,
        user_id: str,
        text: str,
        *,
        session_id: str | None = None,
    ) -> PinnedMemoryRecord | None:
        value = self.maybe_extract_remember_value(text)
        if not value:
            return None
        key = self._derive_key(value)
        return self.upsert_memory(
            tenant_id,
            project_id,
            user_id,
            key,
            value,
            source_session_id=session_id,
        )

    @staticmethod
    def _derive_key(value: str) -> str:
        words = re.findall(r"[\wÀ-ỹ]+", value.lower())[:8]
        return " ".join(words) if words else "memory"

    @staticmethod
    def _clean_key(key: str) -> str:
        return " ".join(key.strip().lower().split())[:160] or "memory"

    @staticmethod
    def _row_to_record(row) -> PinnedMemoryRecord:
        return PinnedMemoryRecord(
            id=row["id"],
            tenant_id=row["tenant_id"],
            project_id=row["project_id"],
            user_id=row["user_id"],
            scope=row["scope"],
            key=row["key"],
            value=row["value"],
            confidence=float(row["confidence"]),
            is_active=bool(row["is_active"]),
            updated_at=str(row["updated_at"]),
        )
