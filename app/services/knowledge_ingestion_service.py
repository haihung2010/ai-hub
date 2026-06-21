from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from dataclasses import dataclass, field

from app.core.database import get_db_connection
from app.models.knowledge import KnowledgeCardCreate, KnowledgeCardRecord
from app.services.contextualizer import Contextualizer
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
        contextualizer: Contextualizer | None = None,
    ) -> None:
        self._chunk_chars = chunk_chars
        self._chunk_overlap_chars = chunk_overlap_chars
        self._max_card_chars = max_card_chars
        self._embedding = embedding_service
        self._linker = KnowledgeLinkService()
        # Anthropic Contextual Retrieval (2026-06-19). When set, each
        # chunk gets an LLM-generated 50-100 token context prepended
        # before embedding AND before the FTS tsvector is built. Phase 2
        # will actually call ctx.generate() during ingestion; for now we
        # just hold the reference and document the contract.
        self._contextualizer = contextualizer

    def create_card(self, req: KnowledgeCardCreate) -> KnowledgeCardRecord:
        """Sync entry point. Delegates to the async implementation via
        ``asyncio.run`` so scripts (cron jobs, one-off ingestion) can use
        the same code path as FastAPI routes.

        Note: cannot be called from inside a running event loop (which is
        why FastAPI routes use the async ``create_card`` directly).
        """
        return asyncio.run(self.create_card_async(req))

    async def create_card_async(self, req: KnowledgeCardCreate) -> KnowledgeCardRecord:
        content = req.content[: self._max_card_chars]
        card_id = str(uuid.uuid4())
        tags_json = json.dumps(req.tags, ensure_ascii=False)
        chunks = self._chunk_content(content)
        # Resolve contextual text per chunk (LLM if configured, else
        # deterministic header). Done before the DB transaction so a slow
        # E4B doesn't hold a row lock.
        contextual_texts = await self._resolve_contextual_texts(
            chunks=chunks,
            full_document=content,
            context=_context_from_request(req),
        )

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
                contextual_texts=contextual_texts,
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
        """Sync wrapper — see ``create_card`` for the rationale."""
        return asyncio.run(self.update_card_async(card_id, req))

    async def update_card_async(
        self, card_id: str, req: KnowledgeCardCreate
    ) -> KnowledgeCardRecord:
        content = req.content[: self._max_card_chars]
        tags_json = json.dumps(req.tags, ensure_ascii=False)
        chunks = self._chunk_content(content)
        contextual_texts = await self._resolve_contextual_texts(
            chunks=chunks,
            full_document=content,
            context=_context_from_request(req),
        )

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
                contextual_texts=contextual_texts,
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

    async def _resolve_contextual_texts(
        self,
        *,
        chunks: list[str],
        full_document: str,
        context: ChunkContext,
    ) -> list[str]:
        """Build the contextualized text for each chunk.

        Two paths:
        - LLM path (Anthropic full Contextual Retrieval): call the
          Contextualizer per chunk, prepend the LLM-generated 50-100
          token context to the raw chunk. Runs all chunks in parallel
          via asyncio.gather — E4B is rate-limited upstream so the
          gather is bounded by E4B's own load balancer.
        - Deterministic path (backward compat): use the metadata-derived
          header from `build_contextual_chunk`. Synchronous, fast.

        Returns a list parallel to `chunks` — index i holds the
        contextual text for chunk i. Used by `_replace_chunks` for the
        embedding and the `contextual_text` column (which the FTS
        trigger indexes).
        """
        if self._contextualizer is None:
            return [build_contextual_chunk(context, chunk) for chunk in chunks]

        # LLM path: ask E4B to generate context for each chunk, then
        # prepend it to the raw chunk. Contextualizer.generate() never
        # raises (falls back to a deterministic header internally), so
        # gather() will not fail.
        contexts = await asyncio.gather(*[
            self._contextualizer.generate(
                chunk_text=chunk, full_document=full_document
            )
            for chunk in chunks
        ])
        return [f"{ctx}\n\n{chunk}" for ctx, chunk in zip(contexts, chunks)]

    def _replace_chunks(
        self,
        conn,
        card_id: str,
        tenant_id: str,
        project_id: str,
        chunks: list[str],
        *,
        contextual_texts: list[str] | None = None,
        context: ChunkContext,
    ) -> None:
        """Insert chunk rows. `contextual_texts[i]` (if provided) is what
        gets embedded AND stored in the `contextual_text` column. Falls
        back to the deterministic header if `contextual_texts` is None
        (so callers that don't use a Contextualizer still work)."""
        if contextual_texts is None:
            contextual_texts = [build_contextual_chunk(context, chunk) for chunk in chunks]
        if len(contextual_texts) != len(chunks):
            raise ValueError(
                f"contextual_texts length {len(contextual_texts)} != chunks length {len(chunks)}"
            )

        conn.execute("DELETE FROM knowledge_card_chunks WHERE card_id = %s", (card_id,))
        has_vector_column = self._has_column(conn, "knowledge_card_chunks", "embedding_vec")
        has_contextual_col = self._has_column(conn, "knowledge_card_chunks", "contextual_text")
        for index, chunk in enumerate(chunks):
            embed_text = contextual_texts[index]
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
            if has_vector_column and has_contextual_col:
                pgvec = self._embedding.embed_as_pgvector(embed_text) if self._embedding else None
                conn.execute(
                    """
                    INSERT INTO knowledge_card_chunks (
                        id, card_id, tenant_id, project_id, chunk_index,
                        content, token_estimate, embedding, embedding_vec,
                        contextual_text
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::vector, %s)
                    """,
                    (*base_values, pgvec, embed_text),
                )
            elif has_vector_column:
                pgvec = self._embedding.embed_as_pgvector(embed_text) if self._embedding else None
                conn.execute(
                    """
                    INSERT INTO knowledge_card_chunks (
                        id, card_id, tenant_id, project_id, chunk_index, content, token_estimate, embedding, embedding_vec
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::vector)
                    """,
                    (*base_values, pgvec),
                )
            elif has_contextual_col:
                conn.execute(
                    """
                    INSERT INTO knowledge_card_chunks (
                        id, card_id, tenant_id, project_id, chunk_index, content, token_estimate, embedding, contextual_text
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (*base_values, embed_text),
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
        contextualize: bool = True,
    ) -> dict[str, int]:
        """Sync wrapper around ``fill_missing_embeddings_async``. See that
        method for the contract.
        """
        return asyncio.run(
            self.fill_missing_embeddings_async(
                tenant_id=tenant_id,
                project_id=project_id,
                batch_size=batch_size,
                force=force,
                contextualize=contextualize,
            )
        )

    async def fill_missing_embeddings_async(
        self,
        *,
        tenant_id: str | None = None,
        project_id: str | None = None,
        batch_size: int = 50,
        force: bool = False,
        contextualize: bool = True,
    ) -> dict[str, int]:
        """Embed chunks that have a NULL embedding blob. Returns updated/skipped counts.

        Uses Contextual Retrieval: reconstructs the card-level header from
        knowledge_cards (title, domain, summary, tags) so backfilled embeddings
        carry the same context as freshly ingested ones.

        When ``force=True``, re-embeds every matching chunk regardless of
        whether it already has an embedding — needed to roll Contextual
        Retrieval out across chunks ingested before the feature existed.

        When ``contextualize=True`` AND a Contextualizer is configured, the
        LLM-generated 50-100 token context is used (Anthropic full
        Contextual Retrieval). Otherwise the deterministic metadata-derived
        header is used (backward compat).
        """
        if not self._embedding:
            return {"total": 0, "updated": 0, "skipped": 0, "error": "no embedding service"}

        # Add cards.content so the LLM Contextualizer can see the full
        # document (required by its prompt template).
        sql = (
            "SELECT chunks.id AS chunk_id, chunks.content AS chunk_content, "
            "cards.title AS card_title, cards.knowledge_domain AS card_domain, "
            "cards.summary AS card_summary, cards.tags AS card_tags, "
            "cards.content AS card_content "
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
        use_llm = contextualize and self._contextualizer is not None

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
                chunk_text = row["chunk_content"]
                if use_llm:
                    llm_ctx = await self._contextualizer.generate(  # type: ignore[union-attr]
                        chunk_text=chunk_text,
                        full_document=row["card_content"] or chunk_text,
                    )
                    embed_text = f"{llm_ctx}\n\n{chunk_text}"
                else:
                    embed_text = build_contextual_chunk(context, chunk_text)
                embedding = self._embedding.embed(embed_text)
                vec_str = self._embedding.embed_as_pgvector(embed_text)
                with get_db_connection() as conn:
                    conn.execute(
                        "UPDATE knowledge_card_chunks "
                        "SET embedding = %s, embedding_vec = %s::vector, "
                        "    contextual_text = %s, contextual_model_version = %s "
                        "WHERE id = %s",
                        (
                            embedding, vec_str, embed_text,
                            self._contextualizer._model if use_llm else "",  # type: ignore[union-attr]
                            row["chunk_id"],
                        ),
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
