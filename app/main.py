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
from app.routes import chatwoot_webhook as chatwoot_routes
from app.routes import a2a as a2a_routes
from app.routes import audio as audio_routes
from app.routes import orders as orders_routes
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


async def _rotation_reminder_job() -> None:
    """P2.4 (2026-06-10): daily reminder for stale API keys + env-managed secrets.

    Reads api_keys rows whose last_rotated_at is older than 90 days
    and logs a WARNING for each. Also pings the operator about the
    MiniMax + webhook HMAC + DB password rotation deadlines (those
    live in env, not in api_keys, so they're a "did you remember"
    reminder, not a per-row check).
    """
    from app.services.api_key_service import (
        ApiKeyService,
        DEFAULT_API_KEY_ROTATION_DAYS,
        DEFAULT_DB_PASSWORD_ROTATION_DAYS,
        DEFAULT_MINIMAX_KEY_ROTATION_DAYS,
        DEFAULT_WEBHOOK_HMAC_ROTATION_DAYS,
    )
    try:
        stale = ApiKeyService().get_rotation_status(
            rotation_days=DEFAULT_API_KEY_ROTATION_DAYS
        )
    except Exception:
        logger.exception("key rotation reminder: get_rotation_status failed")
        return
    if not stale:
        logger.info(
            "key rotation reminder: all %d-day API keys are fresh",
            DEFAULT_API_KEY_ROTATION_DAYS,
        )
        return
    for k in stale:
        logger.warning(
            "key rotation reminder: api_key id=%s name=%s tenant=%s "
            "days_since_rotation=%s (deadline=%sd)",
            k["id"], k["name"], k["tenant_id"],
            k["days_since_rotation"], DEFAULT_API_KEY_ROTATION_DAYS,
        )
    # Env-managed secrets — check presence and warn if env var is set
    # but we have no record of when it was set.
    import os
    env_secrets = {
        "MINIMAX_API_KEY": ("minimax", DEFAULT_MINIMAX_KEY_ROTATION_DAYS),
        "CHATWOOT_WEBHOOK_SECRET": ("webhook", DEFAULT_WEBHOOK_HMAC_ROTATION_DAYS),
        "DB password (DATABASE_URL)": ("db", DEFAULT_DB_PASSWORD_ROTATION_DAYS),
    }
    for env_name, (kind, days) in env_secrets.items():
        # We can only see whether the env var is set; the rotation
        # date has to be tracked out-of-band (e.g. a sticky note in
        # the ops runbook). Just log a periodic "did you rotate?"
        # reminder.
        if kind == "minimax" and not os.environ.get(env_name):
            continue
        if kind == "webhook" and not os.environ.get(env_name):
            continue
        logger.warning(
            "key rotation reminder: %s (env-managed) — verify it was "
            "rotated within the last %d days. See docs/security/secret-rotation.md",
            env_name, days,
        )


async def _gdpr_hard_delete_sweep() -> None:
    """P2.6 (2026-06-10): hard-delete users whose 30-day grace has expired.

    Runs daily at 03:13. Lists pending deletions whose
    scheduled_for is in the past and calls hard_delete_user on
    each. Failures are logged, not propagated, so one bad user
    doesn't block the rest of the sweep.
    """
    from app.services.gdpr_service import (
        hard_delete_user,
        list_pending_deletions,
    )
    try:
        pending = list_pending_deletions(limit=500)
    except Exception:
        logger.exception("gdpr sweep: list_pending_deletions failed")
        return
    if not pending:
        logger.info("gdpr sweep: no pending deletions")
        return
    for row in pending:
        if row["gdpr_delete_scheduled_for"] is None:
            continue
        try:
            summary = hard_delete_user(row["user_id"])
            logger.info(
                "gdpr sweep: hard-deleted user_id=%s name=%s summary=%s",
                row["user_id"], row["name"], summary,
            )
        except Exception:
            logger.exception("gdpr sweep: hard_delete_user failed for %s", row["user_id"])


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
    app_start_time = time.time()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Run schema init off the event loop. init_db() opens a sync
        # psycopg connection and executes DDL — moving it here avoids
        # blocking the import chain (and unit tests that import app.main
        # without ever starting the server) and lets the FastAPI app
        # object be constructed cleanly even if the DB is briefly down.
        import asyncio
        try:
            await asyncio.to_thread(init_db)
        except Exception as exc:
            logger.error("init_db failed during startup: %s", exc)
            raise
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
            # Whisper service (P0.4 — gated by ENABLE_WHISPER; lazy-loaded model)
            try:
                from app.services.whisper_service import WhisperService
                app.state.whisper_service = (
                    WhisperService(model_size=settings.whisper_model_size)
                    if settings.enable_whisper
                    else None
                )
            except Exception as exc:
                logger.warning("Whisper service init failed: %s", exc)
                app.state.whisper_service = None
            # P1.6 — webhook idempotency (Redis SETNX, in-memory fallback)
            try:
                from app.middleware.webhook_idempotency import make_idempotency
                app.state.webhook_idempotency = make_idempotency()
            except Exception as exc:
                logger.warning("Webhook idempotency init failed: %s", exc)
                app.state.webhook_idempotency = None
            # P3.2 — per-model_mode rate limit (Lite/Normal/External)
            try:
                from app.middleware.model_mode_rate_limit import make_model_mode_rate_limiter
                app.state.model_mode_rate_limiter = make_model_mode_rate_limiter()
            except Exception as exc:
                logger.warning("model_mode rate limiter init failed: %s", exc)
                app.state.model_mode_rate_limiter = None
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
            scheduler: AsyncIOScheduler | None = None
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
                    logger.info("periodic summary scheduler started: cron=%s", settings.periodic_summary_cron)
                except Exception as e:
                    logger.warning("failed to start periodic summary scheduler: %s", e)
                    scheduler = None

            # P2.4 (2026-06-10) — secret rotation reminder.
            # Independent of adaptive_routing_enabled so that operators
            # who disabled adaptive routing still get the reminder.
            # Runs daily at 09:07 local time.
            try:
                if scheduler is None:
                    scheduler = AsyncIOScheduler()
                scheduler.add_job(
                    _rotation_reminder_job,
                    CronTrigger.from_crontab("7 9 * * *"),
                    id="key_rotation_reminder",
                    replace_existing=True,
                )
                # P2.6 (2026-06-10) — GDPR hard-delete sweep.
                # Runs daily at 03:13 (off-peak) to delete any user
                # whose gdpr_delete_scheduled_for has passed.
                scheduler.add_job(
                    _gdpr_hard_delete_sweep,
                    CronTrigger.from_crontab("13 3 * * *"),
                    id="gdpr_hard_delete_sweep",
                    replace_existing=True,
                )
                if not scheduler.running:
                    scheduler.start()
                app.state.scheduler = scheduler
                logger.info("key rotation reminder scheduler started (daily 09:07)")
                logger.info("gdpr hard-delete sweep scheduler started (daily 03:13)")
            except Exception as e:
                logger.warning("failed to start key rotation reminder scheduler: %s", e)
            logger.info("ai-hub started on port %s", settings.app_port)
            # P1.5-followup (2026-06-12) — emit memory budget sanity check
            from app.core.config import validate_memory_budget
            validate_memory_budget(settings)
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
    # P1.5 — security headers (X-Content-Type-Options, HSTS, CSP, ...)
    from app.middleware.security_headers import SecurityHeadersMiddleware
    app.add_middleware(SecurityHeadersMiddleware)
    # P1.3 — request context (binds request_id to structlog)
    from app.middleware.request_context import RequestContextMiddleware
    app.add_middleware(RequestContextMiddleware)
    # P3.4 — CSRF for browser-facing flows (admin HTML, /v1/admin/*)
    from app.middleware.csrf import CSRFMiddleware
    app.add_middleware(CSRFMiddleware, enabled=settings.csrf_enabled)

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
    app.include_router(chatwoot_routes.router)
    app.include_router(a2a_routes.router)
    app.include_router(audio_routes.router)
    app.include_router(orders_routes.router)
    # P2.1 — OAuth 2.1 Client Credentials grant
    from app.routes import oauth as oauth_routes
    app.include_router(oauth_routes.router)
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


# Module-level app for `uvicorn app.main:app`. Built once at import
# time using the default Settings. Tests do NOT go through this
# code path — they build their own app via `create_app(settings=...)`
# in the `client` fixture, so CSRF_ENABLED etc. can be controlled
# per-test via the env. (P3.4 — keep this OUT of the test-time
# import chain so the autouse fixture's monkeypatch wins.)
app = create_app()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=app.state.settings.app_port if hasattr(app.state, "settings") else 8000,
        log_level="info",
    )
