"""IHI routes — sensor analysis and RAG case management."""

from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, Depends, Request
from psycopg_pool import ConnectionPool

from app.core.database import _get_pool
from app.models.ihi import (
    AlertLevel,
    AnalyzeResponse,
    FeedbackRequest,
    RAGCase,
    RAGCreateRequest,
    SensorDataRequest,
    SeverityLevel,
)
from app.services.ihi_analyzer import AlertResult, IHIAnalyzer
from app.services.ihi_rag_service import IHIragService

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


@router.post("/analyze", response_model=AnalyzeResponse)
async def analyze_sensor_data(payload: SensorDataRequest) -> AnalyzeResponse:
    """Analyze sensor data from IHI.

    Checks CRITICAL/WARNING rules first via IHIAnalyzer.
    If no match, checks RAG for matching cases.
    Returns alert level, affected devices, and optional case info.
    """
    readings = [(r.id, r.t, r.v, r.c) for r in payload.data]

    # Run rule-based analysis
    results = _analyzer.analyze_batch(readings)

    # Determine overall alert level
    alert = AlertLevel.NORMAL
    danger_devices = []
    warning_devices = []

    for result in results:
        if result.alert == AlertLevel.DANGER:
            danger_devices.append(result.device_id)
            alert = AlertLevel.DANGER
        elif result.alert == AlertLevel.WARNING and alert != AlertLevel.DANGER:
            warning_devices.append(result.device_id)
            alert = AlertLevel.WARNING

    devices = danger_devices if alert == AlertLevel.DANGER else warning_devices

    # If no rule match, try RAG
    case_id: Optional[str] = None
    confidence = 0.0
    symptom: Optional[str] = None

    if alert == AlertLevel.NORMAL and payload.data:
        # Try to find matching RAG case for first reading
        first = payload.data[0]
        rag_service = _get_rag_service()
        case, conf = rag_service.find_matching_case(first.t, first.v, first.c)
        if case:
            case_id = str(case["id"])
            confidence = conf
            symptom = case.get("symptom")
            # Determine alert from RAG case severity
            severity = case.get("severity", "").upper()
            if severity == "CRITICAL":
                alert = AlertLevel.DANGER
            elif severity == "WARNING":
                alert = AlertLevel.WARNING
 # Increment match count
            rag_service.increment_match_count(case["id"])

    return AnalyzeResponse(
        alert=alert,
        devices=devices,
        case_id=case_id,
        confidence=confidence,
        symptom=symptom,
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
