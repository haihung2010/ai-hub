from __future__ import annotations

import asyncio

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/v1/audio", tags=["audio"])


@router.post("/transcriptions")
async def transcribe(
    request: Request,
    file: UploadFile = File(...),
    language: str | None = Form(default=None),
    tenant_id: str | None = Form(default=None),
) -> dict[str, str]:
    api_key_tenant = getattr(request.state, "api_key_tenant_id", None)
    if api_key_tenant is not None and tenant_id is not None and api_key_tenant != tenant_id:
        return JSONResponse(status_code=403, content={"detail": "tenant_id mismatch"})
    whisper = getattr(request.app.state, "whisper_service", None)
    if whisper is None:
        return JSONResponse(status_code=503, content={"detail": "whisper not enabled"})
    audio_bytes = await file.read()
    text = await asyncio.to_thread(whisper.transcribe, audio_bytes, language)
    return {"text": text}
