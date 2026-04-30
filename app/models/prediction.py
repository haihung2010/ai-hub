"""Typed records for stock prediction audit storage."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PredictionRecord:
    id: str
    tenant_id: str
    project_id: str
    user_id: str | None
    session_id: str
    assistant_message_id: int | None
    symbol: str | None
    horizon: str | None
    prediction_text: str
    confidence: str | None
    inputs_json: str
    model: str
    provider: str
    created_at: str
    actual_outcome: str | None = None
    evaluated_at: str | None = None
