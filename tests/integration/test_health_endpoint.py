"""GET / and GET /health under both ok + degraded local provider states."""

from __future__ import annotations

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

@pytest.mark.integration
def test_root_always_ok(client: TestClient, mock_api: respx.MockRouter) -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


@pytest.mark.integration
def test_health_ok_when_local_provider_up(client: TestClient, mock_api: respx.MockRouter) -> None:
    mock_api.get("http://llama.test/v1/models").mock(
        return_value=httpx.Response(
            200, json={"data": [{"id": "local-gemma4-e4b-q4"}, {"id": "other-local"}]}
        )
    )
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "local-gemma4-e4b-q4" in body["local"]["models"]


@pytest.mark.integration
def test_health_degraded_when_local_provider_down(
    client: TestClient, mock_api: respx.MockRouter
) -> None:
    mock_api.get("http://llama.test/v1/models").mock(
        side_effect=httpx.ConnectError("refused")
    )
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "degraded"
    assert body["local"]["models"] == []
