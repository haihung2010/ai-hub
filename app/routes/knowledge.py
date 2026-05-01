from __future__ import annotations

from fastapi import APIRouter, Query, Request

from app.core.database import DEFAULT_TENANT_ID
from app.models.knowledge import KnowledgeCardCreate, KnowledgeSearchRequest
from app.services.knowledge_ingestion_service import KnowledgeIngestionService
from app.services.knowledge_retrieval_service import KnowledgeRetrievalService

router = APIRouter(prefix="/v1/knowledge", tags=["knowledge"])


@router.post("/cards")
async def create_knowledge_card(req: KnowledgeCardCreate, request: Request) -> dict[str, object]:
    ingestion: KnowledgeIngestionService = request.app.state.knowledge_ingestion_service
    card = ingestion.create_card(req)
    return {"card": _card_to_dict(card)}


@router.get("/cards")
async def list_knowledge_cards(
    request: Request,
    project_id: str = Query(min_length=1, max_length=64),
    tenant_id: str = Query(default=DEFAULT_TENANT_ID, min_length=1, max_length=64),
    knowledge_domain: str | None = Query(default=None, min_length=1, max_length=80),
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, object]:
    ingestion: KnowledgeIngestionService = request.app.state.knowledge_ingestion_service
    cards = ingestion.list_cards(
        tenant_id=tenant_id,
        project_id=project_id,
        knowledge_domain=knowledge_domain,
        status=status,
        limit=limit,
    )
    return {"cards": [_card_to_dict(card) for card in cards]}


@router.post("/search")
async def search_knowledge(req: KnowledgeSearchRequest, request: Request) -> dict[str, object]:
    retrieval: KnowledgeRetrievalService = request.app.state.knowledge_retrieval_service
    results = retrieval.search(
        tenant_id=req.tenant_id,
        project_id=req.project_id,
        query=req.query,
        limit=req.limit,
        knowledge_domain=req.knowledge_domain,
    )
    return {"results": [result.model_dump() for result in results]}


def _card_to_dict(card) -> dict[str, object]:
    return {
        "id": card.id,
        "tenant_id": card.tenant_id,
        "project_id": card.project_id,
        "knowledge_domain": card.knowledge_domain,
        "title": card.title,
        "summary": card.summary,
        "content": card.content,
        "source_type": card.source_type,
        "trust_level": card.trust_level,
        "status": card.status,
        "version": card.version,
        "effective_from": card.effective_from,
        "effective_to": card.effective_to,
        "tags": card.tags,
        "owner": card.owner,
        "created_at": card.created_at,
        "updated_at": card.updated_at,
    }
