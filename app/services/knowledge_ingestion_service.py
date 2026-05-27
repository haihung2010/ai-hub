from __future__ import annotations

import json
import re
import uuid

from app.core.database import get_db_connection
from app.models.knowledge import KnowledgeCardCreate, KnowledgeCardRecord
from app.services.knowledge_embedding_service import KnowledgeEmbeddingService
from app.services.knowledge_link_service import KnowledgeLinkService

_PARAGRAPH_RE = re.compile(r"\n\s*\n+")


class KnowledgeIngestionService:
    def __init__(
        self,
        *,
        chunk_chars: int = 2000,
        chunk_overlap_chars: int = 200,
        max_card_chars: int = 100000,
        embedding_service: KnowledgeEmbeddingService | None = None,
    ) -> None:
        self._chunk_chars = chunk_chars
        self._chunk_overlap_chars = chunk_overlap_chars
        self._max_card_chars = max_card_chars
        self._embedding = embedding_service
        self._linker = KnowledgeLinkService()

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
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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

        # Auto-generate knowledge links
        try:
            self._linker.auto_link_card(card_id, req.project_id)
        except Exception as e:
            logger.warning('auto-link failed for card %s: %s', card_id, e)

        return self.get_card(card_id)

    def update_card(self, card_id: str, req: KnowledgeCardCreate) -> KnowledgeCardRecord:
        content = req.content[: self._max_card_chars]
        tags_json = json.dumps(req.tags, ensure_ascii=False)
        chunks = self._chunk_content(content)

        with get_db_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE knowledge_cards
                SET tenant_id = %s, project_id = %s, knowledge_domain = %s, title = %s, summary = %s,
                    content = %s, source_type = %s, trust_level = %s, status = %s, version = %s,
                    effective_from = %s, effective_to = %s, tags = %s, owner = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
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

        # Auto-generate knowledge links
        try:
            self._linker.auto_link_card(card_id, req.project_id)
        except Exception as e:
            logger.warning('auto-link failed for card %s: %s', card_id, e)

        return self.get_card(card_id)

    def get_card(self, card_id: str) -> KnowledgeCardRecord:
        with get_db_connection() as conn:
            row = conn.execute("SELECT * FROM knowledge_cards WHERE id = %s", (card_id,)).fetchone()
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
        query = "SELECT * FROM knowledge_cards WHERE tenant_id = %s AND project_id = %s"
        params: list[object] = [tenant_id, project_id]
        if knowledge_domain:
            query += " AND knowledge_domain = %s"
            params.append(knowledge_domain)
        if status:
            query += " AND status = %s"
            params.append(status)
        query += " ORDER BY updated_at DESC, created_at DESC LIMIT %s"
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
                # Long paragraph: split without overlap (each piece independent)
                for i in range(0, len(paragraph), self._chunk_chars):
                    piece = paragraph[i : i + self._chunk_chars].strip()
                    if piece:
                        chunks.append(piece)
                continue
            candidate = f"{current}\n\n{paragraph}" if current else paragraph
            if len(candidate) <= self._chunk_chars:
                current = candidate
            else:
                # Keep overlap tail from previous chunk for context continuity
                overlap = current[-self._chunk_overlap_chars :] if self._chunk_overlap_chars > 0 else ""
                chunks.append(current)
                current = f"{overlap}\n\n{paragraph}" if overlap else paragraph
        if current:
            chunks.append(current)
        return chunks

    def _replace_chunks(self, conn, card_id: str, tenant_id: str, project_id: str, chunks: list[str]) -> None:
        conn.execute("DELETE FROM knowledge_card_chunks WHERE card_id = %s", (card_id,))
        has_vector_column = self._has_column(conn, "knowledge_card_chunks", "embedding_vec")
        for index, chunk in enumerate(chunks):
            embedding = self._embedding.embed(chunk) if self._embedding else None
            base_values = (
                str(uuid.uuid4()),
                card_id,
                tenant_id,
                project_id,
                index,
                chunk,
                max(1, len(chunk) // 4),
                embedding,
            )
            if has_vector_column:
                conn.execute(
                    """
                    INSERT INTO knowledge_card_chunks (
                        id, card_id, tenant_id, project_id, chunk_index, content, token_estimate, embedding, embedding_vec
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::vector)
                    """,
                    (*base_values, self._embedding.embed_as_pgvector(chunk) if self._embedding else None),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO knowledge_card_chunks (
                        id, card_id, tenant_id, project_id, chunk_index, content, token_estimate, embedding
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    base_values,
                )

    @staticmethod
    def _has_column(conn, table: str, column: str) -> bool:
        row = conn.execute(
            "SELECT 1 FROM information_schema.columns WHERE table_name = %s AND column_name = %s",
            (table, column),
        ).fetchone()
        return row is not None

    def fill_missing_embeddings(
        self,
        *,
        tenant_id: str | None = None,
        project_id: str | None = None,
        batch_size: int = 50,
    ) -> dict[str, int]:
        """Embed chunks that have a NULL embedding blob. Returns updated/skipped counts."""
        if not self._embedding:
            return {"total": 0, "updated": 0, "skipped": 0, "error": "no embedding service"}

        sql = "SELECT id, content FROM knowledge_card_chunks WHERE embedding IS NULL"
        params: list[object] = []
        if tenant_id:
            sql += " AND tenant_id = %s"
            params.append(tenant_id)
        if project_id:
            sql += " AND project_id = %s"
            params.append(project_id)
        sql += f" LIMIT {batch_size * 20}"

        with get_db_connection() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()

        total = len(rows)
        updated = 0
        skipped = 0
        for row in rows:
            try:
                embedding = self._embedding.embed(row["content"])
                vec_str = self._embedding.embed_as_pgvector(row["content"])
                with get_db_connection() as conn:
                    conn.execute(
                        "UPDATE knowledge_card_chunks SET embedding = %s, embedding_vec = %s::vector WHERE id = %s",
                        (embedding, vec_str, row["id"]),
                    )
                    conn.commit()
                updated += 1
            except Exception:
                skipped += 1

        return {"total": total, "updated": updated, "skipped": skipped}

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
