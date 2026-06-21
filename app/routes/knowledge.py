from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from app.core.database import get_db_connection

from app.core.database import DEFAULT_TENANT_ID
from app.models.knowledge import KnowledgeCardCreate, KnowledgeSearchRequest
from app.services.knowledge_ingestion_service import KnowledgeIngestionService
from app.services.knowledge_retrieval_service import KnowledgeRetrievalService
from app.utils.tenant_guard import resolve_tenant, assert_entity_tenant

router = APIRouter(prefix="/v1/knowledge", tags=["knowledge"])


@router.post("/cards")
async def create_knowledge_card(req: KnowledgeCardCreate, request: Request) -> dict[str, object]:
    # Force the card's tenant to the API key's bound tenant (non-admin
    # clients cannot create cards in a tenant they don't own).
    req.tenant_id = resolve_tenant(request, req.tenant_id)
    ingestion: KnowledgeIngestionService = request.app.state.knowledge_ingestion_service
    card = await ingestion.create_card_async(req)
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
    tenant_id = resolve_tenant(request, tenant_id)
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
    req.tenant_id = resolve_tenant(request, req.tenant_id)
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


def _fetch_card_tenant(card_id: str) -> tuple[str | None, str | None, str | None]:
    """Return (tenant_id, project_id, status) for a card, or (None,…)
    when the card doesn't exist. Used by tenant-aware routes that take
    card_id as a path param.
    """
    with get_db_connection() as conn:
        row = conn.execute(
            "SELECT tenant_id, project_id, status FROM knowledge_cards WHERE id = %s",
            (card_id,),
        ).fetchone()
    if row is None:
        return None, None, None
    return row["tenant_id"], row["project_id"], row["status"]


# ── Knowledge Graph & Related Cards ──────────────────────────────────
from app.services.knowledge_link_service import KnowledgeLinkService

_linker = KnowledgeLinkService()


@router.get("/cards/{card_id}/related")
async def get_related_cards(card_id: str, request: Request, limit: int = 5):
    """Get cards related to the given card. Tenant-scoped via API key."""
    card_tenant, _project_id, _status = _fetch_card_tenant(card_id)
    if card_tenant is None:
        raise HTTPException(status_code=404, detail="Card not found")
    assert_entity_tenant(
        request,
        card_tenant,
        entity_label="Card",
        entity_id=card_id,
    )
    related = _linker.get_related_cards(card_id, limit=limit)
    return {"card_id": card_id, "related": related}


@router.get("/graph")
async def get_knowledge_graph(
    project_id: str,
    request: Request,
    tenant_id: str | None = None,
):
    """Get the knowledge graph for a project. Tenant-scoped if the
    principal has a bound tenant (cross-tenant otherwise)."""
    resolved_tenant = resolve_tenant(request, tenant_id)
    # get_graph does not yet accept a tenant filter; do the filter
    # in this layer to avoid leaking nodes from foreign tenants.
    full = _linker.get_graph(project_id, center_card_id=None, depth=2)
    if resolved_tenant and isinstance(full, dict):
        nodes = full.get("nodes") or []
        edges = full.get("edges") or []
        kept_node_ids = {
            n.get("id") for n in nodes if n.get("tenant_id") == resolved_tenant
        }
        full["nodes"] = [n for n in nodes if n.get("id") in kept_node_ids]
        full["edges"] = [
            e for e in edges
            if e.get("source") in kept_node_ids and e.get("target") in kept_node_ids
        ]
    return full


@router.post("/cards/{card_id}/relink")
async def relink_card(card_id: str, request: Request):
    """Re-generate links for a card. Tenant-scoped via API key."""
    card_tenant, card_project_id, _status = _fetch_card_tenant(card_id)
    if card_tenant is None:
        raise HTTPException(status_code=404, detail="Card not found")
    assert_entity_tenant(
        request,
        card_tenant,
        entity_label="Card",
        entity_id=card_id,
    )
    created = _linker.auto_link_card(card_id, card_project_id)
    return {"card_id": card_id, "links_created": created}


@router.post("/relink")
async def relink_all(
    project_id: str,
    request: Request,
    tenant_id: str | None = None,
):
    """Re-generate links for all cards in a project. Tenant-scoped via API key."""
    tenant_id = resolve_tenant(request, tenant_id)
    with get_db_connection() as conn:
        if tenant_id:
            cards = conn.execute(
                "SELECT id FROM knowledge_cards WHERE project_id = %s AND tenant_id = %s AND status = 'active'",
                (project_id, tenant_id),
            ).fetchall()
        else:
            cards = conn.execute(
                "SELECT id FROM knowledge_cards WHERE project_id = %s AND status = 'active'",
                (project_id,),
            ).fetchall()
    total = 0
    for c in cards:
        total += _linker.auto_link_card(c["id"], project_id)
    return {"project_id": project_id, "cards_processed": len(cards), "links_created": total}
