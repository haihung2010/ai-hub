"""Integration tests for stock prediction audit endpoint."""

from __future__ import annotations

from uuid import uuid4

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from tests.conftest import make_ollama_chat_response


@pytest.mark.integration
def test_stock_chat_creates_prediction_record(
    client: TestClient, mock_api: respx.MockRouter
) -> None:
    content = """
1. Mã/cổ phiếu: VNM
2. Khung thời gian: 1 tuần
3. Quan điểm: Trung lập
5. Mức độ tự tin: Trung bình
"""
    mock_api.post("http://llama.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=make_ollama_chat_response(content))
    )

    user_name = f"stock-user-{uuid4().hex}"
    chat_response = client.post(
        "/v1/chat",
        json={
            "tenant_id": "stock",
            "project_id": "stock_prediction",
            "user_name": user_name,
            "user_message": "du doan VNM tuan nay",
        },
    )

    assert chat_response.status_code == 200
    records_response = client.get(
        "/v1/predictions",
        params={"tenant_id": "stock", "project_id": "stock_prediction", "user_name": user_name},
    )

    assert records_response.status_code == 200
    records = records_response.json()
    assert len(records) == 1
    assert records[0]["symbol"] == "VNM"
    assert "session_id" not in records[0]
    assert "user_id" not in records[0]
    assert "inputs_json" not in records[0]


@pytest.mark.integration
def test_predictions_endpoint_filters_by_tenant(
    client: TestClient, mock_api: respx.MockRouter
) -> None:
    content = "1. Mã/cổ phiếu: FPT\n2. Khung thời gian: 1 tháng\n5. Mức độ tự tin: Thấp"
    mock_api.post("http://llama.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=make_ollama_chat_response(content))
    )
    user_name = f"stock-user-{uuid4().hex}"

    response = client.post(
        "/v1/chat",
        json={
            "tenant_id": "stock",
            "project_id": "stock_prediction",
            "user_name": user_name,
            "user_message": "du doan FPT",
        },
    )
    assert response.status_code == 200

    other_tenant_response = client.get(
        "/v1/predictions",
        params={"tenant_id": "other", "project_id": "stock_prediction", "user_name": user_name},
    )

    assert other_tenant_response.status_code == 200
    assert other_tenant_response.json() == []


@pytest.mark.integration
def test_predictions_endpoint_requires_user_name(client: TestClient) -> None:
    response = client.get(
        "/v1/predictions",
        params={"tenant_id": "stock", "project_id": "stock_prediction"},
    )

    assert response.status_code == 422
