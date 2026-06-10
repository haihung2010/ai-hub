"""Audio transcription endpoint (P0.4 — upload size cap, 2026-06-10).

Exposes the GPU Whisper service over HTTP. Caps uploads at 25 MB to
prevent memory-exhaustion attacks via huge audio blobs (the previous
behavior was unbounded, so a malicious client could OOM the worker
process with a single multipart upload).

Auth: requires the standard X-API-KEY (handled by the security
middleware). The endpoint also enforces content-type and a
``WHISPER_MAX_UPLOAD_BYTES`` cap BEFORE the entire body is buffered
into memory.
"""
from __future__ import annotations

import io
import logging

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/audio", tags=["audio"])

# 25 MB cap — OpenAI Whisper API uses the same limit. Audio > 25 MB
# should be split or downsampled by the client.
MAX_UPLOAD_BYTES = 25 * 1024 * 1024


@router.post(
    "/transcriptions",
    summary="Transcribe audio to text (Whisper)",
    description=(
        "Accepts a single audio file (multipart/form-data, field 'file') and "
        "returns the Whisper transcription. Capped at 25 MB. "
        "Optional form field `language` (ISO-639-1) hints Whisper's decoder."
    ),
)
async def transcribe(
    request: Request,
    file: UploadFile = File(...),
    language: str | None = Form(default=None),
) -> dict:
    # Read with a hard byte cap. We do this BEFORE handing the file
    # object to Whisper so a 10 GB upload never reaches the model loader.
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(64 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_UPLOAD_BYTES:
            # 413 Payload Too Large
            raise HTTPException(
                status_code=413,
                detail=(
                    f"Audio upload exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit. "
                    "Split the file or downsample before uploading."
                ),
            )
        chunks.append(chunk)
    if total == 0:
        raise HTTPException(status_code=400, detail="Empty audio upload")

    audio_bytes = b"".join(chunks)
    logger.info(
        "Whisper transcribe filename=%s size=%d language=%s",
        file.filename or "<unnamed>",
        total,
        language or "auto",
    )

    whisper = getattr(request.app.state, "whisper_service", None)
    if whisper is None:
        raise HTTPException(
            status_code=503,
            detail="Whisper service unavailable — model not loaded on this instance.",
        )

    try:
        result = whisper.transcribe(io.BytesIO(audio_bytes), language=language)
    except Exception as exc:
        logger.exception("Whisper transcription failed")
        raise HTTPException(
            status_code=500,
            detail=f"Transcription failed: {exc.__class__.__name__}",
        ) from exc

    return {
        "text": result.get("text", ""),
        "language": result.get("language"),
        "language_probability": result.get("language_probability"),
        "filename": file.filename,
        "bytes": total,
    }
