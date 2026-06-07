from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass, field

from app.core.database import get_db_connection
from app.models.knowledge import KnowledgeCardCreate, KnowledgeCardRecord
from app.services.knowledge_embedding_service import KnowledgeEmbeddingService
from app.services.knowledge_link_service import KnowledgeLinkService

logger = logging.getLogger(__name__)

_PARAGRAPH_RE = re.compile(r"\n\s*\n+")


@dataclass(frozen=True)
class ChunkContext:
    """Minimal card metadata used to build the Contextual Retrieval header.

    Anthropic Contextual Retrieval (2024): prepend a short header derived from
    surrounding metadata before embedding each chunk. The stored chunk content
    is unchanged — only the *embedded* text gains the context.
    """

    title: str
    knowledge_domain: str
    summary: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)


def build_contextual_chunk(context: ChunkContext, chunk: str) -> str:
    """Build a contextualized chunk for embedding.

    Output shape::

        Trích từ "<title>" thuộc chuyên đề <domain>. [Tóm tắt: <summary>.] [Thẻ: <tags>.]
        \\n\\n
        <chunk>

    The blank line separates header from body so cross-encoder rerankers see
    them as distinct segments. Vietnamese header is intentional — primary
    deployment is a Vietnamese chatbot.
    """
    parts = [f'Trích từ "{context.title}" thuộc chuyên đề {context.knowledge_domain}.']
    if context.summary:
        parts.append(f"Tóm tắt: {context.summary}.")
    if context.tags:
        parts.append(f"Thẻ: {', '.join(context.tags)}.")
    header = " ".join(parts)
    return f"{header}\n\n{chunk}"


def _context_from_request(req: KnowledgeCardCreate) -> ChunkContext:
    return ChunkContext(
        title=req.title,
        knowledge_domain=req.knowledge_domain,
        summary=req.summary or "",
        tags=tuple(req.tags or ()),
    )


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
            self._replace_chunks(
                conn,
                card_id,
                req.tenant_id,
                req.project_id,
                chunks,
                context=_context_from_request(req),
            )
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
            self._replace_chunks(
                conn,
                card_id,
                req.tenant_id,
                req.project_id,
                chunks,
                context=_context_from_request(req),
            )
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

    def _replace_chunks(
        self,
        conn,
        card_id: str,
        tenant_id: str,
        project_id: str,
        chunks: list[str],
        *,
        context: ChunkContext,
    ) -> None:
        conn.execute("DELETE FROM knowledge_card_chunks WHERE card_id = %s", (card_id,))
        has_vector_column = self._has_column(conn, "knowledge_card_chunks", "embedding_vec")
        for index, chunk in enumerate(chunks):
            # Contextual Retrieval (Anthropic 2024): embed header+chunk, store raw chunk.
            embed_text = build_contextual_chunk(context, chunk) if self._embedding else chunk
            embedding = self._embedding.embed(embed_text) if self._embedding else None
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
                pgvec = self._embedding.embed_as_pgvector(embed_text) if self._embedding else None
                conn.execute(
                    """
                    INSERT INTO knowledge_card_chunks (
                        id, card_id, tenant_id, project_id, chunk_index, content, token_estimate, embedding, embedding_vec
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::vector)
                    """,
                    (*base_values, pgvec),
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
        force: bool = False,
    ) -> dict[str, int]:
        """Embed chunks that have a NULL embedding blob. Returns updated/skipped counts.

        Uses Contextual Retrieval: reconstructs the card-level header from
        knowledge_cards (title, domain, summary, tags) so backfilled embeddings
        carry the same context as freshly ingested ones.

        When ``force=True``, re-embeds every matching chunk regardless of
        whether it already has an embedding — needed to roll Contextual
        Retrieval out across chunks ingested before the feature existed.
        """
        if not self._embedding:
            return {"total": 0, "updated": 0, "skipped": 0, "error": "no embedding service"}

        sql = (
            "SELECT chunks.id AS chunk_id, chunks.content AS chunk_content, "
            "cards.title AS card_title, cards.knowledge_domain AS card_domain, "
            "cards.summary AS card_summary, cards.tags AS card_tags "
            "FROM knowledge_card_chunks chunks "
            "JOIN knowledge_cards cards ON cards.id = chunks.card_id "
        )
        where_parts: list[str] = []
        if not force:
            where_parts.append("chunks.embedding IS NULL")
        params: list[object] = []
        if tenant_id:
            where_parts.append("chunks.tenant_id = %s")
            params.append(tenant_id)
        if project_id:
            where_parts.append("chunks.project_id = %s")
            params.append(project_id)
        if where_parts:
            sql += " WHERE " + " AND ".join(where_parts)
        sql += f" LIMIT {batch_size * 20}"

        with get_db_connection() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()

        total = len(rows)
        updated = 0
        skipped = 0
        for row in rows:
            try:
                tags_raw = row["card_tags"] or "[]"
                tags = tuple(json.loads(tags_raw)) if isinstance(tags_raw, str) else tuple(tags_raw)
                context = ChunkContext(
                    title=row["card_title"],
                    knowledge_domain=row["card_domain"],
                    summary=row["card_summary"] or "",
                    tags=tags,
                )
                embed_text = build_contextual_chunk(context, row["chunk_content"])
                embedding = self._embedding.embed(embed_text)
                vec_str = self._embedding.embed_as_pgvector(embed_text)
                with get_db_connection() as conn:
                    conn.execute(
                        "UPDATE knowledge_card_chunks SET embedding = %s, embedding_vec = %s::vector WHERE id = %s",
                        (embedding, vec_str, row["chunk_id"]),
                    )
                    conn.commit()
                updated += 1
            except Exception as exc:
                logger.warning("fill_missing_embeddings: skipping chunk %s: %s", row.get("chunk_id"), exc)
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
