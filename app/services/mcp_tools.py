"""Custom MCP tools for AI Hub — stock analysis, knowledge search, system info.

These are registered alongside the auto-generated FastAPI MCP tools.
Add to app/main.py after FastApiMCP mount.
"""

from __future__ import annotations

import json
import logging
import subprocess
from typing import Any

from fastapi import FastAPI
from fastapi_mcp import FastApiMCP

logger = logging.getLogger(__name__)


def create_mcp_with_custom_tools(app: FastAPI) -> FastApiMCP | None:
    """Create and mount MCP server with both auto-generated and custom tools."""
    try:
        mcp = FastApiMCP(
            app,
            name="AI Hub",
            description="AI Hub: chat, knowledge RAG, admin, stock analysis tools via MCP",
            exclude_operations=[
                "health_check", "read_index", "read_admin", "read_chat",
                "open", "static_handler",
            ],
        )

        # ── Custom tool: Stock Analysis ──
        @mcp.tool()
        def analyze_stock(symbol: str, timeframe: str = "daily") -> dict[str, Any]:
            """Analyze a Vietnamese stock symbol using Doden analysis pipeline.
            
            Args:
                symbol: Stock ticker (e.g., 'FPT', 'VHM', 'STB')
                timeframe: Analysis timeframe ('daily', 'weekly', 'monthly')
            
            Returns:
                Analysis results with technical indicators and recommendations.
            """
            try:
                result = subprocess.run(
                    ["/home/hung/ai-hub/venv/bin/python3", "-c", f"""
import sys
sys.path.insert(0, '/home/hung/anti_doc/doden')
# Placeholder — integrate with actual doden when available
print(json.dumps({{"symbol": "{symbol}", "timeframe": "{timeframe}", "status": "analysis_requested", "note": "Connect doden pipeline for full analysis"}}))
"""],
                    capture_output=True, text=True, timeout=30,
                )
                return json.loads(result.stdout) if result.returncode == 0 else {
                    "error": result.stderr[:500]
                }
            except Exception as e:
                return {"error": str(e)}

        # ── Custom tool: Knowledge Search ──
        @mcp.tool()
        def search_knowledge(
            query: str,
            project_id: str = "default",
            limit: int = 4,
        ) -> dict[str, Any]:
            """Search the AI Hub knowledge base using hybrid vector + full-text search.
            
            Args:
                query: Search query in natural language
                project_id: Project scope (e.g., 'vehix', 'test', 'chatbot')
                limit: Max results to return (1-10)
            
            Returns:
                Matching knowledge chunks with scores and metadata.
            """
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
                return {
                    "results": [
                        {
                            "title": r.title,
                            "domain": r.knowledge_domain,
                            "content": r.content[:500],
                            "score": round(r.score, 4),
                            "trust_level": r.trust_level,
                        }
                        for r in results
                    ],
                    "total": len(results),
                }
            except Exception as e:
                return {"error": str(e)}

        # ── Custom tool: System Status ──
        @mcp.tool()
        def get_system_status() -> dict[str, Any]:
            """Get AI Hub system status: GPU, models, queue, database.
            
            Returns:
                System health information including GPU usage, model status, and queue depth.
            """
            import subprocess
            status = {}

            # GPU
            try:
                gpu = subprocess.run(
                    ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu",
                     "--format=csv,noheader,nounits"],
                    capture_output=True, text=True, timeout=5,
                )
                if gpu.returncode == 0:
                    parts = gpu.stdout.strip().split(", ")
                    status["gpu"] = {
                        "utilization_pct": int(parts[0]),
                        "memory_used_mb": int(parts[1]),
                        "memory_total_mb": int(parts[2]),
                        "temperature_c": int(parts[3]),
                    }
            except Exception:
                status["gpu"] = {"error": "nvidia-smi not available"}

            # Database
            try:
                from app.core.database import get_db_connection
                with get_db_connection() as conn:
                    row = conn.execute(
                        "SELECT count(*) AS sessions FROM sessions"
                    ).fetchone()
                    status["database"] = {"total_sessions": row["sessions"]}
            except Exception as e:
                status["database"] = {"error": str(e)[:200]}

            # Services
            try:
                import subprocess as sp
                llama = sp.run(["curl", "-fsS", "http://localhost:8080/health"], capture_output=True, timeout=3)
                status["llama_cpp"] = "running" if llama.returncode == 0 else "down"
            except Exception:
                status["llama_cpp"] = "unknown"

            return status

        # ── Custom tool: Database Query ──
        @mcp.tool()
        def query_database(sql: str, limit: int = 50) -> dict[str, Any]:
            """Execute a read-only SQL query against the AI Hub PostgreSQL database.
            
            Args:
                sql: SELECT query to execute (only SELECT allowed for safety)
                limit: Max rows to return (1-200)
            
            Returns:
                Query results as rows with column names.
            """
            sql_stripped = sql.strip().upper()
            if not sql_stripped.startswith("SELECT"):
                return {"error": "Only SELECT queries are allowed"}
            if any(kw in sql_stripped for kw in ["DROP", "DELETE", "UPDATE", "INSERT", "ALTER", "TRUNCATE"]):
                return {"error": "Write operations are not allowed"}

            try:
                from app.core.database import get_db_connection
                with get_db_connection() as conn:
                    rows = conn.execute(sql).fetchmany(min(limit, 200))
                    if not rows:
                        return {"rows": [], "count": 0}
                    columns = list(rows[0].keys()) if rows else []
                    return {
                        "columns": columns,
                        "rows": [dict(r) for r in rows],
                        "count": len(rows),
                    }
            except Exception as e:
                return {"error": str(e)[:500]}

        # Mount MCP
        mcp.mount()
        logger.info("MCP server with custom tools mounted at /mcp")
        return mcp

    except Exception as exc:
        logger.warning("MCP server failed to mount: %s", exc)
        return None
