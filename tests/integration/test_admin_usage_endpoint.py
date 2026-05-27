"""Admin usage endpoint exposes local resource metrics behind API-key auth."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.routes import admin as admin_routes
from app.services.usage_service import UsageEvent


@pytest.mark.integration
def test_admin_usage_requires_api_key(client: TestClient) -> None:
    client.headers.clear()

    response = client.get("/v1/admin/usage")

    assert response.status_code == 401


@pytest.mark.integration
def test_admin_usage_returns_resource_snapshot(client: TestClient) -> None:
    response = client.get("/v1/admin/usage")

    assert response.status_code == 200
    payload = response.json()
    assert payload["service"] == "ai-hub"
    assert payload["uptime_seconds"] >= 0
    assert payload["process"]["pid"] > 0
    assert payload["process"]["rss_mb"] >= 0
    assert "cpu" in payload
    assert "memory" in payload
    assert "disk" in payload
    assert payload["security"]["public_docs_enabled"] in {True, False}


@pytest.mark.integration
def test_admin_stats_returns_observability_breakdowns(client: TestClient) -> None:
    client.app.state.usage_service.record(
        UsageEvent(
            tenant_id="obs-tenant",
            project_id="test",
            provider="llama_cpp",
            model="local-gemma4-e4b-q8",
            route_alias="local",
            latency_ms=123.0,
            status_code=200,
            fallback_used=False,
            queue_wait_ms=4.0,
            route_reason="local_available",
        )
    )

    response = client.get("/v1/admin/stats")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total_requests"] >= 1
    assert payload["success_requests"] >= 1
    assert payload["fallback_requests"] >= 0
    assert "latency" in payload
    assert "queue_wait" in payload
    assert payload["queue_wait"]["requests"] >= 1
    assert any(row["provider"] == "llama_cpp" for row in payload["by_provider"])
    assert any(row["model"] == "local-gemma4-e4b-q8" for row in payload["by_model"])
    assert any(row["route_alias"] == "local" for row in payload["by_route"])
    assert any(row["route_reason"] == "local_available" for row in payload["by_route_reason"])
    assert payload["recent"]
    recent = payload["recent"][0]
    assert "queue_wait_ms" in recent
    assert "fallback_used" in recent
    assert "route_reason" in recent


@pytest.mark.integration
def test_admin_observability_alias_matches_stats_shape(client: TestClient) -> None:
    response = client.get("/v1/admin/observability")

    assert response.status_code == 200
    payload = response.json()
    assert "by_route_reason" in payload
    assert "recent" in payload


@pytest.mark.integration
def test_admin_model_switch_rejects_invalid_mode(client: TestClient) -> None:
    response = client.post("/v1/admin/model/switch", json={"mode": "bad"})

    assert response.status_code == 422


@pytest.mark.integration
def test_admin_model_switch_runs_whitelisted_mode(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_switch(mode: str) -> dict[str, object]:
        return {"mode": mode, "returncode": 0, "stdout": "ready", "stderr": ""}

    async def fake_list_models() -> list[str]:
        return ["local-gemma4-e4b-q8"]

    monkeypatch.setattr(admin_routes, "_run_model_switch", fake_switch)
    monkeypatch.setattr(client.app.state.local_provider, "list_models", fake_list_models)

    response = client.post("/v1/admin/model/switch", json={"mode": "lite"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] == "lite"
    assert payload["models"] == ["local-gemma4-e4b-q4"]
