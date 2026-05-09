"""POST /v1/chat — main routing endpoint, supports streaming (SSE) and non-streaming."""

from __future__ import annotations

import inspect
import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.core.errors import OllamaUnavailable, UpstreamError, UpstreamTimeout, VramExhausted
from app.models.chat import ChatRequest, ChatResponse
from app.services.ai_service import AIService

router = APIRouter(prefix="/v1", tags=["chat"])

_ERROR_CODES = {
    OllamaUnavailable: 503,
    VramExhausted: 503,
    UpstreamTimeout: 504,
    UpstreamError: 502,
}


def _error_code(exc: Exception) -> int:
    for cls, code in _ERROR_CODES.items():
        if isinstance(exc, cls):
            return code
    return 502


@router.post("/chat", response_model=ChatResponse)
async def chat_endpoint(payload: ChatRequest, request: Request) -> ChatResponse | StreamingResponse:
    service: AIService = request.app.state.ai_service
    api_key_tenant = getattr(request.state, "api_key_tenant_id", None)
    if api_key_tenant is not None and api_key_tenant != payload.tenant_id:
        return JSONResponse(status_code=403, content={"detail": "tenant_id mismatch"})
    if getattr(request.state, "api_key_allow_external", True) is False:
        payload.allow_external = False

    if payload.stream:
        api_key_id = getattr(request.state, "api_key_id", None)

        async def event_stream():
            try:
                async for event in service.chat_stream(payload, api_key_id=api_key_id):
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            except (OllamaUnavailable, VramExhausted, UpstreamTimeout, UpstreamError) as exc:
                error_event = {"type": "error", "code": _error_code(exc), "detail": str(exc)}
                yield f"data: {json.dumps(error_event, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    if "api_key_id" in inspect.signature(service.chat).parameters:
        return await service.chat(payload, api_key_id=getattr(request.state, "api_key_id", None))
    return await service.chat(payload)
