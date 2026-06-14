"""Read-only stock prediction audit endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request

from app.core.database import DEFAULT_TENANT_ID
from app.services.prediction_service import STOCK_PROJECT_ID, PredictionService
from app.services.user_service import UserService
from app.utils.tenant_guard import resolve_tenant

router = APIRouter(prefix="/v1/predictions", tags=["predictions"])


@router.get("")
async def list_predictions(
    request: Request,
    tenant_id: str = Query(default=DEFAULT_TENANT_ID, min_length=1, max_length=64),
    project_id: str = Query(default=STOCK_PROJECT_ID, min_length=1, max_length=64),
    user_name: str = Query(min_length=1, max_length=120),
    symbol: str | None = Query(default=None, min_length=1, max_length=32),
    limit: int = Query(default=20, ge=1, le=100),
) -> list[dict[str, str | None]]:
    tenant_id = resolve_tenant(request, tenant_id)
    users: UserService = request.app.state.user_service
    predictions: PredictionService = request.app.state.prediction_service
    user = users.find_by_name(user_name, tenant_id)
    if user is None:
        return []
    user_id = user.id

    records = predictions.list_records(
        tenant_id=tenant_id,
        project_id=project_id,
        user_id=user_id,
        symbol=symbol,
        limit=limit,
    )
    return [
        {
            "id": record.id,
            "symbol": record.symbol,
            "horizon": record.horizon,
            "prediction_text": record.prediction_text,
            "confidence": record.confidence,
            "model": record.model,
            "provider": record.provider,
            "created_at": record.created_at,
            "actual_outcome": record.actual_outcome,
            "evaluated_at": record.evaluated_at,
        }
        for record in records
    ]
