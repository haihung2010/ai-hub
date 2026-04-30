"""GET / liveness + GET /health readiness for configured AI providers."""

from fastapi import APIRouter, Request

from app.core.errors import AIHubError

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
    local = request.app.state.local_provider
    models: list[str] = []
    local_status = "ok"
    try:
        models = await local.list_models()
    except AIHubError as exc:
        local_status = f"unavailable: {exc}"

    overall = "ok" if local_status == "ok" else "degraded"
    return {
        "status": overall,
        "local": {
            "name": local.name,
            "status": local_status,
            "models": models,
        },
    }
