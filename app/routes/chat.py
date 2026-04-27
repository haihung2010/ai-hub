"""POST /v1/chat — main routing endpoint."""

from fastapi import APIRouter, HTTPException, Request, status

from app.models.chat import ChatRequest, ChatResponse
from app.services.ai_service import AIService

router = APIRouter(prefix="/v1", tags=["chat"])


@router.post("/chat", response_model=ChatResponse)
async def chat_endpoint(payload: ChatRequest, request: Request) -> ChatResponse:
    if payload.stream:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Streaming not implemented yet",
        )
    service: AIService = request.app.state.ai_service
    return await service.chat(payload)
