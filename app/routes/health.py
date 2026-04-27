"""GET / liveness + GET /health readiness with Ollama model enumeration."""

from fastapi import APIRouter, Request

from app.core.errors import AIHubError
from app.services.providers.ollama import OllamaProvider

router = APIRouter(tags=["health"])


@router.get("/")
async def root(request: Request) -> dict[str, object]:
    return {
        "service": "ai-hub",
        "version": request.app.version,
        "status": "ok",
    }


@router.get("/health")
async def health(request: Request) -> dict[str, object]:
    ollama: OllamaProvider = request.app.state.ollama_provider
    models: list[str] = []
    ollama_status = "ok"
    try:
        models = await ollama.list_models()
    except AIHubError as exc:
        ollama_status = f"unavailable: {exc}"

    overall = "ok" if ollama_status == "ok" else "degraded"
    return {
        "status": overall,
        "ollama": {
            "status": ollama_status,
            "models": models,
        },
    }
