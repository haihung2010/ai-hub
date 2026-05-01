from __future__ import annotations

import pytest

from app.core.database import get_db_connection
from app.models.knowledge import KnowledgeCardCreate
from app.services.knowledge_ingestion_service import KnowledgeIngestionService


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
            "SELECT content FROM knowledge_card_chunks WHERE card_id = ? ORDER BY chunk_index",
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
            "SELECT content FROM knowledge_card_chunks WHERE card_id = ?",
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
