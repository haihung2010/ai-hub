"""Integration coverage for user-scoped chat and resume sessions endpoint."""

from __future__ import annotations

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from uuid import uuid4

from tests.conftest import make_ollama_chat_response


@pytest.mark.integration
def test_chat_returns_user_id_when_user_name_is_provided(
    client: TestClient, mock_api: respx.MockRouter
) -> None:
    mock_api.post("http://llama.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=make_ollama_chat_response("AQI 120"))
    )

    response = client.post(
        "/v1/chat",
        json={
            "project_id": "iot",
            "user_name": "hung-user-id",
            "user_message": "AQI hom nay?",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["user_id"]
    assert body["session_id"]


@pytest.mark.integration
def test_same_user_name_can_list_previous_sessions(
    client: TestClient, mock_api: respx.MockRouter
) -> None:
    mock_api.post("http://llama.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=make_ollama_chat_response("AQI 120"))
    )

    user_name = f"hung-resume-{uuid4().hex}"
    chat_response = client.post(
        "/v1/chat",
        json={
            "project_id": "iot",
            "user_name": user_name,
            "user_message": "AQI hom nay?",
        },
    )
    assert chat_response.status_code == 200

    sessions_response = client.get(
        f"/v1/users/{user_name}/sessions",
        params={"project_id": "iot"},
    )

    assert sessions_response.status_code == 200
    body = sessions_response.json()
    assert len(body) == 1
    assert body[0]["session_id"] == chat_response.json()["session_id"]
    assert "AQI" in body[0]["last_message_preview"]


@pytest.mark.integration
def test_unknown_user_name_returns_empty_sessions_list(client: TestClient) -> None:
    response = client.get(
        "/v1/users/ghost/sessions",
        params={"project_id": "iot"},
    )

    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.integration
def test_session_listing_is_tenant_scoped(
    client: TestClient, mock_api: respx.MockRouter
) -> None:
    mock_api.post("http://llama.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=make_ollama_chat_response("ok"))
    )
    user_name = f"tenant-resume-{uuid4().hex}"
    stock_response = client.post(
        "/v1/chat",
        json={
            "tenant_id": "stock",
            "project_id": "stock_prediction",
            "user_name": user_name,
            "user_message": "du doan VNM",
        },
    )
    assert stock_response.status_code == 200

    other_response = client.get(
        f"/v1/users/{user_name}/sessions",
        params={"tenant_id": "other", "project_id": "stock_prediction"},
    )
    stock_sessions = client.get(
        f"/v1/users/{user_name}/sessions",
        params={"tenant_id": "stock", "project_id": "stock_prediction"},
    )

    assert other_response.status_code == 200
    assert other_response.json() == []
    assert len(stock_sessions.json()) == 1
    assert stock_sessions.json()[0]["session_id"] == stock_response.json()["session_id"]
