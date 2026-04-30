from __future__ import annotations

import logging
import re
from collections.abc import Iterable

from app.core.database import get_db_connection
from app.models.memory import (
    MemoryConsolidationRecord,
    MemoryItemRecord,
    RetrievedMemoryBundle,
)

logger = logging.getLogger(__name__)

_WORD_RE = re.compile(r"\w+")


class MemoryRetrievalService:
    def _tokenize(self, text: str) -> set[str]:
        return {token.lower() for token in _WORD_RE.findall(text)}

    def _score_item(self, query_tokens: set[str], row_tokens: Iterable[str], salience: float) -> float:
        haystack = {token.lower() for token in row_tokens if token}
        overlap = len(query_tokens & haystack)
        return overlap + salience

    def _map_item(self, row) -> MemoryItemRecord:
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

    def _map_consolidation(self, row) -> MemoryConsolidationRecord:
        return MemoryConsolidationRecord(
            id=row["id"],
            user_id=row["user_id"],
            tenant_id=row["tenant_id"],
            project_id=row["project_id"],
            scope_key=row["scope_key"],
            source_episode_ids=row["source_episode_ids"],
            content=row["content"],
            version=row["version"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _fetch_items(self, user_id: str, tenant_id: str, project_id: str) -> list[MemoryItemRecord]:
        with get_db_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM memory_items WHERE user_id = ? AND tenant_id = ? AND project_id = ?",
                (user_id, tenant_id, project_id),
            ).fetchall()
        return [self._map_item(row) for row in rows]

    def _fetch_consolidations(self, user_id: str, tenant_id: str, project_id: str) -> list[MemoryConsolidationRecord]:
        with get_db_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM memory_consolidations WHERE user_id = ? AND tenant_id = ? AND project_id = ? ORDER BY updated_at DESC",
                (user_id, tenant_id, project_id),
            ).fetchall()
        return [self._map_consolidation(row) for row in rows]

    def _touch_items(self, item_ids: list[str]) -> None:
        if not item_ids:
            return
        placeholders = ", ".join("?" for _ in item_ids)
        with get_db_connection() as conn:
            conn.execute(
                f"UPDATE memory_items SET last_accessed_at = CURRENT_TIMESTAMP WHERE id IN ({placeholders})",
                tuple(item_ids),
            )
            conn.commit()

    def _rank_items(
        self,
        items: list[MemoryItemRecord],
        query_tokens: set[str],
        memory_type: str,
        limit: int,
    ) -> list[MemoryItemRecord]:
        typed_items = [item for item in items if item.memory_type == memory_type]
        scored = sorted(
            typed_items,
            key=lambda item: self._score_item(
                query_tokens,
                [item.subject or "", item.predicate or "", item.object or "", item.content],
                item.salience,
            ),
            reverse=True,
        )
        return scored[:limit]

    def _rank_consolidations(
        self,
        consolidations: list[MemoryConsolidationRecord],
        query_tokens: set[str],
        limit: int,
    ) -> list[MemoryConsolidationRecord]:
        scored = sorted(
            consolidations,
            key=lambda item: len(query_tokens & self._tokenize(item.content)),
            reverse=True,
        )
        return scored[:limit]

    def retrieve(
        self,
        *,
        user_id: str | None,
        tenant_id: str,
        project_id: str,
        query: str,
        max_procedural: int,
        max_semantic: int,
        max_relational: int,
        max_episodic: int,
        max_consolidated: int,
    ) -> RetrievedMemoryBundle:
        if not user_id:
            return RetrievedMemoryBundle([], [], [], [], [])

        query_tokens = self._tokenize(query)
        items = self._fetch_items(user_id, tenant_id, project_id)
        consolidations = self._fetch_consolidations(user_id, tenant_id, project_id)

        procedural = self._rank_items(items, query_tokens, "procedural", max_procedural)
        semantic = self._rank_items(items, query_tokens, "semantic", max_semantic)
        relational = self._rank_items(items, query_tokens, "relational", max_relational)
        episodic = self._rank_items(items, query_tokens, "episodic", max_episodic)
        consolidated = self._rank_consolidations(consolidations, query_tokens, max_consolidated)

        self._touch_items([item.id for item in [*procedural, *semantic, *relational, *episodic]])
        logger.info(
            "Retrieved StructMem items user=%s project=%s procedural=%d semantic=%d relational=%d episodic=%d consolidated=%d",
            user_id,
            project_id,
            len(procedural),
            len(semantic),
            len(relational),
            len(episodic),
            len(consolidated),
        )
        return RetrievedMemoryBundle(procedural, semantic, relational, episodic, consolidated)
