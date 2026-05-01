from __future__ import annotations

import json
import re
import uuid

from app.core.database import get_db_connection
from app.models.knowledge import KnowledgeCardCreate, KnowledgeCardRecord

_PARAGRAPH_RE = re.compile(r"\n\s*\n+")


class KnowledgeIngestionService:
    def __init__(self, *, chunk_chars: int = 2000, max_card_chars: int = 100000) -> None:
        self._chunk_chars = chunk_chars
        self._max_card_chars = max_card_chars

    def create_card(self, req: KnowledgeCardCreate) -> KnowledgeCardRecord:
        content = req.content[: self._max_card_chars]
        card_id = str(uuid.uuid4())
        tags_json = json.dumps(req.tags, ensure_ascii=False)
        chunks = self._chunk_content(content)

        with get_db_connection() as conn:
            conn.execute(
                """
                INSERT INTO knowledge_cards (
                    id, tenant_id, project_id, knowledge_domain, title, summary, content,
                    source_type, trust_level, status, version, effective_from, effective_to,
                    tags, owner
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    card_id,
                    req.tenant_id,
                    req.project_id,
                    req.knowledge_domain,
                    req.title,
                    req.summary,
                    content,
                    req.source_type,
                    req.trust_level,
                    req.status,
                    req.version,
                    req.effective_from,
                    req.effective_to,
                    tags_json,
                    req.owner,
                ),
            )
            self._replace_chunks(conn, card_id, req.tenant_id, req.project_id, chunks)
            conn.commit()

        return self.get_card(card_id)

    def update_card(self, card_id: str, req: KnowledgeCardCreate) -> KnowledgeCardRecord:
        content = req.content[: self._max_card_chars]
        tags_json = json.dumps(req.tags, ensure_ascii=False)
        chunks = self._chunk_content(content)

        with get_db_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE knowledge_cards
                SET tenant_id = ?, project_id = ?, knowledge_domain = ?, title = ?, summary = ?,
                    content = ?, source_type = ?, trust_level = ?, status = ?, version = ?,
                    effective_from = ?, effective_to = ?, tags = ?, owner = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    req.tenant_id,
                    req.project_id,
                    req.knowledge_domain,
                    req.title,
                    req.summary,
                    content,
                    req.source_type,
                    req.trust_level,
                    req.status,
                    req.version,
                    req.effective_from,
                    req.effective_to,
                    tags_json,
                    req.owner,
                    card_id,
                ),
            )
            if cursor.rowcount == 0:
                raise KeyError(card_id)
            self._replace_chunks(conn, card_id, req.tenant_id, req.project_id, chunks)
            conn.commit()

        return self.get_card(card_id)

    def get_card(self, card_id: str) -> KnowledgeCardRecord:
        with get_db_connection() as conn:
            row = conn.execute("SELECT * FROM knowledge_cards WHERE id = ?", (card_id,)).fetchone()
        if row is None:
            raise KeyError(card_id)
        return self._row_to_card(row)

    def list_cards(
        self,
        *,
        tenant_id: str,
        project_id: str,
        knowledge_domain: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[KnowledgeCardRecord]:
        query = "SELECT * FROM knowledge_cards WHERE tenant_id = ? AND project_id = ?"
        params: list[object] = [tenant_id, project_id]
        if knowledge_domain:
            query += " AND knowledge_domain = ?"
            params.append(knowledge_domain)
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY updated_at DESC, created_at DESC LIMIT ?"
        params.append(limit)
        with get_db_connection() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [self._row_to_card(row) for row in rows]

    def _chunk_content(self, content: str) -> list[str]:
        paragraphs = [part.strip() for part in _PARAGRAPH_RE.split(content) if part.strip()]
        if not paragraphs:
            return [content.strip()] if content.strip() else []

        chunks: list[str] = []
        current = ""
        for paragraph in paragraphs:
            if len(paragraph) > self._chunk_chars:
                if current:
                    chunks.append(current)
                    current = ""
                chunks.extend(paragraph[i : i + self._chunk_chars].strip() for i in range(0, len(paragraph), self._chunk_chars))
                continue
            candidate = f"{current}\n\n{paragraph}" if current else paragraph
            if len(candidate) <= self._chunk_chars:
                current = candidate
            else:
                chunks.append(current)
                current = paragraph
        if current:
            chunks.append(current)
        return chunks

    def _replace_chunks(self, conn, card_id: str, tenant_id: str, project_id: str, chunks: list[str]) -> None:
        conn.execute("DELETE FROM knowledge_card_chunks WHERE card_id = ?", (card_id,))
        for index, chunk in enumerate(chunks):
            conn.execute(
                """
                INSERT INTO knowledge_card_chunks (
                    id, card_id, tenant_id, project_id, chunk_index, content, token_estimate
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    card_id,
                    tenant_id,
                    project_id,
                    index,
                    chunk,
                    max(1, len(chunk) // 4),
                ),
            )

    @staticmethod
    def _row_to_card(row) -> KnowledgeCardRecord:
        return KnowledgeCardRecord(
            id=row["id"],
            tenant_id=row["tenant_id"],
            project_id=row["project_id"],
            knowledge_domain=row["knowledge_domain"],
            title=row["title"],
            summary=row["summary"],
            content=row["content"],
            source_type=row["source_type"],
            trust_level=int(row["trust_level"]),
            status=row["status"],
            version=int(row["version"]),
            effective_from=row["effective_from"],
            effective_to=row["effective_to"],
            tags=json.loads(row["tags"] or "[]"),
            owner=row["owner"],
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )
