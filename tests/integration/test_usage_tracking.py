"""Usage/cost metadata is persisted for chat requests."""

from __future__ import annotations

from uuid import uuid4

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.core.database import get_db_connection
from tests.conftest import make_ollama_chat_response


@pytest.mark.integration
def test_chat_response_includes_usage_metadata_and_persists_event(
    client: TestClient, mock_api: respx.MockRouter
) -> None:
    tenant_id = f"usage-{uuid4().hex}"
    mock_api.post("http://llama.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=make_ollama_chat_response("usage ok"))
    )

    response = client.post(
        "/v1/chat",
        json={
            "tenant_id": tenant_id,
            "project_id": "iot",
            "user_name": "usage-user",
            "user_message": "hello",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["route"] == "local"
    assert payload["latency_ms"] >= 0
    assert payload["fallback_used"] is False
    assert payload["queue_wait_ms"] >= 0
    assert payload["route_reason"] == "local_available"
    assert payload["sources"] == []
    assert payload["usage"] is None

    with get_db_connection() as conn:
        row = conn.execute(
            "SELECT tenant_id, project_id, session_id, provider, model, route_alias, "
            "latency_ms, status_code, fallback_used, queue_wait_ms, route_reason FROM usage_events "
            "WHERE tenant_id = %s ORDER BY created_at DESC LIMIT 1",
            (tenant_id,),
        ).fetchone()

    assert row is not None
    assert row["project_id"] == "iot"
    assert row["session_id"] == payload["session_id"]
    assert row["provider"] == "llama_cpp"
    assert row["model"] == payload["model"]
    assert row["route_alias"] == "local"
    assert row["latency_ms"] >= 0
    assert row["status_code"] == 200
    assert row["fallback_used"] == 0
    assert row["queue_wait_ms"] >= 0
    assert row["route_reason"] == "local_available"

    stats_response = client.get("/v1/admin/stats")
    assert stats_response.status_code == 200
    stats = stats_response.json()
    assert any(item["provider"] == "llama_cpp" for item in stats["by_provider"])
    assert any(item["route_alias"] == "local" for item in stats["by_route"])
    assert any(item["route_reason"] == "local_available" for item in stats["by_route_reason"])
    assert stats["fallback_requests"] == 0
    assert stats["queue_wait"]["requests"] >= 1


@pytest.mark.integration
def test_admin_usage_includes_request_usage_summary(client: TestClient) -> None:
    response = client.get("/v1/admin/usage")

    assert response.status_code == 200
    payload = response.json()
    assert "request_usage" in payload
    request_usage = payload["request_usage"]
    assert "total_requests" in request_usage
    assert "by_provider" in request_usage
    assert "latency" in request_usage
    assert "queue_wait" in request_usage
    assert "by_route_reason" in request_usage
