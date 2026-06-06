"""Tests for Contextual Retrieval (Anthropic pattern, 2024).

The ingestion service must prepend a short context header — derived from card
metadata (title, domain, summary, tags) — to each chunk BEFORE computing its
embedding. The stored `content` column must remain the raw chunk so retrieval
still returns the original text.

Reference: https://www.anthropic.com/news/contextual-retrieval
"""

from __future__ import annotations

import struct

import pytest

from app.core.database import get_db_connection
from app.models.knowledge import KnowledgeCardCreate
from app.services.knowledge_embedding_service import KnowledgeEmbeddingService
from app.services.knowledge_ingestion_service import (
    ChunkContext,
    KnowledgeIngestionService,
    build_contextual_chunk,
)


class _RecordingEmbedding(KnowledgeEmbeddingService):
    """Captures every string passed to embed/embed_as_pgvector for assertions."""

    def __init__(self) -> None:
        super().__init__()
        self.embed_calls: list[str] = []
        self.pgvector_calls: list[str] = []

    def embed(self, text: str) -> bytes:
        self.embed_calls.append(text)
        # Deterministic 8-dim bytes derived from text length so different
        # inputs yield different blobs (used to prove contextualization happened).
        dim = 8
        vals = [float(len(text)) / 1000.0] + [0.0] * (dim - 1)
        return struct.pack(f"{dim}f", *vals)

    def embed_as_pgvector(self, text: str) -> str:
        self.pgvector_calls.append(text)
        vals = [float(len(text)) / 1000.0] + [0.0] * 383
        return "[" + ",".join(f"{v:.8f}" for v in vals) + "]"


# ── build_contextual_chunk: pure function ─────────────────────────────


class TestBuildContextualChunk:
    @pytest.mark.unit
    def test_prepends_title_and_domain(self) -> None:
        ctx = ChunkContext(title="Lãi suất 2026", knowledge_domain="banking_rates")
        out = build_contextual_chunk(ctx, "Vietcombank 4.8%/năm")
        assert "Lãi suất 2026" in out
        assert "banking_rates" in out
        assert "Vietcombank 4.8%/năm" in out

    @pytest.mark.unit
    def test_chunk_body_preserved_verbatim(self) -> None:
        ctx = ChunkContext(title="T", knowledge_domain="d")
        chunk = "Exact body — không được thay đổi."
        out = build_contextual_chunk(ctx, chunk)
        assert out.endswith(chunk)

    @pytest.mark.unit
    def test_includes_summary_when_present(self) -> None:
        ctx = ChunkContext(
            title="T", knowledge_domain="d", summary="Tóm tắt ngắn về chính sách."
        )
        out = build_contextual_chunk(ctx, "body")
        assert "Tóm tắt ngắn về chính sách" in out

    @pytest.mark.unit
    def test_omits_summary_when_empty(self) -> None:
        ctx = ChunkContext(title="T", knowledge_domain="d", summary="")
        out = build_contextual_chunk(ctx, "body")
        assert "Tóm tắt" not in out

    @pytest.mark.unit
    def test_includes_tags_when_present(self) -> None:
        ctx = ChunkContext(
            title="T", knowledge_domain="d", tags=("sla", "pricing")
        )
        out = build_contextual_chunk(ctx, "body")
        assert "sla" in out
        assert "pricing" in out

    @pytest.mark.unit
    def test_omits_tags_block_when_empty(self) -> None:
        ctx = ChunkContext(title="T", knowledge_domain="d", tags=())
        out = build_contextual_chunk(ctx, "body")
        assert "Thẻ" not in out

    @pytest.mark.unit
    def test_header_separated_from_body_by_blank_line(self) -> None:
        ctx = ChunkContext(title="T", knowledge_domain="d")
        out = build_contextual_chunk(ctx, "body")
        assert "\n\nbody" in out

    @pytest.mark.unit
    def test_chunk_context_is_frozen_dataclass(self) -> None:
        ctx = ChunkContext(title="T", knowledge_domain="d")
        with pytest.raises((AttributeError, Exception)):  # FrozenInstanceError
            ctx.title = "X"  # type: ignore[misc]


# ── Integration with ingestion service ────────────────────────────────


class TestIngestionContextualization:
    @pytest.mark.unit
    def test_embed_receives_contextualized_text(self) -> None:
        rec = _RecordingEmbedding()
        svc = KnowledgeIngestionService(chunk_chars=200, embedding_service=rec)

        svc.create_card(
            KnowledgeCardCreate(
                project_id="chatbot",
                knowledge_domain="banking_rates",
                title="Lãi suất gửi tiết kiệm",
                summary="Bảng lãi suất 06/2026",
                content="Vietcombank áp dụng 4.8%/năm cho kỳ 6 tháng.",
                tags=["rates", "vcb"],
            )
        )

        assert rec.embed_calls, "embed must be called at least once"
        embedded_text = rec.embed_calls[0]
        # Header components must be present
        assert "Lãi suất gửi tiết kiệm" in embedded_text
        assert "banking_rates" in embedded_text
        assert "Bảng lãi suất 06/2026" in embedded_text
        assert "rates" in embedded_text
        # Body must be present too
        assert "Vietcombank áp dụng 4.8%" in embedded_text

    @pytest.mark.unit
    def test_pgvector_call_uses_same_contextualized_text(self) -> None:
        rec = _RecordingEmbedding()
        svc = KnowledgeIngestionService(chunk_chars=200, embedding_service=rec)

        svc.create_card(
            KnowledgeCardCreate(
                project_id="chatbot",
                knowledge_domain="faq",
                title="FAQ Title",
                content="Body content here.",
            )
        )

        # Both byte-embedding and pgvector embedding must see the same input
        # (otherwise the two storage paths would diverge).
        assert rec.embed_calls == rec.pgvector_calls

    @pytest.mark.unit
    def test_stored_content_is_raw_chunk_not_contextualized(self) -> None:
        rec = _RecordingEmbedding()
        svc = KnowledgeIngestionService(chunk_chars=200, embedding_service=rec)

        card = svc.create_card(
            KnowledgeCardCreate(
                project_id="chatbot",
                knowledge_domain="faq",
                title="A very distinctive title only in header",
                content="Body sentence A.",
            )
        )

        with get_db_connection() as conn:
            rows = conn.execute(
                "SELECT content FROM knowledge_card_chunks WHERE card_id = %s",
                (card.id,),
            ).fetchall()

        assert rows
        stored = rows[0]["content"]
        assert "Body sentence A" in stored
        # The title should NOT be persisted into chunk content (only into embedding text)
        assert "very distinctive title only in header" not in stored

    @pytest.mark.unit
    def test_no_embedding_service_skips_contextualization(self) -> None:
        # When no embedding service is wired, ingestion must still succeed
        # and store the raw chunk content as before (backward compat).
        svc = KnowledgeIngestionService(chunk_chars=200, embedding_service=None)
        card = svc.create_card(
            KnowledgeCardCreate(
                project_id="chatbot",
                knowledge_domain="faq",
                title="Title X",
                content="Plain body.",
            )
        )
        with get_db_connection() as conn:
            rows = conn.execute(
                "SELECT content, embedding FROM knowledge_card_chunks WHERE card_id = %s",
                (card.id,),
            ).fetchall()
        assert rows[0]["content"] == "Plain body."
        assert rows[0]["embedding"] is None

    @pytest.mark.unit
    def test_multiple_chunks_each_get_same_context_header(self) -> None:
        rec = _RecordingEmbedding()
        svc = KnowledgeIngestionService(chunk_chars=40, embedding_service=rec)

        svc.create_card(
            KnowledgeCardCreate(
                project_id="chatbot",
                knowledge_domain="multi",
                title="Multi Chunk Card",
                content="First paragraph here.\n\nSecond paragraph here.\n\nThird paragraph.",
            )
        )

        assert len(rec.embed_calls) >= 2
        # Each embed input must contain the title (header is per-chunk)
        for text in rec.embed_calls:
            assert "Multi Chunk Card" in text
            assert "multi" in text  # domain

    @pytest.mark.unit
    def test_fill_missing_embeddings_uses_card_context(self) -> None:
        """Backfill must reconstruct the contextual header from card metadata
        rather than embedding raw chunk content."""
        # First, ingest without an embedding service so embedding column is NULL
        plain_svc = KnowledgeIngestionService(chunk_chars=200, embedding_service=None)
        card = plain_svc.create_card(
            KnowledgeCardCreate(
                project_id="chatbot",
                knowledge_domain="backfill_dom",
                title="Backfill Title",
                summary="Backfill summary.",
                content="Backfillable body.",
                tags=["bf"],
            )
        )

        # Now backfill with a recording embedding
        rec = _RecordingEmbedding()
        backfill_svc = KnowledgeIngestionService(
            chunk_chars=200, embedding_service=rec
        )
        result = backfill_svc.fill_missing_embeddings(project_id="chatbot")
        assert result["updated"] >= 1

        # The backfilled embedding text must contain the card's title/domain/summary
        assert rec.embed_calls, "backfill must call embed"
        text = rec.embed_calls[0]
        assert "Backfill Title" in text
        assert "backfill_dom" in text
        assert "Backfill summary" in text
        assert "Backfillable body" in text
        # Sanity check the chunk content stored is still the raw body
        with get_db_connection() as conn:
            row = conn.execute(
                "SELECT content FROM knowledge_card_chunks WHERE card_id = %s",
                (card.id,),
            ).fetchone()
        assert row["content"] == "Backfillable body."

    @pytest.mark.unit
    def test_force_reindex_rebuilds_existing_embeddings(self) -> None:
        """force=True must re-embed chunks that already have an embedding —
        required for migrating chunks ingested before Contextual Retrieval."""
        first = _RecordingEmbedding()
        svc1 = KnowledgeIngestionService(chunk_chars=200, embedding_service=first)
        card = svc1.create_card(
            KnowledgeCardCreate(
                project_id="chatbot",
                knowledge_domain="reindex_dom",
                title="Reindex Title",
                content="Reindex body.",
            )
        )
        first_count = len(first.embed_calls)
        assert first_count >= 1  # initial ingest embedded once

        # Now re-run with force=True — must re-embed even though embedding exists
        second = _RecordingEmbedding()
        svc2 = KnowledgeIngestionService(chunk_chars=200, embedding_service=second)
        result = svc2.fill_missing_embeddings(project_id="chatbot", force=True)
        assert result["updated"] >= 1
        assert second.embed_calls, "force=True must re-embed existing chunks"
        # And it still must include the contextual header
        assert "Reindex Title" in second.embed_calls[0]
        assert "reindex_dom" in second.embed_calls[0]

    @pytest.mark.unit
    def test_default_reindex_skips_chunks_with_embedding(self) -> None:
        """Without force, existing-embedding chunks must NOT be re-embedded."""
        first = _RecordingEmbedding()
        svc1 = KnowledgeIngestionService(chunk_chars=200, embedding_service=first)
        svc1.create_card(
            KnowledgeCardCreate(
                project_id="chatbot",
                knowledge_domain="skip_dom",
                title="Skip Title",
                content="Skip body.",
            )
        )

        # Default (force=False) — should NOT touch anything
        second = _RecordingEmbedding()
        svc2 = KnowledgeIngestionService(chunk_chars=200, embedding_service=second)
        result = svc2.fill_missing_embeddings(project_id="chatbot")
        assert result["updated"] == 0
        assert second.embed_calls == []

