from __future__ import annotations

import pytest

from app.models.knowledge import KnowledgeCardCreate
from app.services.knowledge_ingestion_service import KnowledgeIngestionService
from app.services.knowledge_retrieval_service import KnowledgeRetrievalService


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
