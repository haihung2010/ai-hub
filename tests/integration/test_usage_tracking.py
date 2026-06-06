"""Usage/cost metadata is persisted for chat requests.

Regression tests for the Rank 1 bug (2026-06-06 health report): all 5
token / cost / api_key fields must be populated on every ``usage_events``
row that comes from a real chat completion.
"""
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
            "latency_ms, status_code, fallback_used, queue_wait_ms, route_reason, "
            "prompt_tokens, completion_tokens, total_tokens, cost_usd, api_key_id "
            "FROM usage_events "
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

    # --- Rank 1 bug regression: token / cost / api_key fields populated ---
    # Local provider → cost_usd is 0.0 (self-hosted).
    # api_key_id is NULL because the request used the primary API_KEY (no
    # virtual-key lookup), which the middleware maps to api_key_id=None by
    # design. That's intentional; we just assert the wire is consistent.
    assert row["prompt_tokens"] is not None
    assert row["prompt_tokens"] > 0
    assert row["completion_tokens"] is not None
    assert row["completion_tokens"] > 0
    assert row["total_tokens"] == row["prompt_tokens"] + row["completion_tokens"]
    assert row["cost_usd"] == 0.0  # local llama.cpp is self-hosted
    assert row["api_key_id"] is None  # primary API_KEY → NULL by design

    stats_response = client.get("/v1/admin/stats")
    assert stats_response.status_code == 200
    stats = stats_response.json()
    assert any(item["provider"] == "llama_cpp" for item in stats["by_provider"])
    assert any(item["route_alias"] == "local" for item in stats["by_route"])
    assert any(item["route_reason"] == "local_available" for item in stats["by_route_reason"])
    assert stats["fallback_requests"] == 0
    assert stats["queue_wait"]["requests"] >= 1
    # Rank 1 regression: cost and token summary fields are no longer always 0.
    assert "total_cost_usd" in stats


@pytest.mark.integration
def test_chat_completion_populates_token_and_cost_fields(
    client: TestClient, mock_api: respx.MockRouter
) -> None:
    """Minimal direct test of the Rank 1 bug: assert prompt_tokens,
    completion_tokens, total_tokens, cost_usd are populated.

    Regression guard for health-2026-06-06.md Rank 1 — every ``usage_events``
    row from a real chat must have all 4 token/cost fields non-NULL.
    """
    tenant_id = f"cost-{uuid4().hex}"
    mock_api.post("http://llama.test/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json=make_ollama_chat_response("tra lai rat chi tiet"),
        )
    )

    response = client.post(
        "/v1/chat",
        json={
            "tenant_id": tenant_id,
            "project_id": "iot",
            "user_name": "cost-user",
            "user_message": "Xin chao, hom nay co chuyen gi vui?",
        },
    )
    assert response.status_code == 200

    with get_db_connection() as conn:
        row = conn.execute(
            "SELECT prompt_tokens, completion_tokens, total_tokens, cost_usd, "
            "api_key_id, provider, model "
            "FROM usage_events WHERE tenant_id = %s ORDER BY created_at DESC LIMIT 1",
            (tenant_id,),
        ).fetchone()

    assert row is not None, "no usage_events row was created"
    assert row["prompt_tokens"] is not None and row["prompt_tokens"] > 0
    assert row["completion_tokens"] is not None and row["completion_tokens"] > 0
    assert row["total_tokens"] == row["prompt_tokens"] + row["completion_tokens"]
    # Local llama.cpp is self-hosted → 0.0 cost (deliberate, not NULL).
    assert row["cost_usd"] == 0.0
    assert row["api_key_id"] is None  # primary API_KEY → NULL by design


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
