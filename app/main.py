"""FastAPI application factory and exception wiring for the AI Hub."""

from __future__ import annotations

import logging
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
    UpstreamError,
    UpstreamTimeout,
    VramExhausted,
)
from app.core.logging import configure_logging
from app.middleware.security import SecurityMiddleware
from app.routes import chat as chat_routes
from app.routes import crew as crew_routes
from app.routes import health as health_routes
from app.routes import users as users_routes
from app.agents.crew_service import CrewService
from app.core.database import DB_PATH
from app.services.ai_service import AIService
from app.services.history_service import HistoryService
from app.services.memory_extraction_service import MemoryExtractionService
from app.services.memory_retrieval_service import MemoryRetrievalService
from app.services.providers.ollama import OllamaProvider
from app.services.structmem_service import StructMemService
from app.services.summary_service import SummaryService
from app.services.tools.web_search_service import WebSearchService
from app.services.user_service import UserService

logger = logging.getLogger(__name__)


def _register_exception_handlers(app: FastAPI) -> None:
    async def _json(status: int, detail: str) -> JSONResponse:
        return JSONResponse(status_code=status, content={"detail": detail})

    @app.exception_handler(ProjectNotFound)
    async def _project_not_found(_: Request, exc: ProjectNotFound) -> JSONResponse:
        return await _json(404, f"project not found: {exc}")

    @app.exception_handler(OllamaUnavailable)
    async def _ollama_unavailable(_: Request, exc: OllamaUnavailable) -> JSONResponse:
        return await _json(503, f"ollama unavailable: {exc}")

    @app.exception_handler(VramExhausted)
    async def _vram(_: Request, exc: VramExhausted) -> JSONResponse:
        return await _json(503, f"vram exhausted: {exc}")

    @app.exception_handler(UpstreamTimeout)
    async def _timeout(_: Request, exc: UpstreamTimeout) -> JSONResponse:
        return await _json(504, f"upstream timeout: {exc}")

    @app.exception_handler(UpstreamError)
    async def _upstream(_: Request, exc: UpstreamError) -> JSONResponse:
        return await _json(502, f"upstream error: {exc}")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.security_log_file)
    init_db()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        timeout = httpx.Timeout(settings.request_timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout) as client:
            ollama = OllamaProvider(
                client=client,
                openai_url=settings.ollama_openai_url,
                native_url=settings.ollama_base_url,
            )
            history = HistoryService()
            users = UserService()
            summaries = SummaryService(history=history)
            memory_extraction = MemoryExtractionService()
            memory_retrieval = MemoryRetrievalService()
            structmem = StructMemService(history=history, extraction=memory_extraction)
            web_search = WebSearchService(
                timeout_seconds=settings.web_search_timeout_seconds,
                google_api_key=settings.google_api_key,
                google_search_cx=settings.google_search_cx,
            )
            app.state.ollama_provider = ollama
            app.state.history_service = history
            app.state.user_service = users
            app.state.summary_service = summaries
            app.state.memory_extraction_service = memory_extraction
            app.state.memory_retrieval_service = memory_retrieval
            app.state.structmem_service = structmem
            app.state.web_search_service = web_search
            app.state.crew_service = (
                CrewService(settings, str(DB_PATH))
                if settings.enable_crew_agents
                else None
            )
            app.state.ai_service = AIService(
                local=ollama,
                history=history,
                settings=settings,
                users=users,
                summaries=summaries,
                web_search=web_search,
                memory_retrieval=memory_retrieval,
                structmem=structmem,
            )
            logger.info("ai-hub started on port %s", settings.app_port)
            yield

    app = FastAPI(
        title="AI Hub",
        version=__version__,
        description="Central router for per-project AI chat (Ollama local).",
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
    app.add_middleware(SecurityMiddleware, settings=settings)

    @app.get("/")
    async def index():
        return FileResponse("static/index.html")

    app.include_router(health_routes.router)
    app.include_router(chat_routes.router)
    app.include_router(users_routes.router)
    app.include_router(crew_routes.router)
    app.mount("/", StaticFiles(directory="static", html=True), name="static")
    return app


app = create_app()
