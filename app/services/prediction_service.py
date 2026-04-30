"""Stores and retrieves auditable stock prediction records."""

from __future__ import annotations

import json
import re
import uuid

from app.core.database import DEFAULT_TENANT_ID, get_db_connection
from app.models.prediction import PredictionRecord

STOCK_PROJECT_ID = "stock_prediction"


class PredictionService:
    def maybe_store_from_chat(
        self,
        *,
        tenant_id: str,
        project_id: str,
        user_id: str | None,
        session_id: str,
        content: str,
        model: str,
        provider: str,
        inputs: dict[str, str | None] | None = None,
    ) -> PredictionRecord | None:
        if project_id != STOCK_PROJECT_ID or not content.strip():
            return None
        parsed = self._parse_prediction_text(content)
        return self.create_record(
            tenant_id=tenant_id,
            project_id=project_id,
            user_id=user_id,
            session_id=session_id,
            assistant_message_id=None,
            symbol=parsed["symbol"],
            horizon=parsed["horizon"],
            prediction_text=content,
            confidence=parsed["confidence"],
            inputs_json=json.dumps(inputs or {}, ensure_ascii=False),
            model=model,
            provider=provider,
        )

    def create_record(
        self,
        *,
        tenant_id: str,
        project_id: str,
        user_id: str | None,
        session_id: str,
        assistant_message_id: int | None,
        symbol: str | None,
        horizon: str | None,
        prediction_text: str,
        confidence: str | None,
        inputs_json: str,
        model: str,
        provider: str,
    ) -> PredictionRecord:
        record_id = str(uuid.uuid4())
        with get_db_connection() as conn:
            conn.execute(
                "INSERT INTO prediction_records "
                "(id, tenant_id, project_id, user_id, session_id, assistant_message_id, symbol, horizon, "
                "prediction_text, confidence, inputs_json, model, provider) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    record_id,
                    tenant_id,
                    project_id,
                    user_id,
                    session_id,
                    assistant_message_id,
                    symbol,
                    horizon,
                    prediction_text,
                    confidence,
                    inputs_json,
                    model,
                    provider,
                ),
            )
            row = conn.execute(
                "SELECT id, tenant_id, project_id, user_id, session_id, assistant_message_id, "
                "symbol, horizon, prediction_text, confidence, inputs_json, model, provider, "
                "created_at, actual_outcome, evaluated_at "
                "FROM prediction_records WHERE id = ?",
                (record_id,),
            ).fetchone()
            conn.commit()
        return self._row_to_record(row)

    def list_records(
        self,
        *,
        tenant_id: str = DEFAULT_TENANT_ID,
        project_id: str = STOCK_PROJECT_ID,
        user_id: str | None = None,
        symbol: str | None = None,
        limit: int = 20,
    ) -> list[PredictionRecord]:
        bounded_limit = max(1, min(limit, 100))
        query = (
            "SELECT id, tenant_id, project_id, user_id, session_id, assistant_message_id, "
            "symbol, horizon, prediction_text, confidence, inputs_json, model, provider, "
            "created_at, actual_outcome, evaluated_at "
            "FROM prediction_records WHERE tenant_id = ? AND project_id = ?"
        )
        params: list[str | int] = [tenant_id, project_id]
        if user_id is not None:
            query += " AND user_id = ?"
            params.append(user_id)
        if symbol is not None:
            query += " AND symbol = ?"
            params.append(symbol.upper())
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(bounded_limit)
        with get_db_connection() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [self._row_to_record(row) for row in rows]

    def _parse_prediction_text(self, content: str) -> dict[str, str | None]:
        return {
            "symbol": self._extract_labeled_value(content, ["Mã/cổ phiếu", "Mã", "Symbol"]),
            "horizon": self._extract_labeled_value(content, ["Khung thời gian", "Thời gian", "Horizon"]),
            "confidence": self._extract_labeled_value(content, ["Mức độ tự tin", "Độ tự tin", "Confidence"]),
        }

    def _extract_labeled_value(self, content: str, labels: list[str]) -> str | None:
        for label in labels:
            pattern = rf"(?:^|\n)\s*(?:\d+\.\s*)?{re.escape(label)}\s*:\s*(.+)"
            match = re.search(pattern, content, flags=re.IGNORECASE)
            if match:
                value = match.group(1).strip().strip(" -*")
                return value[:120].upper() if label in {"Mã/cổ phiếu", "Mã", "Symbol"} else value[:120]
        return None

    def _row_to_record(self, row) -> PredictionRecord:
        return PredictionRecord(
            id=row["id"],
            tenant_id=row["tenant_id"],
            project_id=row["project_id"],
            user_id=row["user_id"],
            session_id=row["session_id"],
            assistant_message_id=row["assistant_message_id"],
            symbol=row["symbol"],
            horizon=row["horizon"],
            prediction_text=row["prediction_text"],
            confidence=row["confidence"],
            inputs_json=row["inputs_json"],
            model=row["model"],
            provider=row["provider"],
            created_at=str(row["created_at"]),
            actual_outcome=row["actual_outcome"],
            evaluated_at=str(row["evaluated_at"]) if row["evaluated_at"] else None,
        )
