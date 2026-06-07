"""FastAPI application factory and exception wiring for the AI Hub."""

from __future__ import annotations

import importlib
import logging
import weakref
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
from app.middleware.security import SecurityMiddleware
from app.routes import admin as admin_routes
from app.routes import chat as chat_routes
from app.routes import crew as crew_routes
from app.routes import health as health_routes
from app.routes import knowledge as knowledge_routes
from app.routes import memory as memory_routes
from app.routes import predictions as predictions_routes
from app.routes import skills as skills_routes
from app.routes import users as users_routes
from app.routes import mcp_tools as mcp_tools_routes
from app.routes import facebook_webhook as fb_webhook_routes
from app.routes import ihi as ihi_routes
from app.agents.crew_service import CrewService
from app.core.database import _get_database_url
from app.services.ai_service import AIService
from app.services.failure_risk_service import FailureRiskService
from app.services.history_service import HistoryService
from app.services.knowledge_embedding_service import KnowledgeEmbeddingService
from app.services.knowledge_ingestion_service import KnowledgeIngestionService
from app.services.knowledge_retrieval_service import KnowledgeRetrievalService
from app.services.rerank_service import RerankService
from app.services.memory_consolidation_service import MemoryConsolidationService
from app.services.memory_extraction_service import MemoryExtractionService
from app.services.memory_retrieval_service import MemoryRetrievalService
from app.services.pinned_memory_service import PinnedMemoryService
from app.services.prediction_service import PredictionService
from app.services.providers.llama_cpp import LlamaCppProvider
from app.services.providers.load_balancer import LlamaCppLoadBalancer
from app.services.providers.openrouter import OpenRouterProvider
from app.services.providers.minimax import MiniMaxProvider
from app.services.providers.gemini import GeminiProvider
from app.services.providers.nine_router import NineRouterProvider
from app.services.structmem_service import StructMemService
from app.services.summary_service import SummaryService
from app.services.tracing_service import is_enabled as langfuse_enabled
from app.services.tracing_service import shutdown as langfuse_shutdown
from app.services.mcp.minimax_websearch import (
    MCPError,
    MiniMaxMCPClient,
    ensure_uvx_installed,
)
from app.services.usage_service import UsageService
from app.services.user_service import UserService
from app.services.scheduler import PeriodicSummarizer
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)


class _SchedulerAIServiceProxy:
    """Adapter so PeriodicSummarizer can call ai_service.summarize via weakref."""
    def __init__(self, ref):
        self._ref = ref

    async def summarize(self, *, text, model_override, user_id, session_id):
        svc = self._ref()
        if svc is None:
            raise RuntimeError("ai_service no longer alive")
        return await svc.summarize(text=text, model_override=model_override, user_id=user_id, session_id=session_id)


class _SchedulerDBProxy:
    """Adapter so PeriodicSummarizer can call db.fetch_all / db.execute."""
    def __init__(self, db_module):
        self._db = db_module

    async def fetch_all(self, sql):
        with self._db.get_db_connection() as conn:
            cur = conn.execute(sql)
            cols = [d.name for d in cur.description] if cur.description else []
            return [dict(zip(cols, row)) for row in cur.fetchall()]

    async def execute(self, sql, *params):
        with self._db.get_db_connection() as conn:
            conn.execute(sql, params)
            conn.commit()


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


_ai_service_instance = None


def get_ai_service():
    """Get the AIService instance (module-level reference set during startup)."""
    return _ai_service_instance


def create_app(
    settings: Settings | None = None,
    limiter=None,
    failure_tracker=None,
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.security_log_file)
    init_db()
    app_start_time = time.time()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        timeout = httpx.Timeout(max(settings.request_timeout_seconds, settings.openrouter_timeout_seconds))
        async with httpx.AsyncClient(timeout=timeout) as client:
            if settings.llama_cpp_nodes:
                _providers = [
                    LlamaCppProvider(client=client, openai_url=f"{node}/v1")
                    for node in settings.llama_cpp_nodes
                ]
                _slots_urls = [f"{node}/slots" for node in settings.llama_cpp_nodes]
                local_provider = LlamaCppLoadBalancer(client=client, providers=_providers, slots_urls=_slots_urls)
                background_provider = None
                ihi_provider = (
                    LlamaCppProvider(client=client, openai_url=settings.ihi_llama_cpp_openai_url)
                    if settings.ihi_llama_cpp_enabled
                    else None
                )
                logger.info("load balancer active: %d nodes %s", len(_providers), settings.llama_cpp_nodes)
            elif settings.background_llama_cpp_enabled:
                local_provider = LlamaCppProvider(client=client, openai_url=settings.llama_cpp_openai_url)
                background_provider = LlamaCppProvider(
                    client=client, openai_url=settings.background_llama_cpp_openai_url
                )
                ihi_provider = (
                    LlamaCppProvider(client=client, openai_url=settings.ihi_llama_cpp_openai_url)
                    if settings.ihi_llama_cpp_enabled
                    else None
                )
                logger.info(
                    "background provider active: primary=%s background=%s ihi=%s",
                    settings.llama_cpp_openai_url,
                    settings.background_llama_cpp_openai_url,
                    settings.ihi_llama_cpp_openai_url if settings.ihi_llama_cpp_enabled else "disabled",
                )
            else:
                local_provider = LlamaCppProvider(client=client, openai_url=settings.llama_cpp_openai_url)
                background_provider = None
                ihi_provider = (
                    LlamaCppProvider(client=client, openai_url=settings.ihi_llama_cpp_openai_url)
                    if settings.ihi_llama_cpp_enabled
                    else None
                )
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
            minimax = (
                MiniMaxProvider(
                    client=client,
                    api_key=settings.minimax_api_key,
                    model=settings.minimax_model,
                    base_url=settings.minimax_base_url,
                    enable_caching=True,
                    timeout_seconds=settings.minimax_timeout_seconds,
                )
                if settings.minimax_enabled
                else None
            )
            gemini = (
                GeminiProvider(
                    client=client,
                    api_key=settings.google_ai_studio_api_key,
                )
                if settings.google_ai_studio_api_key
                else None
            )
            nine_router = (
                NineRouterProvider(
                    client=client,
                    base_url=settings.nine_router_base_url,
                    api_key=settings.nine_router_api_key,
                    model=settings.nine_router_model,
                )
                if settings.nine_router_enabled
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
            knowledge_embedding = KnowledgeEmbeddingService(
                model_name=settings.knowledge_embedding_model,
            )
            knowledge_ingestion = KnowledgeIngestionService(
                chunk_chars=settings.knowledge_chunk_chars,
                chunk_overlap_chars=settings.knowledge_chunk_overlap_chars,
                max_card_chars=settings.knowledge_max_card_chars,
                embedding_service=knowledge_embedding,
            )
            reranker = (
                RerankService(
                    base_url=settings.reranker_url,
                    timeout=settings.reranker_timeout_seconds,
                )
                if settings.reranker_enabled
                else None
            )
            knowledge_retrieval = KnowledgeRetrievalService(
                embedding_service=knowledge_embedding,
                rerank_service=reranker,
            )
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
            # MiniMax WebSearch MCP client (replaces local WebSearchService)
            minimax_mcp_client: MiniMaxMCPClient | None = None
            if (
                settings.minimax_enabled
                and settings.minimax_mcp_enabled
                and settings.minimax_api_key
            ):
                try:
                    uvx_path = ensure_uvx_installed()
                    logger.info("Using uvx at %s", uvx_path)
                    # MCP subprocess expects base URL without /v1 (it appends
                    # the API path internally). The chat provider uses
                    # MINIMAX_BASE_URL with /v1, so strip it for MCP.
                    mcp_base_url = settings.minimax_base_url.rstrip("/")
                    if mcp_base_url.endswith("/v1"):
                        mcp_base_url = mcp_base_url[:-3]
                    minimax_mcp_client = MiniMaxMCPClient(
                        api_key=settings.minimax_api_key,
                        base_url=mcp_base_url,
                        command=settings.minimax_mcp_command,
                        args=settings.minimax_mcp_args,
                        timeout=settings.minimax_mcp_timeout_seconds,
                    )
                    await minimax_mcp_client.start()
                    app.state.minimax_mcp = minimax_mcp_client
                    logger.info("MiniMax MCP client started: %d tools available", 1)
                except Exception as exc:
                    logger.warning("MiniMax MCP disabled (failed to start): %s", exc)
                    app.state.minimax_mcp = None
            else:
                app.state.minimax_mcp = None
                if not settings.minimax_enabled:
                    logger.info("MiniMax cloud disabled; MCP search unavailable")
                elif not settings.minimax_api_key:
                    logger.info("MINIMAX_API_KEY not set; MCP search unavailable")
                else:
                    logger.info("MINIMAX_MCP_ENABLED=false; MCP search unavailable")
            app.state.settings = settings
            app.state.start_time = app_start_time
            app.state.local_provider = local_provider
            app.state.openrouter_provider = openrouter
            # Cloud fallback: prefer MiniMax (M3 + prompt caching) when
            # enabled; fall back to OpenRouter otherwise.
            if minimax is not None:
                app.state.minimax_provider = minimax
                app.state.cloud = minimax
                logger.info(
                    "cloud provider: MiniMax M3 (model=%s, caching=on)",
                    settings.minimax_model,
                )
            elif openrouter is not None:
                app.state.cloud = openrouter
                logger.info(
                    "cloud provider: OpenRouter (model=%s)",
                    settings.openrouter_model,
                )
            else:
                app.state.cloud = None
                logger.info("cloud provider: disabled")
            app.state.gemini_provider = gemini
            app.state.history_service = history
            app.state.user_service = users
            app.state.summary_service = summaries
            app.state.prediction_service = predictions
            app.state.memory_extraction_service = memory_extraction
            app.state.memory_retrieval_service = memory_retrieval
            app.state.memory_consolidation_service = memory_consolidation
            app.state.pinned_memory_service = pinned_memory
            app.state.knowledge_ingestion_service = knowledge_ingestion
            app.state.knowledge_retrieval_service = knowledge_retrieval
            app.state.rerank_service = reranker
            app.state.usage_service = usage
            app.state.failure_risk_service = failure_risk
            app.state.structmem_service = structmem
            app.state.crew_service = (
                CrewService(settings, _get_database_url())
                if settings.enable_crew_agents
                else None
            )
            app.state.ai_service = AIService(
                local=local_provider,
                history=history,
                settings=settings,
                users=users,
                summaries=summaries,
                minimax_mcp=app.state.minimax_mcp,
                memory_retrieval=memory_retrieval,
                structmem=structmem,
                predictions=predictions,
                pinned_memory=pinned_memory,
                cloud=app.state.cloud,
                usage=usage,
                failure_risk=failure_risk,
                knowledge_retrieval=knowledge_retrieval,
                background_local=background_provider,
                gemini=gemini,
                nine_router=nine_router,
                ihi=ihi_provider,
            )
            app.state.ai_service_ref = weakref.ref(app.state.ai_service)
            # Adaptive routing: APScheduler for periodic IHI rollups (added 2026-06-07)
            if settings.adaptive_routing_enabled:
                try:
                    scheduler = AsyncIOScheduler()
                    db_module = importlib.import_module("app.core.database")
                    summarizer = PeriodicSummarizer(
                        ai_service=_SchedulerAIServiceProxy(app.state.ai_service_ref),
                        db=_SchedulerDBProxy(db_module),
                        min_tokens=settings.periodic_summary_min_tokens,
                        window_hours=6,
                    )
                    scheduler.add_job(
                        summarizer.rollup_once,
                        CronTrigger.from_crontab(settings.periodic_summary_cron),
                        id="ihi_rollup",
                        replace_existing=True,
                    )
                    scheduler.start()
                    app.state.scheduler = scheduler
                    logger.info("periodic summary scheduler started: cron=%s", settings.periodic_summary_cron)
                except Exception as e:
                    logger.warning("failed to start periodic summary scheduler: %s", e)
            logger.info("ai-hub started on port %s", settings.app_port)
            logger.info(
                "failure_risk mode: log_only=%s enable_actions=%s "
                "enable_search_action=%s high_threshold=%.2f medium_threshold=%.2f",
                settings.failure_risk_log_only,
                settings.failure_risk_enable_actions,
                settings.failure_risk_enable_search_action,
                settings.failure_risk_high_threshold,
                settings.failure_risk_medium_threshold,
            )
            if settings.failure_risk_log_only or not settings.failure_risk_enable_actions:
                logger.warning(
                    "failure_risk actions are DISABLED — risk events will be "
                    "recorded but not applied. To enable, set "
                    "FAILURE_RISK_LOG_ONLY=false and FAILURE_RISK_ENABLE_ACTIONS=true. "
                    "See GET /v1/admin/risk/gap for the action gap."
                )

            # Store module-level reference for get_ai_service() callers
            global _ai_service_instance
            _ai_service_instance = app.state.ai_service
            if langfuse_enabled():
                logger.info("Langfuse tracing active")
            yield
            # Stop MiniMax MCP subprocess on shutdown
            if hasattr(app.state, "minimax_mcp") and app.state.minimax_mcp is not None:
                try:
                    await app.state.minimax_mcp.stop()
                    logger.info("MiniMax MCP client stopped")
                except Exception as exc:
                    logger.warning("MiniMax MCP stop failed: %s", exc)
            if hasattr(app.state, "scheduler"):
                try:
                    app.state.scheduler.shutdown(wait=False)
                    logger.info("periodic summary scheduler stopped")
                except Exception as e:
                    logger.warning("scheduler shutdown failed: %s", e)
            # Graceful shutdown — flush pending Langfuse spans so a clean
            # exit does not drop the last few traces.
            langfuse_shutdown()
            # Drain the security-audit writer so the last few rate-limit
            # / auth-failure denials are not lost on shutdown.
            from app.services.security_audit import shutdown as security_audit_shutdown
            security_audit_shutdown(wait=True)

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
    app.include_router(knowledge_routes.router)
    app.include_router(predictions_routes.router)
    app.include_router(crew_routes.router)
    app.include_router(admin_routes.router)
    app.include_router(mcp_tools_routes.router)
    app.include_router(fb_webhook_routes.router)
    app.include_router(skills_routes.router)
    app.include_router(ihi_routes.router)
    # MCP server: expose all API endpoints as MCP tools
    try:
        from fastapi_mcp import FastApiMCP
        mcp = FastApiMCP(
            app,
            name="AI Hub",
            description="AI Hub: chat, knowledge RAG, admin, stock analysis tools via MCP",
        )
        mcp.mount_http()
        logger.info("MCP server mounted at /mcp (HTTP transport)")
    except Exception as exc:
        logger.warning("MCP server failed to mount: %s", exc)
    # Add no-cache middleware for admin static files
    from starlette.middleware.base import BaseHTTPMiddleware
    class NoCacheAdminMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            response = await call_next(request)
            if request.url.path.startswith("/admin") or ".v2." in request.url.path:
                response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
                response.headers["Pragma"] = "no-cache"
                response.headers["Expires"] = "0"
            return response
    app.add_middleware(NoCacheAdminMiddleware)

    app.mount("/", StaticFiles(directory="static", html=True), name="static")
    return app


app = create_app()
