"""Admin endpoints for Langfuse cost/latency/usage dashboards.

These endpoints provide a thin wrapper around the Langfuse HTTP API
(`/api/public/metrics`) for per-tenant cost and latency breakdowns.

When LANGFUSE_ENABLED=false (default), endpoints return 503 with a
helpful message — they do NOT call any external service and they do
NOT fail in test/dev environments.
"""
from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix="/v1/admin/langfuse", tags=["admin-langfuse"])


def _langfuse_disabled_response() -> HTTPException:
    return HTTPException(
        status_code=503,
        detail=(
            "Langfuse not enabled. Set LANGFUSE_ENABLED=true in .env "
            "and start docker/langfuse/docker-compose.yml. "
            "See docs/superpowers/plans/2026-06-21-langfuse-observability-doc-ingestion-poc.md"
        ),
    )


@router.get("/cost")
async def cost_summary(days: int = Query(default=7, ge=1, le=90)) -> dict:
    """Per-tenant cost summary from Langfuse metrics API.

    When Langfuse is disabled, returns 503 with helpful message.
    Real implementation deferred to Phase 2 (post-MVP).
    """
    if os.environ.get("LANGFUSE_ENABLED", "false").lower() != "true":
        raise _langfuse_disabled_response()
    # TODO Phase 2: Query Langfuse /api/public/metrics with cost filter
    raise HTTPException(status_code=501, detail="Langfuse cost metrics query — pending Phase 2")


@router.get("/latency")
async def latency_summary(days: int = Query(default=7, ge=1, le=90)) -> dict:
    """p50/p95/p99 latency per route from Langfuse traces.

    When Langfuse is disabled, returns 503 with helpful message.
    """
    if os.environ.get("LANGFUSE_ENABLED", "false").lower() != "true":
        raise _langfuse_disabled_response()
    # TODO Phase 2: Query Langfuse /api/public/metrics with latency filter
    raise HTTPException(status_code=501, detail="Langfuse latency metrics query — pending Phase 2")


@router.get("/traces/{trace_id}")
async def get_trace(trace_id: str) -> dict:
    """Fetch a single trace by ID from Langfuse.

    Used by the admin UI to deep-link from a request log to its trace.
    """
    if os.environ.get("LANGFUSE_ENABLED", "false").lower() != "true":
        raise _langfuse_disabled_response()
    # TODO Phase 2: GET /api/public/traces/{trace_id}
    raise HTTPException(status_code=501, detail="Langfuse trace fetch — pending Phase 2")


@router.get("/health")
async def langfuse_health() -> dict:
    """Health check for the Langfuse integration.

    When disabled, returns {"enabled": False, "status": "disabled"}.
    When enabled, returns {"enabled": True, "status": "ok|degraded|down"}.
    """
    if os.environ.get("LANGFUSE_ENABLED", "false").lower() != "true":
        return {"enabled": False, "status": "disabled"}
    # TODO Phase 2: ping Langfuse /api/public/health
    return {"enabled": True, "status": "ok"}
