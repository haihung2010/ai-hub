"""IHI routes — sensor analysis and RAG case management."""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Optional

import httpx
import uuid
from fastapi import APIRouter, Depends, HTTPException, Request
from psycopg_pool import ConnectionPool

from app.core.config import get_settings
from app.core.database import _get_pool
from app.models.ihi import (
    AlertLevel,
    AnalyzeRequest,
    AnalyzeResponse,
    FeedbackRequest,
    FeedbackCreateRequest,
    IHIEvaluateRequest,
    IHIEvaluateResponse,
    RAGCase,
    RAGCreateRequest,
    RAGResponse,
    PatternRange,
    SensorDataRequest,
    SeverityLevel,
    ThresholdViolationModel,
)
from app.services.ihi_analyzer import AlertResult, IHIAnalyzer, IHIThresholdAnalyzer
from app.services.ihi_rag_service import IHIragService
from app.services.ihi_overrides_service import (
    delete_override,
    get_active_override,
    set_override,
)
from app.services.thresholds.loader import evaluate_all_thresholds, get_effective_threshold
from app.services.thresholds.sensor_envelopes import SENSOR_ENVELOPES as ENV

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/ihi", tags=["ihi"])

# Global analyzer instance
_analyzer = IHIAnalyzer()

# Lazy-initialized RAG service
_rag_service: Optional[IHIragService] = None


def _get_rag_service() -> IHIragService:
    global _rag_service
    if _rag_service is None:
        _rag_service = IHIragService(db_pool=_get_pool())
        _rag_service.load_cases()
    return _rag_service


def _db_pool_dep() -> ConnectionPool:
    return _get_pool()


async def _log_ihi_usage(
    db: ConnectionPool,
    api_key_id: Optional[str],
    tenant_id: Optional[str],
    project_id: str,
    latency_ms: float,
    alert: str,
):
    """Log IHI analyze request to usage_events."""
    try:
        with db.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO usage_events
                    (id, tenant_id, api_key_id, project_id, provider, model, route_alias,
                     prompt_tokens, completion_tokens, total_tokens, cost_usd, latency_ms, status_code)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    str(uuid.uuid4()),
                    tenant_id or "ihi",
                    api_key_id,
                    project_id,
                    "ihi_local",
                    "ihi_rule_based",
                    "analyze",
                    0, 0, 0, 0.0,
                    round(latency_ms, 2),
                    200,
                ))
            conn.commit()
    except Exception:
        pass


@router.post("/analyze", response_model=AnalyzeResponse)
async def analyze_sensor_data(
    payload: AnalyzeRequest,
    request: Request,
) -> AnalyzeResponse:
    """Analyze sensor data with 3-layer pipeline: rule → RAG → LLM.

    Layer 1: rule pre-check using thresholds/loader (overrides > defaults).
    Layer 2: RAG pattern-match + vector similarity (if Layer 1 uncertain).
    Layer 3: LLM with RAG context (last resort, narrator only).
    """
    # Build device_readings dict: {device_id: {measurement: value}}
    # Map legacy (t, v, c) to BOTH the new envelope field names AND legacy aliases
    # so unknown devices (via DEFAULT_ENVELOPE) and known devices both work.
    device_readings = {}
    for r in payload.data:
        device_readings[r.id] = {
            "temperature": r.t,
            "velocity_rms": r.v,  # for Sensor-001 (ISO 10816-3)
            "velocity":     r.v,  # alias for default envelope
            "current":      r.c,
        }
    for device_id, extra in payload.extra.items():
        device_readings.setdefault(device_id, {}).update(extra)

    db = _db_pool_dep()
    analyzer = IHIThresholdAnalyzer(db)
    rule_result = analyzer.analyze_readings(device_readings)

    # Build violation list for response
    violations = []
    for device_id, readings in device_readings.items():
        for v in evaluate_all_thresholds(db, device_id, readings):
            violations.append(ThresholdViolationModel(
                device_id=v.device_id, measurement=v.measurement,
                value=v.value, severity=v.severity,
                threshold_source=v.threshold.source,
                threshold_severity=v.threshold.severity,
                unit=v.threshold.unit, note=v.threshold.note,
            ))

    # Layer 1 hard stop: any DANGER violation
    if rule_result.alert == AlertLevel.DANGER:
        source = "rule_override" if any(
            v.threshold_source == "manual" for v in violations
        ) else "rule_default"
        # Handle override_thresholds: write submitted readings as new baseline
        if payload.override_thresholds:
            api_key_id = getattr(request.state, "api_key_id", None)
            set_by = f"api_key:{api_key_id}" if api_key_id else "manual"
            for device_id, readings in device_readings.items():
                for measurement, value in readings.items():
                    if value is None: continue
                    set_override(
                        db, device_id=device_id, measurement=measurement,
                        min_value=None, max_value=value,
                        severity=rule_result.alert.value,
                        source="manual", set_by=set_by, note=payload.note,
                    )
        return AnalyzeResponse(
            alert=rule_result.alert,
            devices=list({v.device_id for v in violations if v.severity == "DANGER"}),
            case_id=None, confidence=1.0,
            symptom=None, violations=violations,
            source_layer=source,
            narrative=f"Rule layer caught {len(violations)} violation(s)",
        )

    # Layer 1 WARNING or NORMAL: don't escalate to LLM yet (Layers 2/3 are not yet integrated)
    return AnalyzeResponse(
        alert=rule_result.alert,
        devices=list({v.device_id for v in violations}),
        case_id=None, confidence=1.0 if rule_result.alert == AlertLevel.NORMAL else 0.7,
        symptom=None, violations=violations,
        source_layer="rule",
        narrative="Layer 1 only (Layers 2/3 not yet integrated)",
    )


@router.get("/rag", response_model=list[RAGCase])
async def list_rag_cases(
    severity: Optional[SeverityLevel] = None,
    limit: int = 100,
    db_pool: ConnectionPool = Depends(_db_pool_dep),
) -> list[RAGCase]:
    """List RAG cases with optional severity filter."""
    rag_service = _get_rag_service()
    cases = rag_service.list_cases(severity=severity.value if severity else None, limit=limit)
    return [
        RAGCase(
            case_id=str(c["id"]),
            severity=SeverityLevel(c["severity"].upper()) if isinstance(c["severity"], str) else c["severity"],
            symptom=c.get("symptom", ""),
            pattern=c.get("pattern", {}),
            description=c.get("description", ""),
            resolution=c.get("resolution"),
            status=c.get("status", "active"),
            match_count=c.get("match_count", 0),
        )
        for c in cases
    ]


@router.post("/rag", response_model=RAGCase)
async def create_rag_case(
    payload: RAGCreateRequest,
    db_pool: ConnectionPool = Depends(_db_pool_dep),
) -> RAGCase:
    """Create a new RAG case from manager feedback."""
    rag_service = _get_rag_service()
    pattern_dict = payload.pattern.model_dump()
    case_id = rag_service.create_case(
        device_id=payload.case_id,
        severity=payload.severity.value,
        pattern=pattern_dict,
        description=payload.description,
        confirmed_by="manager",
    )
    # Reload to get updated cache
    rag_service.load_cases()
    case = rag_service.get_case(case_id)
    return RAGCase(
        case_id=str(case["id"]),
        severity=SeverityLevel(case["severity"].upper()),
        symptom=case.get("symptom", ""),
        pattern=case.get("pattern", {}),
        description=case.get("description", ""),
        resolution=case.get("resolution"),
        status=case.get("status", "active"),
        match_count=case.get("match_count", 0),
    )


@router.post("/rag/feedback", response_model=RAGResponse)
async def create_rag_from_feedback(
    payload: FeedbackCreateRequest,
    db_pool: ConnectionPool = Depends(_db_pool_dep),
) -> RAGResponse:
    """Create RAG case from incident feedback (simplified endpoint).
    Auto-generates case_id and infers symptom + pattern from severity."""
    rag_service = _get_rag_service()

    # Infer symptom from severity + description keywords
    desc_lower = (payload.description or "").lower()
    sev = payload.severity.value.upper()

    if "rung" in desc_lower or "vibration" in desc_lower:
        symptom = "excessive_vibration" if sev == "CRITICAL" else "vibration_precursor"
    elif "nhiet" in desc_lower or "temp" in desc_lower or "hot" in desc_lower or "overheat" in desc_lower:
        symptom = "overheat" if sev == "CRITICAL" else "overheat_precursor"
    elif "dong" in desc_lower or "current" in desc_lower or "tai" in desc_lower:
        symptom = "overload" if sev == "CRITICAL" else "overload_precursor"
    else:
        symptom = "multi_param" if sev == "CRITICAL" else "normal"

    # Infer pattern from severity
    if sev == "CRITICAL":
        pattern = {"t_min": 85, "t_max": 100, "v_min": 5.0, "v_max": 10.0, "c_min": 65, "c_max": 100}
    elif sev == "WARNING":
        pattern = {"t_min": 80, "t_max": 90, "v_min": 4.0, "v_max": 6.0, "c_min": 55, "c_max": 75}
    else:
        pattern = {"t_min": 75, "t_max": 85, "v_min": 3.0, "v_max": 5.0, "c_min": 45, "c_max": 65}

    case_id = rag_service.create_case(
        device_id=payload.device_id,
        severity=sev,
        pattern=pattern,
        description=payload.description,
        confirmed_by="manager",
    )
    rag_service.load_cases()

    return RAGResponse(
        case_id=str(case_id),
        status="created",
        pattern=PatternRange(**pattern),
    )


@router.get("/rag/{case_id}", response_model=RAGCase)
async def get_rag_case(
    case_id: int,
    db_pool: ConnectionPool = Depends(_db_pool_dep),
) -> RAGCase:
    """Get a specific RAG case by ID."""
    rag_service = _get_rag_service()
    case = rag_service.get_case(case_id)
    if not case:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"case {case_id} not found")
    return RAGCase(
        case_id=str(case["id"]),
        severity=SeverityLevel(case["severity"].upper()),
        symptom=case.get("symptom", ""),
        pattern=case.get("pattern", {}),
        description=case.get("description", ""),
        resolution=case.get("resolution"),
        status=case.get("status", "active"),
        match_count=case.get("match_count", 0),
    )


@router.put("/rag/{case_id}", response_model=RAGCase)
async def update_rag_case(
    case_id: int,
    payload: RAGCreateRequest,
    db_pool: ConnectionPool = Depends(_db_pool_dep),
) -> RAGCase:
    """Update an existing RAG case."""
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE ihi_rag_cases
                SET severity = %s, symptom = %s, pattern = %s,
                    description = %s, resolution = %s, status = %s
                WHERE id = %s
                RETURNING id
 """, (
                payload.severity.value,
                payload.symptom,
                json.dumps(payload.pattern.model_dump()),
                payload.description,
                payload.resolution,
                payload.status,
                case_id,
            ))
            row = cur.fetchone()
        conn.commit()

    if not row:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"case {case_id} not found")

    rag_service = _get_rag_service()
    rag_service.load_cases()
    case = rag_service.get_case(case_id)
    return RAGCase(
        case_id=str(case["id"]),
        severity=SeverityLevel(case["severity"].upper()),
        symptom=case.get("symptom", ""),
        pattern=case.get("pattern", {}),
        description=case.get("description", ""),
        resolution=case.get("resolution"),
        status=case.get("status", "active"),
        match_count=case.get("match_count", 0),
    )


@router.delete("/rag/{case_id}")
async def delete_rag_case(
    case_id: int,
    db_pool: ConnectionPool = Depends(_db_pool_dep),
) -> dict:
    """Delete a RAG case."""
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM ihi_rag_cases WHERE id = %s RETURNING id", (case_id,))
            row = cur.fetchone()
        conn.commit()

    if not row:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"case {case_id} not found")

    rag_service = _get_rag_service()
    rag_service.load_cases()
    return {"deleted": case_id}


@router.post("/feedback")
async def submit_feedback(
    payload: FeedbackRequest,
    db_pool: ConnectionPool = Depends(_db_pool_dep),
) -> dict:
    """Submit manager feedback for a RAG case."""
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO ihi_feedback (case_id, feedback, rating)
                VALUES (%s, %s, %s)
                ON CONFLICT DO NOTHING
            """, (payload.case_id, payload.feedback, payload.rating))
        conn.commit()

    return {"ok": True, "case_id": payload.case_id}


# === Device threshold override endpoints ===

@router.get("/devices/{device_id}/thresholds")
async def get_device_thresholds(device_id: str) -> dict:
    """Return effective thresholds for a device (override + default merged).

    Each entry: {measurement, min_value, max_value, severity, unit, source, note}.
    """
    db = _db_pool_dep()
    thresholds = {}
    env = ENV.get(device_id, {})
    seen_measurements: set[str] = set()
    for measurement in env.get("thresholds", {}).keys():
        seen_measurements.add(measurement)
        t = get_effective_threshold(db, device_id, measurement)
        if t:
            thresholds[measurement] = {
                "measurement": t.measurement,
                "min_value": t.min_value,
                "max_value": t.max_value,
                "severity": t.severity,
                "unit": t.unit,
                "source": t.source,
                "note": t.note,
            }
    # Also surface any active overrides even if the device has no default envelope
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT measurement FROM ihi_device_overrides
                WHERE device_id = %s
                  AND valid_from <= CURRENT_TIMESTAMP
                  AND (valid_to IS NULL OR valid_to > CURRENT_TIMESTAMP)
            """, (device_id,))
            override_measurements = {row["measurement"] for row in cur.fetchall()}
    for measurement in override_measurements - seen_measurements:
        t = get_effective_threshold(db, device_id, measurement)
        if t:
            thresholds[measurement] = {
                "measurement": t.measurement,
                "min_value": t.min_value,
                "max_value": t.max_value,
                "severity": t.severity,
                "unit": t.unit,
                "source": t.source,
                "note": t.note,
            }
    return {"device_id": device_id, "thresholds": thresholds}


@router.post("/devices/{device_id}/thresholds")
async def set_device_threshold(device_id: str, payload: dict) -> dict:
    """Set manual override for a (device, measurement) threshold.

    Body: {measurement, min_value?, max_value?, severity, note?}
    """
    measurement = payload.get("measurement")
    if not measurement:
        raise HTTPException(status_code=400, detail="measurement required")
    db = _db_pool_dep()
    set_by = payload.get("set_by") or "api_user"
    row_id = set_override(
        db, device_id=device_id, measurement=measurement,
        min_value=payload.get("min_value"),
        max_value=payload.get("max_value"),
        severity=payload.get("severity", "DANGER"),
        source="manual", set_by=set_by, note=payload.get("note"),
    )
    return {"ok": True, "id": row_id, "device_id": device_id, "measurement": measurement}


@router.delete("/devices/{device_id}/thresholds/{measurement}")
async def delete_device_threshold(device_id: str, measurement: str) -> dict:
    """Clear manual override; revert to default."""
    db = _db_pool_dep()
    deleted = delete_override(db, device_id, measurement)
    return {"ok": True, "deleted": deleted}


@router.get("/devices/{device_id}/thresholds/history")
async def get_threshold_history(device_id: str) -> dict:
    """Audit log: who set what when for this device."""
    db = _db_pool_dep()
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, measurement, min_value, max_value, severity, source,
                       set_by, note, valid_from, valid_to, created_at
                FROM ihi_device_overrides
                WHERE device_id = %s
                ORDER BY created_at DESC
            """, (device_id,))
            rows = cur.fetchall()
    return {
        "device_id": device_id,
        "history": [
            {
                "id": r["id"], "measurement": r["measurement"],
                "min_value": r["min_value"], "max_value": r["max_value"],
                "severity": r["severity"], "source": r["source"],
                "set_by": r["set_by"], "note": r["note"],
                "valid_from": r["valid_from"].isoformat() if r["valid_from"] else None,
                "valid_to": r["valid_to"].isoformat() if r["valid_to"] else None,
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            } for r in rows
        ],
    }


# === LLM-based evaluation endpoint (independent of IHI rule) ===
# Calls Gemma4 E4B local (or whatever IHI_LLAMA_CPP_OPENAI_URL points to).
# Designed for the IHI scheduler: receives one snapshot, returns free-form
# analysis. Phase 1 and Phase 2 calls are completely independent.

_IHI_LLM_SYSTEM = """Bạn là chuyên gia giám sát tình trạng máy công nghiệp. Phân tích readings cảm biến của 3 thiết bị tại MỘT thời điểm.

Thiết bị:
- Sensor-001 (vibration): temperature (°C), humidity (%), battery (%), velocity_x/y/z (mm/s), acceleration_peak_x/y/z
- PLC-001 (digital): AI1 (V), DI1..DI4 (0/1)
- Meter-001 (3-phase electric): V1N/V2N/V3N (V), I1/I2/I3 (A), kW/kW1/kW2/kW3, kVA, kVAr, kWh, PF/PF1/PF2/PF3, F (Hz)

Ngưỡng tham khảo (đã cập nhật theo NEMA MG-1, ISO 10816-3):
- Temperature: >90°C DANGER, 80-90°C WARNING (NEMA Class B SF=1.15)
- Velocity (Class II rigid, Sensor-001 default): >4.5 mm/s DANGER, 2.8-4.5 WARNING (ISO 10816-3)
- Velocity (Class II flexible): >7.1 mm/s DANGER, 4.5-7.1 WARNING
- Current: >75A DANGER, 65-75A WARNING
- Voltage imbalance: >5% DANGER, 2-5% WARNING (NEMA MG-1 Part 14 — KHÔNG dùng 10% cũ)
- Frequency: <49 Hz hoặc >51 Hz WARNING
- Battery sensor: <10% DANGER, 10-20% WARNING (LoRaWAN convention)
- PLC AI range: 0-10V (hoặc 4-20mA); ngoài range = DANGER (broken sensor)
- Phase loss: 1 phase <0.5A trong khi 2 phase >5A = DANGER
- All phases near 0A: DANGER (machine off hoặc mất toàn bộ pha)
- Power factor: <0.7 WARNING
- DI đột ngột đổi trạng thái: cảnh báo

Lưu ý: Mỗi device có thể có manual override (set bởi operator). Override luôn ưu tiên cao nhất.

{rag_context}

Trả lời ngắn gọn bằng tiếng Việt, format BẮT BUỘC:
**Verdict:** NORMAL / WARNING / DANGER
**Giải thích:** (2-4 câu, nêu rõ chỉ số bất thường nếu có)
**Khuyến nghị:** (1 câu, hoặc "Không cần" nếu NORMAL)"""


@router.post("/evaluate", response_model=IHIEvaluateResponse)
async def evaluate_snapshot(
    payload: IHIEvaluateRequest,
) -> IHIEvaluateResponse:
    """Evaluate one snapshot of 3 devices via local LLM (Gemma4 E4B).

    Each call is independent. Phase 1 and Phase 2 do NOT share context.
    Returns raw LLM analysis text (no JSON contract).
    """
    settings = get_settings()
    model = "local-gemma4-e4b-q4"

    # Build the user message: structured but readable
    user_msg_parts = [
        f"Phase: {payload.phase}",
        f"Sample time: {payload.sample_time}",
        "",
        "Readings:",
    ]
    for device_name, dev in payload.devices.items():
        device_type = dev.get("type", "") if isinstance(dev, dict) else ""
        readings = dev.get("readings", {}) if isinstance(dev, dict) else {}
        user_msg_parts.append(f"\n## {device_name} ({device_type})")
        if not readings:
            user_msg_parts.append("  (no readings)")
        else:
            for k, v in readings.items():
                user_msg_parts.append(f"  {k}: {v}")
    user_msg = "\n".join(user_msg_parts)

    # Inject RAG context: retrieve top-3 similar cases
    rag_service = _get_rag_service()
    rag_context = ""
    try:
        # Convert devices dict to flat readings format for RAG
        flat_readings = {}
        for dev_name, dev in payload.devices.items():
            readings = dev.get("readings", {}) if isinstance(dev, dict) else {}
            for k, v in readings.items():
                flat_readings[k] = v
        top_cases = rag_service.retrieve_top_k(flat_readings, k=3)
        if top_cases:
            parts = ["Các case tương tự từ knowledge base:"]
            for i, (case, score) in enumerate(top_cases, 1):
                parts.append(
                    f"[{i}] case_id={case['id']} severity={case['severity']} "
                    f"symptom={case.get('symptom', '?')} (match: {score:.2f})\n"
                    f"    Pattern: {case.get('pattern', {})}\n"
                    f"    Mô tả: {case.get('description', '')}"
                )
            rag_context = "\n".join(parts)
        else:
            rag_context = "Không có case tương tự trong knowledge base."
    except Exception as e:
        logger.warning("RAG context retrieval failed: %s", e)
        rag_context = "RAG lookup skipped (error)."

    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": _IHI_LLM_SYSTEM.format(rag_context=rag_context)},
            {"role": "user", "content": user_msg},
        ],
        "max_tokens": 800,
        "temperature": 0.2,
        "stream": False,
    }

    # Try IHI-specific port first, fall back to main local llama.cpp if it's down.
    candidate_urls = [
        settings.ihi_llama_cpp_openai_url.rstrip("/"),
        settings.llama_cpp_openai_url.rstrip("/"),
    ]
    last_err: Exception | None = None
    data: dict | None = None
    t0 = time.perf_counter()
    async with httpx.AsyncClient(timeout=60.0) as client:
        for base in candidate_urls:
            if not base.endswith("/v1"):
                base = base.rstrip("/") + "/v1"
            url = f"{base}/chat/completions"
            try:
                resp = await client.post(url, json=body)
                resp.raise_for_status()
                data = resp.json()
                break
            except httpx.HTTPError as e:
                logger.warning("IHI evaluate: %s failed: %s — trying next", url, e)
                last_err = e
                continue
    if data is None:
        logger.error("IHI evaluate: all LLM endpoints failed: %s", last_err)
        raise HTTPException(
            status_code=502,
            detail=f"LLM upstream error: all endpoints failed ({last_err})",
        )

    latency_ms = round((time.perf_counter() - t0) * 1000, 2)
    try:
        verdict_text = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as e:
        logger.error("IHI evaluate: malformed LLM response: %s | data=%s", e, data)
        raise HTTPException(status_code=502, detail=f"Malformed LLM response: {e}")
    usage = data.get("usage", {}) or {}
    prompt_tokens = int(usage.get("prompt_tokens", 0))
    completion_tokens = int(usage.get("completion_tokens", 0))

    return IHIEvaluateResponse(
        phase=payload.phase,
        sample_time=payload.sample_time,
        verdict_text=verdict_text.strip(),
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        latency_ms=latency_ms,
    )


# === Read-side endpoints for the alert feed frontend ===
# Reads directly from the local SQLite (alert.db) — separate from PostgreSQL.

_ALERT_DB_PATH = Path("/home/hung/ihi_test/alert.db")


def _open_alert_db() -> sqlite3.Connection:
    if not _ALERT_DB_PATH.exists():
        raise HTTPException(status_code=503, detail=f"alert.db not found at {_ALERT_DB_PATH}")
    con = sqlite3.connect(str(_ALERT_DB_PATH))
    con.row_factory = sqlite3.Row
    return con


@router.get("/cycles")
async def list_cycles(limit: int = 20) -> dict:
    """Return the most recent N cycles (newest first) with both phases.

    Each cycle includes scrape metadata, snapshot count, and both verdicts.
    Used by ihi-alert-feed.html timeline.
    """
    if limit < 1 or limit > 200:
        limit = 20
    try:
        con = _open_alert_db()
    except HTTPException:
        return {"cycles": [], "count": 0, "db_available": False}

    scrapes = con.execute(
        "SELECT id, started_at, finished_at, status, rows_added "
        "FROM scrapes ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    if not scrapes:
        con.close()
        return {"cycles": [], "count": 0, "db_available": True}

    out_cycles = []
    for s in scrapes:
        scrape_id = s["id"]
        verdicts = con.execute(
            """
            SELECT id, phase, sample_time, verdict_text, model,
                   prompt_tokens, completion_tokens, latency_ms, created_at
            FROM verdicts WHERE scrape_id = ?
            ORDER BY phase
            """,
            (scrape_id,),
        ).fetchall()
        out_cycles.append({
            "scrape_id": scrape_id,
            "started_at": s["started_at"],
            "finished_at": s["finished_at"],
            "status": s["status"],
            "rows_added": s["rows_added"],
            "phases": [
                {
                    "id": v["id"],
                    "phase": v["phase"],
                    "sample_time": v["sample_time"],
                    "verdict_text": v["verdict_text"],
                    "model": v["model"],
                    "prompt_tokens": v["prompt_tokens"],
                    "completion_tokens": v["completion_tokens"],
                    "latency_ms": v["latency_ms"],
                    "created_at": v["created_at"],
                }
                for v in verdicts
            ],
        })
    con.close()
    return {
        "cycles": out_cycles,
        "count": len(out_cycles),
        "db_available": True,
        "db_path": str(_ALERT_DB_PATH),
    }


@router.get("/cycles/{scrape_id}/readings")
async def get_cycle_readings(scrape_id: int) -> dict:
    """Return the full snapshot_readings for a given cycle, grouped by phase.

    Used by ihi-alert-feed.html to show the raw data alongside each verdict.
    """
    try:
        con = _open_alert_db()
    except HTTPException:
        return {"scrape_id": scrape_id, "phases": {}, "db_available": False}

    rows = con.execute(
        """
        SELECT s.id as snapshot_id, s.phase, s.sample_time,
               sr.device, sr.measurement, sr.value
        FROM snapshots s
        LEFT JOIN snapshot_readings sr ON sr.snapshot_id = s.id
        WHERE s.scrape_id = ?
        ORDER BY s.phase, sr.device, sr.measurement
        """,
        (scrape_id,),
    ).fetchall()
    con.close()

    phases: dict = {}
    for r in rows:
        phase_key = str(r["phase"])
        if phase_key not in phases:
            phases[phase_key] = {
                "sample_time": r["sample_time"],
                "devices": {},
            }
        if r["device"] is None:
            continue
        dev = phases[phase_key]["devices"].setdefault(r["device"], {})
        dev[r["measurement"]] = r["value"]
    return {"scrape_id": scrape_id, "phases": phases, "db_available": True}


# === Chart endpoint for ihi-charts.html dashboard ===
# Reads from /home/hung/ihi_test/sensors.db and returns downsampled series
# for 4 charts: Energy, Vibration, Temperature, Humidity. Time range in hours.

_SENSORS_DB_PATH = Path("/home/hung/ihi_test/sensors.db")

_CHART_QUERIES = {
    "energy": {
        "label": "Năng lượng",
        "sql": """
            SELECT time, device, measurement, value
            FROM measurements
            WHERE time >= ? AND time < ?
              AND device IN ('Meter-001','PLC-001')
              AND measurement IN ('V1N','V2N','V3N','F','kW')
            ORDER BY time
        """,
    },
    "vibration": {
        "label": "Rung động (Sensor-001)",
        "sql": """
            SELECT time, device, measurement, value
            FROM measurements
            WHERE time >= ? AND time < ?
              AND device='Sensor-001'
              AND measurement IN ('acceleration_peak_x','acceleration_peak_y','acceleration_peak_z')
            ORDER BY time
        """,
    },
    "temperature": {
        "label": "Nhiệt độ (Sensor-001)",
        "sql": """
            SELECT time, device, measurement, value
            FROM measurements
            WHERE time >= ? AND time < ?
              AND device='Sensor-001' AND measurement='temperature'
            ORDER BY time
        """,
    },
    "humidity": {
        "label": "Độ ẩm (Sensor-001)",
        "sql": """
            SELECT time, device, measurement, value
            FROM measurements
            WHERE time >= ? AND time < ?
              AND device='Sensor-001' AND measurement='humidity'
            ORDER BY time
        """,
    },
}


@router.get("/charts")
async def get_charts(hours: int = 24, max_points: int = 400) -> dict:
    """Return downsampled series for the 4 dashboard charts.

    Args:
        hours: time window (1..168)
        max_points: target points per series after downsampling
    """
    if hours < 1 or hours > 168:
        hours = 24
    if max_points < 50 or max_points > 5000:
        max_points = 400
    if not _SENSORS_DB_PATH.exists():
        raise HTTPException(status_code=503, detail=f"sensors.db not found at {_SENSORS_DB_PATH}")
    con = sqlite3.connect(str(_SENSORS_DB_PATH))
    # 24h window ending at "now" (server time, UTC). ICT conversion done in frontend.
    from datetime import datetime, timedelta, timezone
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=hours)
    s_str = start.strftime("%Y-%m-%dT%H:%M:%SZ")
    e_str = end.strftime("%Y-%m-%dT%H:%M:%SZ")

    out: dict = {
        "window_start": s_str,
        "window_end": e_str,
        "hours": hours,
        "charts": {},
    }
    for chart_id, cfg in _CHART_QUERIES.items():
        rows = con.execute(cfg["sql"], (s_str, e_str)).fetchall()
        # Group by (device, measurement)
        groups: dict = {}
        for ts, dev, meas, val in rows:
            key = f"{dev}·{meas}"
            groups.setdefault(key, []).append((ts, val))
        series_out = []
        for key, points in groups.items():
            points.sort(key=lambda x: x[0])
            # Downsample by averaging buckets if too many points
            if len(points) > max_points:
                bucket = len(points) // max_points
                sampled = []
                for i in range(0, len(points), max(1, bucket)):
                    chunk = points[i:i + max(1, bucket)]
                    avg_v = sum(p[1] for p in chunk) / len(chunk)
                    sampled.append((chunk[0][0], round(avg_v, 4)))
                pts = sampled
            else:
                pts = [(t, round(v, 4)) for t, v in points]
            series_out.append({"key": key, "points": pts, "n": len(points)})
        out["charts"][chart_id] = {
            "label": cfg["label"],
            "series": series_out,
        }
    con.close()
    return out
