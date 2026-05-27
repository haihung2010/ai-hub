from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from app.core.database import get_db_connection

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


# ── Knowledge Graph & Related Cards ──────────────────────────────────
from app.services.knowledge_link_service import KnowledgeLinkService

_linker = KnowledgeLinkService()


@router.get("/cards/{card_id}/related")
async def get_related_cards(card_id: str, limit: int = 5):
    """Get cards related to the given card."""
    related = _linker.get_related_cards(card_id, limit=limit)
    return {"card_id": card_id, "related": related}


@router.get("/graph")
async def get_knowledge_graph(project_id: str):
    """Get the knowledge graph for a project."""
    graph = _linker.get_graph(project_id)
    return graph


@router.post("/cards/{card_id}/relink")
async def relink_card(card_id: str):
    """Re-generate links for a card."""
    with get_db_connection() as conn:
        card = conn.execute(
            "SELECT project_id FROM knowledge_cards WHERE id = %s", (card_id,)
        ).fetchone()
    if not card:
        raise HTTPException(status_code=404, detail="Card not found")
    created = _linker.auto_link_card(card_id, card["project_id"])
    return {"card_id": card_id, "links_created": created}


@router.post("/relink")
async def relink_all(project_id: str):
    """Re-generate links for all cards in a project."""
    with get_db_connection() as conn:
        cards = conn.execute(
            "SELECT id FROM knowledge_cards WHERE project_id = %s AND status = 'active'",
            (project_id,),
        ).fetchall()
    total = 0
    for c in cards:
        total += _linker.auto_link_card(c["id"], project_id)
    return {"project_id": project_id, "cards_processed": len(cards), "links_created": total}
