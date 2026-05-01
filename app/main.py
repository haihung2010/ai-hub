"""FastAPI application factory and exception wiring for the AI Hub."""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.core.config import Settings, get_settings
from app.core.database import init_db
from app.core.errors import (
    OllamaUnavailable,
    ProjectNotFound,
    SessionAccessDenied,
    UpstreamError,
    UpstreamTimeout,
    VramExhausted,
)
from app.core.logging import configure_logging
from app.middleware.security import (
    AuthFailureTracker,
    InMemoryRateLimiter,
    SecurityMiddleware,
    SqliteFailureTracker,
    SqliteRateLimiter,
)
from app.routes import admin as admin_routes
from app.routes import chat as chat_routes
from app.routes import crew as crew_routes
from app.routes import health as health_routes
from app.routes import memory as memory_routes
from app.routes import predictions as predictions_routes
from app.routes import users as users_routes
from app.agents.crew_service import CrewService
from app.core.database import DB_PATH
from app.services.ai_service import AIService
from app.services.failure_risk_service import FailureRiskService
from app.services.history_service import HistoryService
from app.services.memory_consolidation_service import MemoryConsolidationService
from app.services.memory_extraction_service import MemoryExtractionService
from app.services.memory_retrieval_service import MemoryRetrievalService
from app.services.pinned_memory_service import PinnedMemoryService
from app.services.prediction_service import PredictionService
from app.services.providers.llama_cpp import LlamaCppProvider
from app.services.providers.openrouter import OpenRouterProvider
from app.services.structmem_service import StructMemService
from app.services.summary_service import SummaryService
from app.services.tools.web_search_service import WebSearchService
from app.services.usage_service import UsageService
from app.services.user_service import UserService

logger = logging.getLogger(__name__)


def _register_exception_handlers(app: FastAPI) -> None:
    async def _json(status: int, detail: str) -> JSONResponse:
        return JSONResponse(status_code=status, content={"detail": detail})

    @app.exception_handler(ProjectNotFound)
    async def _project_not_found(_: Request, exc: ProjectNotFound) -> JSONResponse:
        return await _json(404, f"project not found: {exc}")

    @app.exception_handler(SessionAccessDenied)
    async def _session_access_denied(_: Request, exc: SessionAccessDenied) -> JSONResponse:
        return await _json(403, f"session access denied: {exc}")

    @app.exception_handler(OllamaUnavailable)
    async def _local_unavailable(_: Request, exc: OllamaUnavailable) -> JSONResponse:
        return await _json(503, f"local provider unavailable: {exc}")

    @app.exception_handler(VramExhausted)
    async def _vram(_: Request, exc: VramExhausted) -> JSONResponse:
        return await _json(503, f"vram exhausted: {exc}")

    @app.exception_handler(UpstreamTimeout)
    async def _timeout(_: Request, exc: UpstreamTimeout) -> JSONResponse:
        return await _json(504, f"upstream timeout after retry: {exc}. Please try again; your prompt was not saved as answered.")

    @app.exception_handler(UpstreamError)
    async def _upstream(_: Request, exc: UpstreamError) -> JSONResponse:
        return await _json(502, f"upstream error: {exc}")


def create_app(
    settings: Settings | None = None,
    limiter: InMemoryRateLimiter | SqliteRateLimiter | None = None,
    failure_tracker: AuthFailureTracker | SqliteFailureTracker | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.security_log_file)
    init_db()
    app_start_time = time.time()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        timeout = httpx.Timeout(max(settings.request_timeout_seconds, settings.openrouter_timeout_seconds))
        async with httpx.AsyncClient(timeout=timeout) as client:
            local_provider = LlamaCppProvider(client=client, openai_url=settings.llama_cpp_openai_url)
            openrouter = (
                OpenRouterProvider(
                    client=client,
                    base_url=settings.openrouter_base_url,
                    api_key=settings.openrouter_api_key,
                    fallback_models=settings.openrouter_fallback_models,
                )
                if settings.openrouter_enabled
                else None
            )
            history = HistoryService()
            users = UserService()
            summaries = SummaryService(history=history, max_concurrency=settings.summary_concurrency)
            predictions = PredictionService()
            memory_extraction = MemoryExtractionService()
            memory_retrieval = MemoryRetrievalService()
            memory_consolidation = MemoryConsolidationService()
            pinned_memory = PinnedMemoryService()
            usage = UsageService()
            failure_risk = FailureRiskService(
                high_threshold=settings.failure_risk_high_threshold,
                medium_threshold=settings.failure_risk_medium_threshold,
            )
            structmem = StructMemService(
                history=history,
                extraction=memory_extraction,
                consolidation=memory_consolidation,
            )
            web_search = WebSearchService(
                timeout_seconds=settings.web_search_timeout_seconds,
                google_api_key=settings.google_api_key,
                google_search_cx=settings.google_search_cx,
            )
            app.state.settings = settings
            app.state.start_time = app_start_time
            app.state.local_provider = local_provider
            app.state.openrouter_provider = openrouter
            app.state.history_service = history
            app.state.user_service = users
            app.state.summary_service = summaries
            app.state.prediction_service = predictions
            app.state.memory_extraction_service = memory_extraction
            app.state.memory_retrieval_service = memory_retrieval
            app.state.memory_consolidation_service = memory_consolidation
            app.state.pinned_memory_service = pinned_memory
            app.state.usage_service = usage
            app.state.failure_risk_service = failure_risk
            app.state.structmem_service = structmem
            app.state.web_search_service = web_search
            app.state.crew_service = (
                CrewService(settings, str(DB_PATH))
                if settings.enable_crew_agents
                else None
            )
            app.state.ai_service = AIService(
                local=local_provider,
                history=history,
                settings=settings,
                users=users,
                summaries=summaries,
                web_search=web_search,
                memory_retrieval=memory_retrieval,
                structmem=structmem,
                predictions=predictions,
                pinned_memory=pinned_memory,
                cloud=openrouter,
                usage=usage,
                failure_risk=failure_risk,
            )
            logger.info("ai-hub started on port %s", settings.app_port)
            yield

    app = FastAPI(
        title="AI Hub",
        version=__version__,
        description="Central router for per-project AI chat (local inference backend).",
        lifespan=lifespan,
    )
    _register_exception_handlers(app)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["X-API-KEY", "Content-Type"],
    )
    app.add_middleware(SecurityMiddleware, settings=settings, limiter=limiter, failure_tracker=failure_tracker)

    @app.get("/")
    async def index():
        return FileResponse("static/index.html")

    app.include_router(health_routes.router)
    app.include_router(chat_routes.router)
    app.include_router(users_routes.router)
    app.include_router(memory_routes.router)
    app.include_router(predictions_routes.router)
    app.include_router(crew_routes.router)
    app.include_router(admin_routes.router)
    app.mount("/", StaticFiles(directory="static", html=True), name="static")
    return app


app = create_app()
