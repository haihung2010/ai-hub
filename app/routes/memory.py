"""GET /v1/memory — inspect user memory context for a project."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request

from app.core.database import DEFAULT_TENANT_ID
from app.services.pinned_memory_service import PinnedMemoryService
from app.services.summary_service import SummaryService
from app.services.user_service import UserService

router = APIRouter(prefix="/v1", tags=["memory"])


@router.get("/memory")
async def get_memory_context(
    request: Request,
    project_id: str = Query(min_length=1, max_length=64),
    user_name: str = Query(min_length=1, max_length=128),
    tenant_id: str = Query(default=DEFAULT_TENANT_ID, min_length=1, max_length=64),
) -> dict[str, object]:
    users: UserService = request.app.state.user_service
    pinned: PinnedMemoryService = request.app.state.pinned_memory_service
    summaries: SummaryService = request.app.state.summary_service

    user = users.find_by_name(user_name, tenant_id)
    if user is None:
        return {
            "user": None,
            "summary": None,
            "pinned_memories": [],
        }

    latest_summary = summaries.get_latest_summary(user.id, project_id, tenant_id)
    memories = pinned.list_memories(tenant_id, project_id, user.id)
    return {
        "user": {"id": user.id, "name": user.name, "tenant_id": user.tenant_id},
        "summary": {"content": latest_summary} if latest_summary else None,
        "pinned_memories": [
            {
                "id": memory.id,
                "scope": memory.scope,
                "key": memory.key,
                "value": memory.value,
                "confidence": memory.confidence,
                "updated_at": memory.updated_at,
            }
            for memory in memories
        ],
    }
