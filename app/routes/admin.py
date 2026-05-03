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

router = APIRouter(prefix="/v1/admin", tags=["admin"])
PROJECT_ROOT = Path(__file__).resolve().parents[2]


class ModelSwitchRequest(BaseModel):
    mode: Literal["lite", "thinking"]


async def _run_model_switch(mode: str) -> dict[str, object]:
    script = "scripts/start_lite_q8.sh" if mode == "lite" else "scripts/start_thinking_qwen.sh"
    env = os.environ.copy()
    if mode == "lite":
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


@router.post("/model/switch")
async def switch_model(payload: ModelSwitchRequest, request: Request) -> dict[str, object]:
    result = await _run_model_switch(payload.mode)
    if result["returncode"] != 0:
        raise HTTPException(status_code=500, detail=result)

    result["models"] = [
        "local-gemma4-e4b-q8" if payload.mode == "lite" else "local-qwen3.6-27b"
    ]
    return result


@router.post("/knowledge/reindex")
async def reindex_knowledge(
    request: Request,
    tenant_id: str | None = None,
    project_id: str | None = None,
    batch_size: int = 50,
) -> dict[str, object]:
    """Back-fill embeddings for knowledge chunks that were ingested without a vector."""
    ingestion = request.app.state.knowledge_ingestion_service
    result = ingestion.fill_missing_embeddings(
        tenant_id=tenant_id,
        project_id=project_id,
        batch_size=batch_size,
    )
    return result


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
