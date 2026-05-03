from __future__ import annotations

import struct

import pytest

from app.models.knowledge import KnowledgeCardCreate
from app.services.knowledge_embedding_service import KnowledgeEmbeddingService
from app.services.knowledge_ingestion_service import KnowledgeIngestionService
from app.services.knowledge_retrieval_service import KnowledgeRetrievalService


class _FakeEmbedding(KnowledgeEmbeddingService):
    """Returns a unit vector based on presence of keywords for deterministic testing."""
    _KEYWORDS = ["refund", "pricing", "gold", "ecommerce", "support", "faq", "policy"]

    def __init__(self) -> None:
        super().__init__()

    def embed(self, text: str) -> bytes:
        lower = text.lower()
        vals = [1.0 if kw in lower else 0.0 for kw in self._KEYWORDS]
        norm = sum(v * v for v in vals) ** 0.5
        if norm > 0:
            vals = [v / norm for v in vals]
        return struct.pack(f"{len(vals)}f", *vals)


@pytest.mark.unit
def test_retrieval_returns_only_active_same_project_cards() -> None:
    ingestion = KnowledgeIngestionService()
    retrieval = KnowledgeRetrievalService()
    ingestion.create_card(
        KnowledgeCardCreate(
            project_id="chatbot",
            knowledge_domain="company_info",
            title="Chatbot company profile",
            content="HTech chatbot supports ecommerce consultation.",
            status="active",
        )
    )
    ingestion.create_card(
        KnowledgeCardCreate(
            project_id="chatbot",
            knowledge_domain="company_info",
            title="Draft profile",
            content="Draft ecommerce consultation should not appear.",
            status="draft",
        )
    )
    ingestion.create_card(
        KnowledgeCardCreate(
            project_id="other",
            knowledge_domain="company_info",
            title="Other project",
            content="Other ecommerce consultation should not appear.",
        )
    )

    results = retrieval.search(
        tenant_id="default",
        project_id="chatbot",
        query="ecommerce consultation",
        limit=5,
    )

    assert len(results) == 1
    assert results[0].title == "Chatbot company profile"


@pytest.mark.unit
def test_retrieval_ranks_title_and_domain_matches() -> None:
    ingestion = KnowledgeIngestionService()
    retrieval = KnowledgeRetrievalService()
    ingestion.create_card(
        KnowledgeCardCreate(
            project_id="chatbot",
            knowledge_domain="pricing_policy",
            title="Gold pricing policy",
            content="The gold plan includes premium support.",
            trust_level=5,
        )
    )
    ingestion.create_card(
        KnowledgeCardCreate(
            project_id="chatbot",
            knowledge_domain="support_process",
            title="Generic support",
            content="Pricing is mentioned once here.",
            trust_level=1,
        )
    )

    results = retrieval.search(
        tenant_id="default",
        project_id="chatbot",
        query="gold pricing policy",
        limit=2,
    )

    assert results[0].title == "Gold pricing policy"
    assert results[0].score > results[1].score


@pytest.mark.unit
def test_retrieval_respects_tenant_isolation() -> None:
    ingestion = KnowledgeIngestionService()
    retrieval = KnowledgeRetrievalService()
    ingestion.create_card(
        KnowledgeCardCreate(
            tenant_id="tenant-a",
            project_id="chatbot",
            knowledge_domain="faq",
            title="Tenant A FAQ",
            content="Refund policy is thirty days.",
        )
    )

    results = retrieval.search(
        tenant_id="tenant-b",
        project_id="chatbot",
        query="refund policy",
        limit=5,
    )

    assert results == []


@pytest.mark.unit
def test_retrieval_uses_semantic_score_when_embeddings_present() -> None:
    embedding = _FakeEmbedding()
    ingestion = KnowledgeIngestionService(embedding_service=embedding)
    retrieval = KnowledgeRetrievalService(embedding_service=embedding)

    ingestion.create_card(
        KnowledgeCardCreate(
            project_id="semantic",
            knowledge_domain="pricing",
            title="Gold plan pricing",
            content="The gold plan costs fifty dollars per month.",
            trust_level=3,
        )
    )
    ingestion.create_card(
        KnowledgeCardCreate(
            project_id="semantic",
            knowledge_domain="general",
            title="Unrelated topic",
            content="This is about something completely different.",
            trust_level=3,
        )
    )

    results = retrieval.search(
        tenant_id="default",
        project_id="semantic",
        query="gold pricing policy",
        limit=5,
    )

    assert len(results) >= 1
    assert results[0].title == "Gold plan pricing"


@pytest.mark.unit
def test_retrieval_falls_back_to_token_when_no_embeddings() -> None:
    ingestion = KnowledgeIngestionService(embedding_service=None)
    retrieval = KnowledgeRetrievalService(embedding_service=None)

    ingestion.create_card(
        KnowledgeCardCreate(
            project_id="fallback",
            knowledge_domain="faq",
            title="Refund policy",
            content="Customers can request refund within thirty days.",
        )
    )

    results = retrieval.search(
        tenant_id="default",
        project_id="fallback",
        query="refund",
        limit=5,
    )

    assert len(results) == 1
    assert "refund" in results[0].content.lower()
