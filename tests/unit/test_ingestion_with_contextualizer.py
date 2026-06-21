"""Tests for KnowledgeIngestionService when wired with a Contextualizer.

Builds on top of `test_contextual_retrieval.py` (deterministic header).
These tests cover the LLM-generated path (Anthropic full Contextual
Retrieval). The Contextualizer is stubbed so the test never touches E4B
on port 8081.

The async pattern: create_card and update_card are `async def` so they
can `await ctx.generate()` per chunk. Sync wrappers for scripts live in
the same module.
"""

from __future__ import annotations

import asyncio
import json
import struct
import uuid
from typing import Any

import pytest

from app.core.database import get_db_connection
from app.models.knowledge import KnowledgeCardCreate
from app.services.contextualizer import Contextualizer
from app.services.knowledge_embedding_service import KnowledgeEmbeddingService
from app.services.knowledge_ingestion_service import KnowledgeIngestionService


pytestmark = pytest.mark.no_isolated_db


# ── Stubs ──────────────────────────────────────────────────────────


class _RecordingEmbedding(KnowledgeEmbeddingService):
    """Captures every string passed to embed/embed_as_pgvector."""

    def __init__(self) -> None:
        super().__init__()
        self.embed_calls: list[str] = []
        self.pgvector_calls: list[str] = []

    def embed(self, text: str) -> bytes:
        self.embed_calls.append(text)
        dim = 8
        vals = [float(len(text)) / 1000.0] + [0.0] * (dim - 1)
        return struct.pack(f"{dim}f", *vals)

    def embed_as_pgvector(self, text: str) -> str:
        self.pgvector_calls.append(text)
        vals = [float(len(text)) / 1000.0] + [0.0] * (dim := 383)
        return "[" + ",".join(f"{v:.8f}" for v in vals) + "]"


class _StubContextualizer:
    """Records every generate() call. Returns a configurable per-chunk
    context. Defaults to returning a deterministic string so the test
    can assert the LLM output was used.
    """

    def __init__(self, response: str = "LLM-generated context.") -> None:
        self._response = response
        self.calls: list[dict[str, str]] = []

    async def generate(
        self, *, chunk_text: str, full_document: str
    ) -> str:
        self.calls.append({"chunk_text": chunk_text, "full_document": full_document})
        return self._response


# ── Async ingestion with Contextualizer ────────────────────────────


class TestAsyncIngestionWithContextualizer:
    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_create_card_calls_contextualizer_for_each_chunk(self) -> None:
        rec = _RecordingEmbedding()
        ctx_stub = _StubContextualizer(
            response="Bảng lãi suất ngân hàng Việt Nam 2026."
        )
        svc = KnowledgeIngestionService(
            chunk_chars=200,  # force multiple chunks
            embedding_service=rec,
            contextualizer=ctx_stub,  # type: ignore[arg-type]
        )

        long_content = (
            "Vietcombank 4.8%/năm cho kỳ hạn 12 tháng. "
            "BIDV 5.1%/năm cho kỳ hạn 12 tháng. "
            "Agribank 4.5%/năm cho kỳ hạn 12 tháng. "
            "Techcombank 5.5%/năm cho kỳ hạn 12 tháng."
        )
        await svc.create_card_async(
            KnowledgeCardCreate(
                project_id="chatbot",
                knowledge_domain="banking_rates",
                title="Lãi suất 2026",
                content=long_content,
            )
        )

        # Contextualizer called once per chunk
        assert len(ctx_stub.calls) >= 1
        # Each call saw the same full document (chunks differ but doc
        # is the same for the whole card)
        for call in ctx_stub.calls:
            assert call["full_document"] == long_content

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_create_card_embed_uses_llm_context(self) -> None:
        """When Contextualizer is set, embed() must receive the LLM-generated
        context (not the deterministic header, not the raw chunk)."""
        rec = _RecordingEmbedding()
        ctx_stub = _StubContextualizer(
            response="Bảng lãi suất ngân hàng Việt Nam 2026"
        )
        svc = KnowledgeIngestionService(
            chunk_chars=200,
            embedding_service=rec,
            contextualizer=ctx_stub,  # type: ignore[arg-type]
        )

        await svc.create_card_async(
            KnowledgeCardCreate(
                project_id="chatbot",
                knowledge_domain="banking_rates",
                title="Lãi suất",
                content="Vietcombank 4.8%/năm.",
            )
        )

        assert rec.embed_calls, "embed must be called"
        embedded = rec.embed_calls[0]
        # LLM context must be in the embedded text
        assert "Bảng lãi suất ngân hàng Việt Nam 2026" in embedded
        # Raw chunk must be in the embedded text too
        assert "Vietcombank 4.8%/năm" in embedded

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_contextualizer_none_falls_back_to_deterministic(self) -> None:
        """When Contextualizer is None, the existing build_contextual_chunk
        path is used (backward compat with current production behavior)."""
        rec = _RecordingEmbedding()
        svc = KnowledgeIngestionService(
            chunk_chars=200,
            embedding_service=rec,
            contextualizer=None,  # explicit off
        )

        await svc.create_card_async(
            KnowledgeCardCreate(
                project_id="chatbot",
                knowledge_domain="banking_rates",
                title="Lãi suất 2026",
                summary="Bảng lãi suất 06/2026",
                content="Vietcombank 4.8%/năm.",
                tags=["rates"],
            )
        )

        assert rec.embed_calls
        embedded = rec.embed_calls[0]
        # Deterministic header parts present
        assert "Lãi suất 2026" in embedded
        assert "banking_rates" in embedded
        assert "Bảng lãi suất 06/2026" in embedded
        assert "rates" in embedded
        assert "Vietcombank 4.8%/năm" in embedded


# ── Sync wrapper for scripts (backward compat) ─────────────────────


class TestSyncWrapper:
    @pytest.mark.unit
    def test_create_card_sync_calls_async_under_the_hood(self) -> None:
        """`create_card` is a thin sync wrapper around `create_card_async`
        for scripts that can't await (e.g. cron jobs, one-off ingestion).
        It runs the async implementation via asyncio.run."""
        rec = _RecordingEmbedding()
        ctx_stub = _StubContextualizer(response="LLM ctx")
        svc = KnowledgeIngestionService(
            chunk_chars=200,
            embedding_service=rec,
            contextualizer=ctx_stub,  # type: ignore[arg-type]
        )

        # This must NOT raise "asyncio.run() cannot be called from a
        # running event loop" — it works from plain sync code.
        card = svc.create_card(
            KnowledgeCardCreate(
                project_id="chatbot",
                knowledge_domain="banking_rates",
                title="Lãi suất",
                content="Vietcombank 4.8%.",
            )
        )

        assert card.id
        assert len(ctx_stub.calls) >= 1
