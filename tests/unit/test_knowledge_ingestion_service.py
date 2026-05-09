from __future__ import annotations

import struct

import pytest

from app.core.database import get_db_connection
from app.models.knowledge import KnowledgeCardCreate
from app.services.knowledge_embedding_service import KnowledgeEmbeddingService
from app.services.knowledge_ingestion_service import KnowledgeIngestionService


class _FakeEmbedding(KnowledgeEmbeddingService):
    """Deterministic embedding: first 4 floats from char codes, rest zeros."""
    def __init__(self) -> None:
        super().__init__()

    def embed(self, text: str) -> bytes:
        dim = 8
        vals = [float(ord(c)) / 1000.0 for c in text[:dim]]
        vals += [0.0] * (dim - len(vals))
        return struct.pack(f"{dim}f", *vals)


@pytest.mark.unit
def test_create_card_persists_card_and_chunks() -> None:
    service = KnowledgeIngestionService(chunk_chars=40)

    card = service.create_card(
        KnowledgeCardCreate(
            tenant_id="default",
            project_id="chatbot",
            knowledge_domain="pricing_policy",
            title="Pricing policy",
            summary="Standard support pricing",
            content="First paragraph about gold plan.\n\nSecond paragraph about support SLA.",
            tags=["pricing", "sla"],
        )
    )

    assert card.project_id == "chatbot"
    assert card.tags == ["pricing", "sla"]
    with get_db_connection() as conn:
        chunks = conn.execute(
            "SELECT content FROM knowledge_card_chunks WHERE card_id = %s ORDER BY chunk_index",
            (card.id,),
        ).fetchall()
    assert len(chunks) >= 2
    assert "gold plan" in chunks[0]["content"]


@pytest.mark.unit
def test_update_card_replaces_chunks() -> None:
    service = KnowledgeIngestionService(chunk_chars=80)
    card = service.create_card(
        KnowledgeCardCreate(
            project_id="chatbot",
            knowledge_domain="faq",
            title="FAQ",
            content="Old answer",
        )
    )

    updated = service.update_card(
        card.id,
        KnowledgeCardCreate(
            project_id="chatbot",
            knowledge_domain="faq",
            title="FAQ v2",
            content="New answer only",
            version=2,
        ),
    )

    assert updated.version == 2
    with get_db_connection() as conn:
        chunks = conn.execute(
            "SELECT content FROM knowledge_card_chunks WHERE card_id = %s",
            (card.id,),
        ).fetchall()
    assert [row["content"] for row in chunks] == ["New answer only"]


@pytest.mark.unit
def test_invalid_status_is_rejected() -> None:
    with pytest.raises(ValueError):
        KnowledgeCardCreate(
            project_id="chatbot",
            knowledge_domain="faq",
            title="FAQ",
            content="Answer",
            status="deleted",
        )


@pytest.mark.unit
def test_create_card_stores_embedding_blob() -> None:
    service = KnowledgeIngestionService(
        chunk_chars=200,
        embedding_service=_FakeEmbedding(),
    )
    card = service.create_card(
        KnowledgeCardCreate(
            project_id="chatbot",
            knowledge_domain="faq",
            title="Embed test",
            content="Some content to embed.",
        )
    )

    with get_db_connection() as conn:
        rows = conn.execute(
            "SELECT embedding FROM knowledge_card_chunks WHERE card_id = %s",
            (card.id,),
        ).fetchall()

    assert len(rows) >= 1
    for row in rows:
        assert row["embedding"] is not None
        assert len(row["embedding"]) > 0


@pytest.mark.unit
def test_create_card_without_embedding_stores_null() -> None:
    service = KnowledgeIngestionService(chunk_chars=200, embedding_service=None)
    card = service.create_card(
        KnowledgeCardCreate(
            project_id="chatbot",
            knowledge_domain="faq",
            title="No embed",
            content="Content without embeddings.",
        )
    )

    with get_db_connection() as conn:
        rows = conn.execute(
            "SELECT embedding FROM knowledge_card_chunks WHERE card_id = %s",
            (card.id,),
        ).fetchall()

    assert all(row["embedding"] is None for row in rows)
