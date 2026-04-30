"""Unit tests for stock prediction audit storage."""

from __future__ import annotations

import pytest
from uuid import uuid4

from app.core.database import init_db
from app.services.history_service import HistoryService
from app.services.prediction_service import PredictionService


@pytest.fixture(autouse=True)
def _init_db() -> None:
    init_db()


@pytest.mark.unit
def test_maybe_store_from_chat_creates_stock_prediction_record() -> None:
    history = HistoryService()
    service = PredictionService()
    user_id = f"user-{uuid4().hex}"
    session_id = history.create_session("stock_prediction", user_id=user_id, tenant_id="stock")
    content = """
1. Mã/cổ phiếu: VNM
2. Khung thời gian: 1 tuần
3. Quan điểm: Trung lập
5. Mức độ tự tin: Trung bình
"""

    record = service.maybe_store_from_chat(
        tenant_id="stock",
        project_id="stock_prediction",
        user_id=user_id,
        session_id=session_id,
        content=content,
        model="model-a",
        provider="llama_cpp",
        inputs={"user_message": "du doan VNM"},
    )

    assert record is not None
    assert record.tenant_id == "stock"
    assert record.project_id == "stock_prediction"
    assert record.symbol == "VNM"
    assert record.horizon == "1 tuần"
    assert record.confidence == "Trung bình"


@pytest.mark.unit
def test_non_stock_project_is_not_stored() -> None:
    service = PredictionService()

    record = service.maybe_store_from_chat(
        tenant_id="default",
        project_id="iot",
        user_id=None,
        session_id="session-1",
        content="normal chat",
        model="model-a",
        provider="llama_cpp",
    )

    assert record is None


@pytest.mark.unit
def test_list_records_filters_by_tenant_and_symbol() -> None:
    history = HistoryService()
    service = PredictionService()
    user_id = f"user-{uuid4().hex}"
    stock_session = history.create_session("stock_prediction", user_id=user_id, tenant_id="stock")
    other_session = history.create_session("stock_prediction", user_id=user_id, tenant_id="other")

    service.create_record(
        tenant_id="stock",
        project_id="stock_prediction",
        user_id=user_id,
        session_id=stock_session,
        assistant_message_id=None,
        symbol="SSI",
        horizon=None,
        prediction_text="stock prediction",
        confidence=None,
        inputs_json="{}",
        model="model-a",
        provider="llama_cpp",
    )
    service.create_record(
        tenant_id="other",
        project_id="stock_prediction",
        user_id=user_id,
        session_id=other_session,
        assistant_message_id=None,
        symbol="SSI",
        horizon=None,
        prediction_text="other prediction",
        confidence=None,
        inputs_json="{}",
        model="model-a",
        provider="llama_cpp",
    )

    records = service.list_records(
        tenant_id="stock",
        project_id="stock_prediction",
        user_id=user_id,
        symbol="ssi",
    )

    assert len(records) == 1
    assert records[0].prediction_text == "stock prediction"
