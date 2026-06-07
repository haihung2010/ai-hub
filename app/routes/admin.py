"""Admin endpoints for local AI Hub operations."""

from __future__ import annotations

import asyncio
import os
import shutil
import time
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from fastapi.responses import JSONResponse
from app.core.database import get_db_connection

router = APIRouter(prefix="/v1/admin", tags=["admin"])
PROJECT_ROOT = Path(__file__).resolve().parents[2]


class ModelSwitchRequest(BaseModel):
    mode: Literal["lite"]


async def _run_model_switch(mode: str) -> dict[str, object]:
    script = "scripts/start_lite_q8.sh"
    env = os.environ.copy()
    env.update({"PARALLEL": "8", "CTX_SIZE": "65536"})

    proc = await asyncio.create_subprocess_exec(
        str(PROJECT_ROOT / script),
        cwd=PROJECT_ROOT,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return {
        "mode": mode,
        "returncode": proc.returncode,
        "stdout": stdout.decode(errors="replace")[-4000:],
        "stderr": stderr.decode(errors="replace")[-4000:],
    }


def _read_meminfo() -> dict[str, float | None]:
    values: dict[str, int] = {}
    meminfo = Path("/proc/meminfo")
    if not meminfo.exists():
        return {"total_mb": None, "available_mb": None, "used_pct": None}
    for line in meminfo.read_text(encoding="utf-8").splitlines():
        key, raw = line.split(":", 1)
        number = raw.strip().split()[0]
        if number.isdigit():
            values[key] = int(number)
    total_kb = values.get("MemTotal")
    available_kb = values.get("MemAvailable")
    if not total_kb or available_kb is None:
        return {"total_mb": None, "available_mb": None, "used_pct": None}
    used_pct = round(100 * (1 - available_kb / total_kb), 2)
    return {
        "total_mb": round(total_kb / 1024, 2),
        "available_mb": round(available_kb / 1024, 2),
        "used_pct": used_pct,
    }


def _process_rss_mb() -> float:
    statm = Path("/proc/self/statm")
    if not statm.exists():
        return 0.0
    pages = int(statm.read_text(encoding="utf-8").split()[1])
    return round(pages * os.sysconf("SC_PAGE_SIZE") / 1024 / 1024, 2)


@router.get("/usage")
async def usage(request: Request) -> dict[str, object]:
    settings = request.app.state.settings
    start_time = getattr(request.app.state, "start_time", time.time())
    disk = shutil.disk_usage(".")
    load1, load5, load15 = os.getloadavg() if hasattr(os, "getloadavg") else (0.0, 0.0, 0.0)
    return {
        "service": "ai-hub",
        "uptime_seconds": round(time.time() - start_time, 3),
        "process": {
            "pid": os.getpid(),
            "rss_mb": _process_rss_mb(),
        },
        "cpu": {
            "load_1m": round(load1, 3),
            "load_5m": round(load5, 3),
            "load_15m": round(load15, 3),
            "cpu_count": os.cpu_count(),
        },
        "memory": _read_meminfo(),
        "disk": {
            "total_gb": round(disk.total / 1024**3, 2),
            "used_gb": round(disk.used / 1024**3, 2),
            "free_gb": round(disk.free / 1024**3, 2),
            "used_pct": round(100 * disk.used / disk.total, 2) if disk.total else 0.0,
        },
        "security": {
            "allowed_hosts": settings.allowed_hosts,
            "public_health_enabled": settings.public_health_enabled,
            "public_docs_enabled": settings.public_docs_enabled,
            "rate_limit_per_minute": settings.rate_limit_per_minute,
            "auth_failure_limit": settings.auth_failure_limit,
            "auth_failure_block_seconds": settings.auth_failure_block_seconds,
        },
        "request_usage": request.app.state.usage_service.summary(),
    }


@router.get("/stats")
async def stats(request: Request) -> dict[str, object]:
    """Request-level usage statistics — latency, routing, fallback, queueing, and errors."""
    return request.app.state.usage_service.summary()


@router.get("/observability")
async def observability(request: Request) -> dict[str, object]:
    """Alias for dashboard-friendly request observability."""
    return request.app.state.usage_service.summary()


@router.get("/risk/gap")
async def risk_action_gap(request: Request) -> dict[str, object]:
    """Show the failure_risk action gap.

    The failure-risk service can RECOMMEND actions (enable_search, ask_clarification,
    inject_risk_context) but may not APPLY them depending on settings
    (FAILURE_RISK_LOG_ONLY, FAILURE_RISK_ENABLE_ACTIONS). The "gap" is the
    number of events where an action was recommended but NOT applied.

    This is the leading indicator for whether to enable actions. If gap > 0
    over a long window, the operator may want to flip FAILURE_RISK_LOG_ONLY
    off and FAILURE_RISK_ENABLE_ACTIONS on.

    See session checkpoint 2026-06-06 (Rank 5 finding: 1,675 events, 0 actions
    applied).
    """
    settings = request.app.state.settings
    mode = {
        "log_only": settings.failure_risk_log_only,
        "enable_actions": settings.failure_risk_enable_actions,
        "enable_search_action": settings.failure_risk_enable_search_action,
        "high_threshold": settings.failure_risk_high_threshold,
        "medium_threshold": settings.failure_risk_medium_threshold,
    }
    with get_db_connection() as conn:
        # Total events
        total = conn.execute(
            "SELECT COUNT(*) AS n FROM failure_risk_events"
        ).fetchone()["n"]
        # Per-action breakdown: how many recommended vs applied
        rows = conn.execute(
            """
            SELECT recommended_action,
                   COUNT(*) AS total,
                   SUM(action_applied) AS applied,
                   COUNT(*) - SUM(action_applied) AS gap
            FROM failure_risk_events
            GROUP BY recommended_action
            ORDER BY total DESC
            """
        ).fetchall()
        # Last 24h gap (events that recommended a real action but weren't applied)
        gap_24h = conn.execute(
            """
            SELECT COUNT(*) AS n
            FROM failure_risk_events
            WHERE action_applied = 0
              AND recommended_action != 'none'
              AND created_at > NOW() - INTERVAL '24 hours'
            """
        ).fetchone()["n"]
    per_action = [
        {
            "recommended_action": r["recommended_action"],
            "total": r["total"],
            "applied": int(r["applied"] or 0),
            # 'none' means no action was recommended, so lack of applied
            # is by design — don't count it as a gap.
            "gap": (
                0 if r["recommended_action"] == "none"
                else int(r["gap"] or 0)
            ),
        }
        for r in rows
    ]
    total_gap = sum(p["gap"] for p in per_action)
    return {
        "mode": mode,
        "summary": {
            "total_events": total,
            "total_gap": total_gap,
            "gap_last_24h": gap_24h,
            "action_enabled": (
                not mode["log_only"] and mode["enable_actions"]
            ),
        },
        "per_action": per_action,
        "recommendation": (
            "Actions ARE enabled — gap represents requests where risk_service "
            "recommended an action but the action could not be applied "
            "(e.g. enable_search requested but no search tool available)."
            if not mode["log_only"] and mode["enable_actions"]
            else (
                "Actions are DISABLED (FAILURE_RISK_LOG_ONLY=true or "
                "FAILURE_RISK_ENABLE_ACTIONS=false). All recommended actions "
                "are recorded but not applied. To enable, set both to true "
                "in .env and restart the API server. See the gap above to "
                "estimate the impact."
            )
        ),
    }


@router.post("/model/switch")
async def switch_model(payload: ModelSwitchRequest, request: Request) -> dict[str, object]:
    result = await _run_model_switch(payload.mode)
    if result["returncode"] != 0:
        raise HTTPException(status_code=500, detail=result)

    result["models"] = ["local-gemma4-e4b-q4"]
    return result


@router.post("/knowledge/reindex")
async def reindex_knowledge(
    request: Request,
    tenant_id: str | None = None,
    project_id: str | None = None,
    batch_size: int = 50,
    force: bool = False,
) -> dict[str, object]:
    """Back-fill embeddings for knowledge chunks.

    By default only re-embeds chunks with NULL embedding. Pass ``force=true``
    to re-embed every chunk — required to roll out Contextual Retrieval to
    chunks ingested before the feature existed.
    """
    ingestion = request.app.state.knowledge_ingestion_service
    result = ingestion.fill_missing_embeddings(
        tenant_id=tenant_id,
        project_id=project_id,
        batch_size=batch_size,
        force=force,
    )
    return result


@router.get("/queue")
async def queue_status(request: Request) -> dict[str, object]:
    """GPU queue depth: active requests and available slots."""
    settings = request.app.state.settings
    ai_service = request.app.state.ai_service
    capacity = settings.gpu_concurrency
    available = ai_service._gpu_lock._value
    active = capacity - available
    return {"capacity": capacity, "active": active, "waiting": max(0, active - capacity)}


@router.get("/health/providers")
async def provider_health(request: Request) -> dict[str, object]:
    settings = request.app.state.settings
    local = request.app.state.local_provider
    openrouter = getattr(request.app.state, "openrouter_provider", None)
    return {
        "providers": [
            {
                "name": local.name,
                "configured": True,
                "base_url": settings.llama_cpp_base_url,
                "status": "configured",
            },
            {
                "name": "openrouter",
                "configured": bool(settings.openrouter_enabled and openrouter is not None),
                "base_url": settings.openrouter_base_url,
                "status": "configured" if settings.openrouter_enabled and openrouter is not None else "disabled",
            },
        ]
    }


_DB_QUERY_MAX_ROWS = 1000
_DB_FORBIDDEN_KEYWORDS = (
    "insert", "update", "delete", "drop", "alter", "truncate",
    "create", "grant", "revoke", "copy", "vacuum", "reindex",
)


def _validate_select_sql(sql: str) -> str | None:
    """Return error message if SQL is unsafe; None if OK."""
    stripped = sql.strip().rstrip(";").strip()
    if not stripped:
        return "Empty query."
    lower = stripped.lower()
    if not (lower.startswith("select") or lower.startswith("with")):
        return "Only SELECT / WITH queries are allowed."
    if ";" in stripped:
        return "Multi-statement queries are not allowed."
    tokens = set(lower.replace("(", " ").replace(")", " ").split())
    bad = tokens.intersection(_DB_FORBIDDEN_KEYWORDS)
    if bad:
        return f"Forbidden keyword(s): {', '.join(sorted(bad))}"
    return None


@router.post("/security/unblock")
async def security_unblock(request: Request, body: dict | None = None):
    """Clear auth-failure block for a given IP (defaults to caller's IP)."""
    body = body or {}
    target_ip = (body.get("ip") or "").strip()
    if not target_ip:
        target_ip = (request.client.host if request.client else "") or "127.0.0.1"
    try:
        import redis as redis_lib
        from app.core.config import get_settings
        r = redis_lib.from_url(get_settings().redis_url, decode_responses=True)
        deleted = r.delete(f"af:{target_ip}", f"aff:{target_ip}")
        return {"ip": target_ip, "deleted_keys": deleted, "ok": True}
    except Exception as e:
        return JSONResponse(status_code=500, content={"detail": str(e)})



async def run_query(request: Request, body: dict):
    """Run read-only SQL query (SELECT/WITH only, capped at 1000 rows)."""
    sql = (body.get("query") or "").strip().rstrip(";")
    err = _validate_select_sql(sql)
    if err:
        return JSONResponse(status_code=400, content={"detail": err})

    capped_sql = sql
    if " limit " not in sql.lower():
        capped_sql = f"{sql} LIMIT {_DB_QUERY_MAX_ROWS}"

    try:
        t0 = time.time()
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SET LOCAL statement_timeout = '5s'")
                cur.execute(capped_sql)
                rows = cur.fetchall()
                cols = [d.name for d in cur.description] if cur.description else []
        return {
            "columns": cols,
            "rows": [dict(r) for r in rows],
            "row_count": len(rows),
            "elapsed_ms": round((time.time() - t0) * 1000, 1),
            "capped": " limit " not in sql.lower(),
        }
    except Exception as e:
        return JSONResponse(status_code=400, content={"detail": str(e)})


@router.get("/db/tables")
async def list_db_tables():
    """List all user tables with row counts and column info."""
    try:
        with get_db_connection() as conn:
            tables = conn.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
                ORDER BY table_name
                """
            ).fetchall()

            results = []
            for t in tables:
                name = t["table_name"]
                cols = conn.execute(
                    """
                    SELECT column_name, data_type, is_nullable
                    FROM information_schema.columns
                    WHERE table_schema = 'public' AND table_name = %s
                    ORDER BY ordinal_position
                    """,
                    (name,),
                ).fetchall()
                count_row = conn.execute(
                    f'SELECT COUNT(*) AS n FROM "{name}"'
                ).fetchone()
                results.append({
                    "name": name,
                    "row_count": count_row["n"] if count_row else 0,
                    "columns": [dict(c) for c in cols],
                })
            return {"tables": results}
    except Exception as e:
        return JSONResponse(status_code=500, content={"detail": str(e)})


@router.get("/db/preview/{table_name}")
async def preview_table(table_name: str, limit: int = 100, offset: int = 0):
    """Preview rows of a table (safe identifier validation)."""
    if not table_name.replace("_", "").isalnum():
        return JSONResponse(status_code=400, content={"detail": "Invalid table name."})
    limit = max(1, min(limit, _DB_QUERY_MAX_ROWS))
    offset = max(0, offset)
    try:
        with get_db_connection() as conn:
            exists = conn.execute(
                """
                SELECT 1 FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = %s
                """,
                (table_name,),
            ).fetchone()
            if not exists:
                return JSONResponse(status_code=404, content={"detail": "Table not found."})
            with conn.cursor() as cur:
                cur.execute(
                    f'SELECT * FROM "{table_name}" LIMIT %s OFFSET %s',
                    (limit, offset),
                )
                rows = cur.fetchall()
                cols = [d.name for d in cur.description] if cur.description else []
            total = conn.execute(
                f'SELECT COUNT(*) AS n FROM "{table_name}"'
            ).fetchone()["n"]
        return {
            "table": table_name,
            "columns": cols,
            "rows": [dict(r) for r in rows],
            "limit": limit,
            "offset": offset,
            "total": total,
        }
    except Exception as e:
        return JSONResponse(status_code=400, content={"detail": str(e)})


@router.get("/management/keys")
async def list_api_keys(request: Request):
    """List all API keys with usage and owner info."""
    sql = """
        SELECT
            k.id, k.name, k.tenant_id, u.name as owner_name, k.enabled, k.rpm_limit,
            k.monthly_budget_usd, k.is_admin,
            COALESCE(SUM(e.cost_usd), 0) as current_spend
        FROM api_keys k
        LEFT JOIN users u ON k.owner_user_id = u.id
        LEFT JOIN usage_events e ON k.id = e.api_key_id AND DATE_TRUNC('month', e.created_at) = DATE_TRUNC('month', CURRENT_DATE)
        GROUP BY k.id, u.name
        ORDER BY k.created_at DESC
    """
    with get_db_connection() as conn:
        rows = conn.execute(sql).fetchall()
        return [dict(row) for row in rows]


@router.get("/management/sessions")
async def list_active_sessions(
    request: Request,
    project_id: str | None = None,
    limit: int = 200,
):
    """List most active sessions across all users and projects."""
    with get_db_connection() as conn:
        if project_id:
            sql = """
                SELECT
                    s.id, s.project_id, u.name as user_name,
                    COUNT(e.id) as message_count,
                    MAX(e.created_at) as last_active
                FROM sessions s
                JOIN users u ON s.user_id = u.id
                LEFT JOIN usage_events e ON s.id = e.session_id
                WHERE s.project_id = %s
                GROUP BY s.id, s.project_id, u.name
                ORDER BY last_active DESC NULLS LAST
                LIMIT %s
            """
            rows = conn.execute(sql, (project_id, limit)).fetchall()
        else:
            sql = """
                SELECT
                    s.id, s.project_id, u.name as user_name,
                    COUNT(e.id) as message_count,
                    MAX(e.created_at) as last_active
                FROM sessions s
                JOIN users u ON s.user_id = u.id
                LEFT JOIN usage_events e ON s.id = e.session_id
                GROUP BY s.id, s.project_id, u.name
                ORDER BY last_active DESC NULLS LAST
                LIMIT %s
            """
            rows = conn.execute(sql, (limit,)).fetchall()
        return [dict(row) for row in rows]


class KeyCreateRequest(BaseModel):
    name: str
    tenant_id: str = "default"
    is_admin: bool = False
    rpm_limit: int = 60
    monthly_budget_usd: float | None = None


@router.post("/keys")
async def create_api_key(payload: KeyCreateRequest, request: Request):
    """Create a new virtual API key."""
    from app.services.api_key_service import ApiKeyService
    service = ApiKeyService()
    key_id, raw_key = service.create_key(
        name=payload.name,
        tenant_id=payload.tenant_id,
        is_admin=payload.is_admin,
        rpm_limit=payload.rpm_limit,
        monthly_budget_usd=payload.monthly_budget_usd,
    )
    return {"id": key_id, "key": raw_key}


@router.delete("/keys/{key_id}")
async def delete_api_key(key_id: str):
    """Disable/Delete an API key."""
    with get_db_connection() as conn:
        conn.execute("UPDATE api_keys SET enabled = 0 WHERE id = %s", (key_id,))
        conn.commit()
    return {"status": "deleted"}


class KeyPatchRequest(BaseModel):
    enabled: bool | None = None
    rpm_limit: int | None = None
    monthly_budget_usd: float | None = None
    is_admin: bool | None = None
    name: str | None = None


@router.patch("/keys/{key_id}")
async def patch_api_key(key_id: str, payload: KeyPatchRequest):
    """Partially update an API key (re-enable, change limits, rename, toggle admin)."""
    fields = payload.model_dump(exclude_none=True)
    if not fields:
        raise HTTPException(status_code=400, detail="No fields to update")

    allowed = {"enabled", "rpm_limit", "monthly_budget_usd", "is_admin", "name"}
    set_parts: list[str] = []
    values: list[object] = []
    for col, raw in fields.items():
        if col not in allowed:
            continue
        if col in {"enabled", "is_admin"}:
            values.append(1 if raw else 0)
        else:
            values.append(raw)
        set_parts.append(f"{col} = %s")

    if not set_parts:
        raise HTTPException(status_code=400, detail="No valid fields")

    values.append(key_id)
    sql = f"UPDATE api_keys SET {', '.join(set_parts)} WHERE id = %s RETURNING id, name, enabled, rpm_limit, monthly_budget_usd, is_admin, tenant_id"
    with get_db_connection() as conn:
        row = conn.execute(sql, values).fetchone()
        conn.commit()
        if not row:
            raise HTTPException(status_code=404, detail="Key not found")
        return dict(row)


class RagUploadRequest(BaseModel):
    project_id: str
    tenant_id: str = "default"
    domain: str = "general"
    title: str
    content: str
    tags: list[str] = []


@router.get("/knowledge/cards")
async def list_knowledge_cards(
    request: Request,
    tenant_id: str | None = None,
    project_id: str | None = None,
    limit: int = 50,
):
    """List knowledge base cards."""
    from app.core.database import get_db_connection
    sql = "SELECT * FROM knowledge_cards WHERE 1=1"
    params = []
    if tenant_id:
        sql += " AND tenant_id = %s"
        params.append(tenant_id)
    if project_id:
        sql += " AND project_id = %s"
        params.append(project_id)
    sql += " ORDER BY created_at DESC LIMIT %s"
    params.append(limit)

    with get_db_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]


@router.post("/knowledge/upload")
async def upload_knowledge(payload: RagUploadRequest, request: Request):
    """Ingest text content directly into the vector knowledge base."""
    from app.models.knowledge import KnowledgeCardCreate
    ingestion = request.app.state.knowledge_ingestion_service
    card_req = KnowledgeCardCreate(
        project_id=payload.project_id,
        tenant_id=payload.tenant_id,
        knowledge_domain=payload.domain,
        title=payload.title,
        content=payload.content,
        tags=payload.tags,
    )
    card = ingestion.create_card(card_req)
    return {"id": card.id, "status": "indexed"}


@router.delete("/knowledge/cards/{card_id}")
async def delete_knowledge_card(card_id: str):
    """Delete a knowledge card. Chunks cascade via FK ON DELETE CASCADE."""
    with get_db_connection() as conn:
        row = conn.execute(
            "DELETE FROM knowledge_cards WHERE id = %s RETURNING id",
            (card_id,),
        ).fetchone()
        conn.commit()
        if not row:
            raise HTTPException(status_code=404, detail="Card not found")
    return {"status": "deleted", "id": card_id}


@router.get("/tenants")
async def list_active_tenants():
    """List projects with usage activity (groups usage_events by project_id)."""
    sql = """
        SELECT
            project_id,
            tenant_id,
            COUNT(DISTINCT user_id) FILTER (WHERE user_id IS NOT NULL) AS user_count,
            COUNT(*) FILTER (WHERE created_at >= CURRENT_DATE) AS requests_today,
            COUNT(*) AS total_requests,
            MAX(created_at) AS last_activity,
            (MAX(created_at) > NOW() - INTERVAL '5 minutes') AS is_active
        FROM usage_events
        GROUP BY project_id, tenant_id
        ORDER BY last_activity DESC NULLS LAST
    """
    with get_db_connection() as conn:
        rows = conn.execute(sql).fetchall()
        return [dict(row) for row in rows]


@router.get("/tenants/{project_id}/users")
async def list_project_users(project_id: str, limit: int = 10):
    """List the most recently active users in a project."""
    sql = """
        SELECT
            u.id, u.name, u.tenant_id, u.created_at,
            COUNT(DISTINCT s.id) AS session_count,
            COUNT(DISTINCT m.id) AS message_count,
            MAX(m.created_at) AS last_message_at,
            (MAX(ue.created_at) > NOW() - INTERVAL '5 minutes') AS is_active
        FROM users u
        JOIN sessions s ON u.id = s.user_id AND s.project_id = %s
        LEFT JOIN messages m ON m.session_id = s.id
        LEFT JOIN usage_events ue ON ue.user_id = u.id AND ue.project_id = %s
        GROUP BY u.id, u.name, u.tenant_id, u.created_at
        ORDER BY last_message_at DESC NULLS LAST
        LIMIT %s
    """
    with get_db_connection() as conn:
        rows = conn.execute(sql, (project_id, project_id, limit)).fetchall()
        return [dict(row) for row in rows]


@router.get("/users/{user_id}/detail")
async def user_detail(user_id: str):
    """User profile: info, bound API key, pinned memory, structured memory, summaries."""
    with get_db_connection() as conn:
        user = conn.execute("SELECT * FROM users WHERE id = %s", (user_id,)).fetchone()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        key = conn.execute(
            """SELECT id, name, enabled, rpm_limit, is_admin, allow_external,
                      allowed_projects_json, monthly_budget_usd, created_at
               FROM api_keys WHERE owner_user_id = %s LIMIT 1""",
            (user_id,),
        ).fetchone()

        pinned = conn.execute(
            """SELECT key, value, confidence, project_id, updated_at
               FROM pinned_memories WHERE user_id = %s AND is_active = 1
               ORDER BY updated_at DESC LIMIT 20""",
            (user_id,),
        ).fetchall()

        memory_items = conn.execute(
            """SELECT memory_type, subject, predicate, object, content, salience, created_at
               FROM memory_items WHERE user_id = %s
               ORDER BY salience DESC, created_at DESC LIMIT 20""",
            (user_id,),
        ).fetchall()

        summaries = conn.execute(
            """SELECT content, project_id, updated_at FROM summaries WHERE user_id = %s
               ORDER BY updated_at DESC LIMIT 5""",
            (user_id,),
        ).fetchall()

        return {
            "user": dict(user),
            "api_key": dict(key) if key else None,
            "pinned_memories": [dict(r) for r in pinned],
            "memory_items": [dict(r) for r in memory_items],
            "summaries": [dict(r) for r in summaries],
        }


@router.get("/users/{user_id}/messages")
async def user_messages(user_id: str, project_id: str | None = None, limit: int = 100):
    """Full chat history for a user, optionally filtered by project."""
    sql = """
        SELECT m.id, m.role, m.content, m.created_at, s.project_id, m.is_summarized
        FROM messages m
        JOIN sessions s ON m.session_id = s.id
        WHERE m.user_id = %s
    """
    params: list = [user_id]
    if project_id:
        sql += " AND s.project_id = %s"
        params.append(project_id)
    sql += " ORDER BY m.created_at DESC LIMIT %s"
    params.append(limit)

    with get_db_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]


@router.get("/gpu/stats")
async def gpu_stats():
    """Fetch live NVIDIA GPU metrics."""
    try:
        import subprocess
        cmd = "nvidia-smi --query-gpu=name,index,memory.used,memory.total,utilization.gpu,temperature.gpu --format=csv,noheader,nounits"
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, check=True)
        gpus = []
        for line in result.stdout.strip().split("\n"):
            if not line: continue
            parts = [p.strip() for p in line.split(",")]
            gpus.append({
                "name": parts[0],
                "index": int(parts[1]),
                "memory_used": int(parts[2]),
                "memory_total": int(parts[3]),
                "utilization": int(parts[4]),
                "temperature": int(parts[5]),
            })
        return {"gpus": gpus}
    except Exception as e:
        # Fallback for dev environment without NVIDIA
        return {"gpus": [], "error": str(e)}
