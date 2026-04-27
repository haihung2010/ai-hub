"""GET / and GET /health under both ok + degraded Ollama states."""

from __future__ import annotations

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from tests.conftest import make_ollama_tags_response


@pytest.mark.integration
def test_root_always_ok(client: TestClient, mock_api: respx.MockRouter) -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


@pytest.mark.integration
def test_health_ok_when_ollama_up(client: TestClient, mock_api: respx.MockRouter) -> None:
    mock_api.get("http://ollama.test/api/tags").mock(
        return_value=httpx.Response(
            200, json=make_ollama_tags_response(["gemma4:latest", "llama3:latest"])
        )
    )
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "gemma4:latest" in body["ollama"]["models"]


@pytest.mark.integration
def test_health_degraded_when_ollama_down(
    client: TestClient, mock_api: respx.MockRouter
) -> None:
    mock_api.get("http://ollama.test/api/tags").mock(
        side_effect=httpx.ConnectError("refused")
    )
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "degraded"
    assert body["ollama"]["models"] == []
