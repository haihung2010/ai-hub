"""Custom API endpoints for AI Hub — exposed as MCP tools via FastApiMCP.

These endpoints are auto-discovered by FastApiMCP since they appear in the OpenAPI schema.
No need for @mcp.tool() — just add routes and they become MCP tools.
"""

from __future__ import annotations

import json
import logging
import subprocess
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/tools", tags=["MCP Tools"])


class StockAnalysisRequest(BaseModel):
    symbol: str
    timeframe: str = "daily"


class StockAnalysisResponse(BaseModel):
    symbol: str
    timeframe: str
    status: str
    note: str


@router.post("/stock-analysis", response_model=StockAnalysisResponse)
async def analyze_stock(req: StockAnalysisRequest):
    """Analyze a Vietnamese stock symbol. Returns analysis request status.
    For full analysis, connect the Doden pipeline."""
    return StockAnalysisResponse(
        symbol=req.symbol.upper(),
        timeframe=req.timeframe,
        status="analysis_requested",
        note=f"Stock {req.symbol.upper()} queued for analysis. Connect doden pipeline for full results.",
    )


class KnowledgeSearchResponse(BaseModel):
    results: list[dict[str, Any]]
    total: int


@router.get("/knowledge-search", response_model=KnowledgeSearchResponse)
async def search_knowledge(
    query: str = Query(..., description="Natural language search query"),
    project_id: str = Query("default", description="Project scope"),
    limit: int = Query(4, ge=1, le=10, description="Max results"),
):
    """Search AI Hub knowledge base with hybrid vector + full-text search."""
    try:
        from app.core.database import get_db_connection
        from app.services.knowledge_embedding_service import KnowledgeEmbeddingService
        from app.services.knowledge_retrieval_service import KnowledgeRetrievalService

        emb = KnowledgeEmbeddingService()
        ret = KnowledgeRetrievalService(embedding_service=emb)
        results = ret.search(
            tenant_id="default",
            project_id=project_id,
            query=query,
            limit=min(limit, 10),
        )
        return KnowledgeSearchResponse(
            results=[
                {
                    "title": r.title,
                    "domain": r.knowledge_domain,
                    "content": r.content[:500],
                    "score": round(r.score, 4),
                    "trust_level": r.trust_level,
                }
                for r in results
            ],
            total=len(results),
        )
    except Exception as e:
        return KnowledgeSearchResponse(results=[], total=0)


class SystemStatusResponse(BaseModel):
    gpu: dict[str, Any] | None = None
    database: dict[str, Any] | None = None
    llama_cpp: str = "unknown"


@router.get("/system-status", response_model=SystemStatusResponse)
async def get_system_status():
    """Get AI Hub system status: GPU, database, llama.cpp."""
    status = SystemStatusResponse()

    # GPU
    try:
        gpu = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        if gpu.returncode == 0:
            parts = gpu.stdout.strip().split(", ")
            status.gpu = {
                "utilization_pct": int(parts[0]),
                "memory_used_mb": int(parts[1]),
                "memory_total_mb": int(parts[2]),
                "temperature_c": int(parts[3]),
            }
    except Exception:
        status.gpu = {"error": "nvidia-smi not available"}

    # Database
    try:
        from app.core.database import get_db_connection
        with get_db_connection() as conn:
            row = conn.execute("SELECT count(*) AS sessions FROM sessions").fetchone()
            status.database = {"total_sessions": row["sessions"]}
    except Exception as e:
        status.database = {"error": str(e)[:200]}

    # llama.cpp
    try:
        r = subprocess.run(["curl", "-fsS", "http://localhost:8080/health"], capture_output=True, timeout=3)
        status.llama_cpp = "running" if r.returncode == 0 else "down"
    except Exception:
        status.llama_cpp = "unknown"

    return status


class DBQueryRequest(BaseModel):
    """DEPRECATED — kept only to avoid breaking old clients. The
    ``/v1/tools/query-database`` endpoint now always returns 410 Gone.

    The previous substring blocklist was bypassable (string literals,
    stacked statements via ``;``) and exposed raw SQL execution to any
    caller with an MCP-visible API key. Use the admin ``/v1/admin/db/*``
    endpoints with proper admin auth instead.
    """

    sql: str
    limit: int = 50


class DBQueryResponse(BaseModel):
    columns: list[str] | None = None
    rows: list[dict[str, Any]] | None = None
    count: int = 0
    error: str | None = None


@router.post("/query-database", response_model=DBQueryResponse)
async def query_database(req: DBQueryRequest):
    """DISABLED 2026-06-08 — substring blocklist was bypassable.

    Returns 410 Gone. Use admin ``/v1/admin/db/*`` with proper auth for
    ad-hoc queries during incident response.
    """
    raise HTTPException(
        status_code=410,
        detail=(
            "query-database tool is disabled due to security. "
            "Use the admin /v1/admin/db/* endpoints with proper admin auth."
        ),
    )
