"""Consolidates memory_items into a compact persistent summary per user/project."""

from __future__ import annotations

import json
import logging
import uuid

from app.core.database import DEFAULT_TENANT_ID, get_db_connection
from app.models.chat import Message
from app.models.memory import MemoryItemRecord

logger = logging.getLogger(__name__)

CONSOLIDATION_PROMPT = """You synthesize structured long-term memories into a compact persistent summary.
Given these memory items, write 3-10 bullet points capturing the most durable facts.
Focus on: user preferences, recurring patterns, key entities, and established facts.
Ignore transient, trivial, or redundant entries.
Return plain-text bullet points only — no JSON, no headers.
"""

_MEMORY_TYPES = ("procedural", "semantic", "relational", "episodic")


class MemoryConsolidationService:
    def _fetch_items(self, user_id: str, tenant_id: str, project_id: str) -> list[MemoryItemRecord]:
        with get_db_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM memory_items WHERE user_id = %s AND tenant_id = %s AND project_id = %s",
                (user_id, tenant_id, project_id),
            ).fetchall()
        return [self._map(row) for row in rows]

    def _fetch_episode_ids(self, user_id: str, tenant_id: str, project_id: str) -> list[str]:
        with get_db_connection() as conn:
            rows = conn.execute(
                "SELECT id FROM memory_episodes WHERE user_id = %s AND tenant_id = %s AND project_id = %s ORDER BY created_at",
                (user_id, tenant_id, project_id),
            ).fetchall()
        return [row["id"] for row in rows]

    @staticmethod
    def _map(row) -> MemoryItemRecord:
        return MemoryItemRecord(
            id=row["id"],
            episode_id=row["episode_id"],
            user_id=row["user_id"],
            tenant_id=row["tenant_id"],
            project_id=row["project_id"],
            memory_type=row["memory_type"],
            subject=row["subject"],
            predicate=row["predicate"],
            object=row["object"],
            content=row["content"],
            salience=float(row["salience"]),
            valid_from=row["valid_from"],
            valid_to=row["valid_to"],
            last_accessed_at=row["last_accessed_at"],
            created_at=row["created_at"],
        )

    def _build_prompt(self, items: list[MemoryItemRecord]) -> list[Message]:
        lines: list[str] = []
        for mtype in _MEMORY_TYPES:
            typed = [i for i in items if i.memory_type == mtype]
            if not typed:
                continue
            lines.append(f"## {mtype.capitalize()} memories")
            for item in typed:
                lines.append(f"- {item.content}")
        return [
            Message(role="system", content=CONSOLIDATION_PROMPT),
            Message(role="user", content="\n".join(lines)),
        ]

    def _upsert(
        self,
        *,
        user_id: str,
        tenant_id: str,
        project_id: str,
        scope_key: str,
        source_episode_ids: list[str],
        content: str,
    ) -> str:
        with get_db_connection() as conn:
            existing = conn.execute(
                "SELECT id, version FROM memory_consolidations WHERE user_id = %s AND tenant_id = %s AND project_id = %s AND scope_key = %s",
                (user_id, tenant_id, project_id, scope_key),
            ).fetchone()

            if existing:
                new_version = existing["version"] + 1
                conn.execute(
                    "UPDATE memory_consolidations SET source_episode_ids = %s, content = %s, version = %s, updated_at = CURRENT_TIMESTAMP WHERE id = %s",
                    (json.dumps(source_episode_ids), content, new_version, existing["id"]),
                )
                conn.commit()
                return existing["id"]

            record_id = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO memory_consolidations (id, user_id, tenant_id, project_id, scope_key, source_episode_ids, content, version) VALUES (%s, %s, %s, %s, %s, %s, %s, 1)",
                (record_id, user_id, tenant_id, project_id, scope_key, json.dumps(source_episode_ids), content),
            )
            conn.commit()
            return record_id

    async def consolidate(
        self,
        *,
        user_id: str,
        tenant_id: str = DEFAULT_TENANT_ID,
        project_id: str,
        provider,
        model: str,
        min_items: int = 5,
    ) -> str | None:
        items = self._fetch_items(user_id, tenant_id, project_id)
        if len(items) < min_items:
            return None

        prompt_messages = self._build_prompt(items)
        try:
            content = await provider.complete(prompt_messages, model, 0.3)
        except Exception:
            logger.exception("Consolidation LLM call failed user=%s project=%s", user_id, project_id)
            return None

        content = content.strip()
        if not content:
            return None

        episode_ids = self._fetch_episode_ids(user_id, tenant_id, project_id)
        scope_key = f"{tenant_id}:{project_id}"
        record_id = self._upsert(
            user_id=user_id,
            tenant_id=tenant_id,
            project_id=project_id,
            scope_key=scope_key,
            source_episode_ids=episode_ids,
            content=content,
        )
        logger.info(
            "Consolidated memory user=%s project=%s items=%d record=%s",
            user_id,
            project_id,
            len(items),
            record_id,
        )
        return record_id
