"""User endpoints: session resume and history clear."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from app.core.database import DEFAULT_TENANT_ID
from app.services.history_service import HistoryService
from app.services.user_service import UserService

router = APIRouter(prefix="/v1/users", tags=["users"])


@router.get("/{user_name}/sessions")
async def list_user_sessions(
    user_name: str,
    request: Request,
    project_id: str | None = Query(default=None, min_length=1, max_length=64),
    tenant_id: str = Query(default=DEFAULT_TENANT_ID, min_length=1, max_length=64),
) -> list[dict[str, str | None]]:
    service: UserService = request.app.state.user_service
    user = service.find_by_name(user_name, tenant_id)
    if user is None:
        return []

    sessions = service.find_sessions_for_user(
        user_id=user.id,
        tenant_id=tenant_id,
        project_id=project_id,
    )
    return [
        {
            "session_id": session.id,
            "created_at": session.created_at,
            "last_message_preview": session.last_message_preview,
        }
        for session in sessions
    ]


@router.delete("/{user_name}/history")
async def clear_user_history(
    user_name: str,
    request: Request,
    project_id: str = Query(..., min_length=1, max_length=64),
    tenant_id: str = Query(default=DEFAULT_TENANT_ID, min_length=1, max_length=64),
) -> dict:
    user_service: UserService = request.app.state.user_service
    history_service: HistoryService = request.app.state.history_service

    user = user_service.find_by_name(user_name, tenant_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    cleared = history_service.clear_user_history(
        user_id=user.id,
        project_id=project_id,
        tenant_id=tenant_id,
    )
    return {"cleared_sessions": cleared, "user_name": user_name, "project_id": project_id}
