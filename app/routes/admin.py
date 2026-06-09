"""Admin endpoints for local AI Hub operations."""

from __future__ import annotations

import asyncio
import os
import shutil
import time
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from fastapi.responses import JSONResponse
from app.core.database import get_db_connection

router = APIRouter(prefix="/v1/admin", tags=["admin"])
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def require_admin(request: Request) -> None:
    """Reject the request unless the authenticated API key has admin privileges.

    The security middleware sets ``request.state.api_key_is_admin`` for both
    the static master key (always admin) and DB-backed virtual keys (per-key
    ``is_admin`` flag). Routes under ``/v1/admin`` are sensitive — they expose
    usage data, allow minting API keys, deleting knowledge cards, and running
    arbitrary SELECT SQL — so non-admin keys must be denied with 403.
    """
    if not getattr(request.state, "api_key_is_admin", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="admin key required",
        )


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


@router.get("/usage", dependencies=[Depends(require_admin)])
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


@router.get("/stats", dependencies=[Depends(require_admin)])
async def stats(request: Request) -> dict[str, object]:
    """Request-level usage statistics — latency, routing, fallback, queueing, and errors."""
    return request.app.state.usage_service.summary()


@router.get("/observability", dependencies=[Depends(require_admin)])
async def observability(request: Request) -> dict[str, object]:
    """Alias for dashboard-friendly request observability."""
    return request.app.state.usage_service.summary()


@router.get("/risk/gap", dependencies=[Depends(require_admin)])
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


@router.post("/model/switch", dependencies=[Depends(require_admin)])
async def switch_model(payload: ModelSwitchRequest, request: Request) -> dict[str, object]:
    result = await _run_model_switch(payload.mode)
    if result["returncode"] != 0:
        raise HTTPException(status_code=500, detail=result)

    result["models"] = ["local-gemma4-e4b-q4"]
    return result


@router.post("/knowledge/reindex", dependencies=[Depends(require_admin)])
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


@router.get("/queue", dependencies=[Depends(require_admin)])
async def queue_status(request: Request) -> dict[str, object]:
    """GPU queue depth: active requests and available slots."""
    settings = request.app.state.settings
    ai_service = request.app.state.ai_service
    capacity = settings.gpu_concurrency
    available = ai_service._gpu_lock._value
    active = capacity - available
    return {"capacity": capacity, "active": active, "waiting": max(0, active - capacity)}


@router.get("/health/providers", dependencies=[Depends(require_admin)])
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


@router.get("/health/instances", dependencies=[Depends(require_admin)])
async def instance_health(request: Request) -> dict[str, object]:
    """Probe each llama.cpp instance individually. Returns per-port status,
    model alias, and uptime probe latency. Use this for faster failure
    detection than waiting for /v1/admin/health/providers (which only
    reports config without probing).

    Each instance is probed with a 1s timeout GET /v1/models. If the probe
    fails, the instance status is "down" and a reason is returned.
    """
    import asyncio
    import time
    import httpx

    settings = request.app.state.settings

    # Build list of instances to probe: primary, background, reranker, iHi
    instances = [
        {"name": "primary",   "alias": "local-gemma4-12b-q4-text", "url": settings.llama_cpp_openai_url},
        {"name": "background", "alias": "local-gemma4-e2b-q4-bg",   "url": settings.background_llama_cpp_openai_url if settings.background_llama_cpp_enabled else None},
        {"name": "reranker",  "alias": "bge-reranker-v2-m3",         "url": None},  # resolved below
        {"name": "ihi",       "alias": "local-gemma4-e2b-q4-ihi",  "url": settings.ihi_llama_cpp_openai_url if settings.ihi_llama_cpp_enabled else None},
    ]

    # Resolve reranker URL from settings.llama_cpp_nodes or build default
    # The reranker lives on port 8082 by convention.
    if instances[2]["url"] is None:
        base = settings.llama_cpp_openai_url  # http://localhost:8080/v1
        if base:
            # swap port to 8082
            try:
                from urllib.parse import urlparse
                parsed = urlparse(base)
                instances[2]["url"] = f"{parsed.scheme}://{parsed.hostname}:8082/v1"
            except Exception:
                pass

    async def _probe(inst: dict) -> dict:
        if not inst["url"]:
            return {**inst, "status": "disabled", "latency_ms": None, "model": None, "error": "no_url_configured"}
        t0 = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=1.0) as client:
                resp = await client.get(f"{inst['url']}/models")
            elapsed_ms = round((time.monotonic() - t0) * 1000, 1)
            if resp.status_code == 200:
                data = resp.json()
                models = data.get("data", [])
                model_id = models[0]["id"] if models else None
                return {
                    **inst,
                    "status": "up",
                    "latency_ms": elapsed_ms,
                    "model": model_id,
                    "error": None,
                }
            return {
                **inst,
                "status": "degraded",
                "latency_ms": elapsed_ms,
                "model": None,
                "error": f"HTTP {resp.status_code}",
            }
        except Exception as exc:
            elapsed_ms = round((time.monotonic() - t0) * 1000, 1)
            return {
                **inst,
                "status": "down",
                "latency_ms": elapsed_ms,
                "model": None,
                "error": str(exc)[:200],
            }

    results = await asyncio.gather(*[_probe(i) for i in instances])
    healthy = sum(1 for r in results if r["status"] == "up")
    return {
        "summary": {
            "total": len(results),
            "healthy": healthy,
            "degraded": sum(1 for r in results if r["status"] == "degraded"),
            "down": sum(1 for r in results if r["status"] == "down"),
        },
        "instances": results,
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


@router.post("/security/unblock", dependencies=[Depends(require_admin)])
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


@router.get("/db/tables", dependencies=[Depends(require_admin)])
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


@router.get("/db/preview/{table_name}", dependencies=[Depends(require_admin)])
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


@router.get("/management/keys", dependencies=[Depends(require_admin)])
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


@router.get("/management/sessions", dependencies=[Depends(require_admin)])
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
                    s.id, s.project_id, s.user_id, u.name as user_name, u.tenant_id,
                    COUNT(e.id) as message_count,
                    MAX(e.created_at) as last_active
                FROM sessions s
                JOIN users u ON s.user_id = u.id
                LEFT JOIN usage_events e ON s.id = e.session_id
                WHERE s.project_id = %s
                GROUP BY s.id, s.project_id, s.user_id, u.name, u.tenant_id
                ORDER BY last_active DESC NULLS LAST
                LIMIT %s
            """
            rows = conn.execute(sql, (project_id, limit)).fetchall()
        else:
            sql = """
                SELECT
                    s.id, s.project_id, s.user_id, u.name as user_name, u.tenant_id,
                    COUNT(e.id) as message_count,
                    MAX(e.created_at) as last_active
                FROM sessions s
                JOIN users u ON s.user_id = u.id
                LEFT JOIN usage_events e ON s.id = e.session_id
                GROUP BY s.id, s.project_id, s.user_id, u.name, u.tenant_id
                ORDER BY last_active DESC NULLS LAST
                LIMIT %s
            """
            rows = conn.execute(sql, (limit,)).fetchall()
        return [dict(row) for row in rows]


class KeyCreateRequest(BaseModel):
    name: str
    tenant_id: str = "default"
    is_admin: bool = False
    rpm_limit: int = Field(default=60, ge=1, le=10000)
    monthly_budget_usd: float | None = None


@router.post("/keys", dependencies=[Depends(require_admin)])
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


@router.delete("/keys/{key_id}", dependencies=[Depends(require_admin)])
async def delete_api_key(key_id: str):
    """Disable/Delete an API key."""
    with get_db_connection() as conn:
        conn.execute("UPDATE api_keys SET enabled = 0 WHERE id = %s", (key_id,))
        conn.commit()
    return {"status": "deleted"}


class KeyPatchRequest(BaseModel):
    enabled: bool | None = None
    rpm_limit: int | None = Field(default=None, ge=1, le=10000)
    monthly_budget_usd: float | None = None
    is_admin: bool | None = None
    name: str | None = None


@router.patch("/keys/{key_id}", dependencies=[Depends(require_admin)])
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
    content: str = Field(..., max_length=10_000_000)
    tags: list[str] = []


@router.get("/knowledge/cards", dependencies=[Depends(require_admin)])
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


@router.post("/knowledge/upload", dependencies=[Depends(require_admin)])
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


@router.delete("/knowledge/cards/{card_id}", dependencies=[Depends(require_admin)])
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


@router.get("/tenants", dependencies=[Depends(require_admin)])
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


@router.get("/tenants/{project_id}/users", dependencies=[Depends(require_admin)])
async def list_project_users(project_id: str, limit: int = 10):
    """List the most recently active users in a project.

    Uses pre-aggregated subqueries (one per metric) to avoid the
    sessions × messages CROSS JOIN explosion that produced 24M rows
    for 12 users × 2039 sessions × 4078 messages (~21s on-disk sort).
    Each subquery is small and indexed; total join is at most N_users rows.
    """
    sql = """
        SELECT
            u.id, u.name, u.tenant_id, u.created_at,
            COALESCE(s_agg.session_count, 0) AS session_count,
            COALESCE(m_agg.message_count, 0) AS message_count,
            m_agg.last_message_at,
            (COALESCE(ue_agg.latest_activity, u.created_at) > NOW() - INTERVAL '5 minutes') AS is_active
        FROM users u
        LEFT JOIN (
            SELECT user_id, COUNT(*) AS session_count
            FROM sessions
            WHERE project_id = %s
            GROUP BY user_id
        ) s_agg ON u.id = s_agg.user_id
        LEFT JOIN (
            SELECT s.user_id, COUNT(m.id) AS message_count, MAX(m.created_at) AS last_message_at
            FROM messages m
            JOIN sessions s ON m.session_id = s.id
            WHERE s.project_id = %s
            GROUP BY s.user_id
        ) m_agg ON u.id = m_agg.user_id
        LEFT JOIN (
            SELECT user_id, MAX(created_at) AS latest_activity
            FROM usage_events
            WHERE project_id = %s
            GROUP BY user_id
        ) ue_agg ON u.id = ue_agg.user_id
        ORDER BY m_agg.last_message_at DESC NULLS LAST
        LIMIT %s
    """
    with get_db_connection() as conn:
        rows = conn.execute(sql, (project_id, project_id, project_id, limit)).fetchall()
        return [dict(row) for row in rows]


@router.get("/users/{user_id}/detail", dependencies=[Depends(require_admin)])
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

        sessions = conn.execute(
            """SELECT s.id, s.project_id, s.tenant_id, s.created_at,
                      COUNT(m.id) AS message_count,
                      MAX(m.created_at) AS last_message_at
               FROM sessions s
               LEFT JOIN messages m ON m.session_id = s.id
               WHERE s.user_id = %s
               GROUP BY s.id, s.project_id, s.tenant_id, s.created_at
               ORDER BY s.created_at DESC
               LIMIT 50""",
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
            "sessions": [dict(r) for r in sessions],
        }


@router.get("/users/{user_id}/messages", dependencies=[Depends(require_admin)])
async def user_messages(
    user_id: str,
    project_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
    truncate: int = 200,
    search: str | None = None,
    role: str | None = None,
    model: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
):
    """Full chat history for a user, optionally filtered by project/role/model/search/date.

    Args:
        limit: max messages to return (default 50).
        offset: skip this many messages (for pagination).
        truncate: truncate message content to this many chars (set to 0 for full).
        search: case-insensitive substring match on content (ILIKE).
        role: 'user' / 'assistant' / 'system'.
        model: filter by exact model name (matched via usage_events join).
        date_from: ISO date or datetime lower bound (inclusive).
        date_to: ISO date or datetime upper bound (inclusive).
    """
    base_sql = f"""
        SELECT m.id, m.role, m.content, m.created_at, s.project_id, m.is_summarized,
               ue.model, ue.latency_ms, ue.prompt_tokens, ue.completion_tokens, ue.cost_usd,
               COUNT(*) OVER () AS total_count
        FROM messages m
        JOIN sessions s ON m.session_id = s.id
        LEFT JOIN LATERAL (
            SELECT model, latency_ms, prompt_tokens, completion_tokens, cost_usd
            FROM usage_events ue
            WHERE ue.user_id = m.user_id
              AND ue.project_id = s.project_id
              AND ue.created_at > m.created_at - INTERVAL '5 seconds'
              AND ue.created_at < m.created_at + INTERVAL '5 seconds'
            ORDER BY ABS(EXTRACT(EPOCH FROM (ue.created_at - m.created_at)))
            LIMIT 1
        ) ue ON TRUE
        WHERE m.user_id = %s
    """
    params: list = [user_id]
    if project_id:
        base_sql += " AND s.project_id = %s"
        params.append(project_id)
    if role:
        base_sql += " AND m.role = %s"
        params.append(role)
    if model:
        base_sql += " AND ue.model = %s"
        params.append(model)
    if search:
        base_sql += " AND m.content ILIKE %s"
        params.append(f"%{search}%")
    if date_from:
        base_sql += " AND m.created_at >= %s"
        params.append(date_from)
    if date_to:
        base_sql += " AND m.created_at <= %s"
        params.append(date_to)
    base_sql += " ORDER BY m.created_at DESC LIMIT %s OFFSET %s"
    params.extend([limit, offset])

    with get_db_connection() as conn:
        rows = conn.execute(base_sql, params).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            if truncate > 0 and d.get('content') and len(d['content']) > truncate:
                d['content'] = d['content'][:truncate] + '...'
                d['truncated'] = True
            else:
                d['truncated'] = False
            out.append(d)
        return out


@router.get("/users/{user_id}/stats", dependencies=[Depends(require_admin)])
async def user_stats(user_id: str, project_id: str | None = None):
    """Aggregated stats for a user — fast, no full message fetch.

    Use this for the user detail page topbar (counts) instead of fetching
    all messages just to count.
    """
    project_filter = ""
    project_params: list = [project_id] if project_id else []
    if project_id:
        project_filter = " AND s.project_id = %s"

    with get_db_connection() as conn:
        row = conn.execute(
            f"""
            SELECT
                (SELECT COUNT(DISTINCT s.id) FROM sessions s WHERE s.user_id = %s{project_filter}) AS session_count,
                (SELECT COUNT(m.id) FROM messages m JOIN sessions s ON m.session_id = s.id WHERE m.user_id = %s{project_filter}) AS message_count,
                (SELECT COUNT(m.id) FROM messages m JOIN sessions s ON m.session_id = s.id WHERE m.user_id = %s{project_filter} AND m.role = 'user') AS user_messages,
                (SELECT COUNT(m.id) FROM messages m JOIN sessions s ON m.session_id = s.id WHERE m.user_id = %s{project_filter} AND m.role = 'assistant') AS assistant_messages,
                (SELECT COUNT(m.id) FROM messages m JOIN sessions s ON m.session_id = s.id WHERE m.user_id = %s{project_filter} AND m.is_summarized = 1) AS summarized,
                (SELECT MIN(m.created_at) FROM messages m JOIN sessions s ON m.session_id = s.id WHERE m.user_id = %s{project_filter}) AS first_at,
                (SELECT MAX(m.created_at) FROM messages m JOIN sessions s ON m.session_id = s.id WHERE m.user_id = %s{project_filter}) AS last_at,
                (SELECT COALESCE(SUM(prompt_tokens), 0) FROM usage_events WHERE user_id = %s) AS prompt_tok,
                (SELECT COALESCE(SUM(completion_tokens), 0) FROM usage_events WHERE user_id = %s) AS completion_tok,
                (SELECT COALESCE(SUM(cost_usd), 0) FROM usage_events WHERE user_id = %s) AS total_cost
            """,
            (
                [user_id] + project_params
                + [user_id] + project_params
                + [user_id] + project_params
                + [user_id] + project_params
                + [user_id] + project_params
                + [user_id] + project_params
                + [user_id] + project_params
                + [user_id]
                + [user_id]
                + [user_id]
            ),
        ).fetchone()
        return dict(row) if row else {}


@router.get("/gpu/stats", dependencies=[Depends(require_admin)])
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


@router.get("/cache/stats", dependencies=[Depends(require_admin)])
async def cache_stats() -> dict[str, object]:
    """Return Redis query-cache stats: hits, misses, hit rate, errors.

    Stats are in-process counters; they reset on process restart. Useful
    for measuring cache effectiveness during smoke / load tests.
    """
    from app.services.response_cache import get_stats
    return get_stats()
