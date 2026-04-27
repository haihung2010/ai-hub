"""End-to-end chat flow with httpx mocked via respx."""

from __future__ import annotations

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from tests.conftest import make_ollama_chat_response


def _last_payload(route: respx.Route) -> dict:
    import json as _json

    return _json.loads(route.calls.last.request.content)


@pytest.mark.integration
def test_iot_happy_path(client: TestClient, mock_api: respx.MockRouter) -> None:
    mock_api.post("http://ollama.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=make_ollama_chat_response("AQI 120: deo khau trang."))
    )
    resp = client.post(
        "/v1/chat",
        json={"project_id": "iot", "user_message": "AQI hom nay?"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["project_id"] == "iot"
    assert body["provider"] == "ollama"
    assert "AQI" in body["content"]


@pytest.mark.integration
def test_vehix_happy_path(client: TestClient, mock_api: respx.MockRouter) -> None:
    mock_api.post("http://ollama.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=make_ollama_chat_response("Gia thue xe 7 cho 1tr/ngay."))
    )
    resp = client.post(
        "/v1/chat",
        json={"project_id": "vehix", "user_message": "Gia xe 7 cho?"},
    )
    assert resp.status_code == 200
    assert "xe" in resp.json()["content"].lower()


@pytest.mark.integration
def test_unknown_project_is_404(client: TestClient, mock_api: respx.MockRouter) -> None:
    resp = client.post(
        "/v1/chat",
        json={"project_id": "ghost", "user_message": "hi"},
    )
    assert resp.status_code == 404


@pytest.mark.integration
def test_ollama_down_is_503(client: TestClient, mock_api: respx.MockRouter) -> None:
    mock_api.post("http://ollama.test/v1/chat/completions").mock(
        side_effect=httpx.ConnectError("refused")
    )
    resp = client.post(
        "/v1/chat",
        json={"project_id": "iot", "user_message": "hi"},
    )
    assert resp.status_code == 503
    assert "ollama" in resp.json()["detail"].lower()


@pytest.mark.integration
def test_ollama_timeout_is_504(client: TestClient, mock_api: respx.MockRouter) -> None:
    mock_api.post("http://ollama.test/v1/chat/completions").mock(
        side_effect=httpx.ReadTimeout("slow")
    )
    resp = client.post(
        "/v1/chat",
        json={"project_id": "iot", "user_message": "hi"},
    )
    assert resp.status_code == 504


@pytest.mark.integration
def test_ollama_vram_is_503(client: TestClient, mock_api: respx.MockRouter) -> None:
    mock_api.post("http://ollama.test/v1/chat/completions").mock(
        return_value=httpx.Response(500, text="model requires more memory than available, out of memory")
    )
    resp = client.post(
        "/v1/chat",
        json={"project_id": "iot", "user_message": "hi"},
    )
    assert resp.status_code == 503
    assert "vram" in resp.json()["detail"].lower()


@pytest.mark.integration
def test_streaming_returns_501(client: TestClient, mock_api: respx.MockRouter) -> None:
    resp = client.post(
        "/v1/chat",
        json={"project_id": "iot", "user_message": "hi", "stream": True},
    )
    assert resp.status_code == 501


@pytest.mark.integration
def test_history_capped_to_settings(client: TestClient, mock_api: respx.MockRouter) -> None:
    route = mock_api.post("http://ollama.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=make_ollama_chat_response("ok"))
    )
    long_history = [
        {"role": "user", "content": f"msg {i}"} for i in range(12)
    ]
    resp = client.post(
        "/v1/chat",
        json={"project_id": "iot", "user_message": "now", "history": long_history},
    )
    assert resp.status_code == 200
    payload = _last_payload(route)
    # 1 system + 5 trimmed history + 1 user = 7 (MAX_HISTORY_MESSAGES=5 in test settings)
    assert len(payload["messages"]) == 7
    assert payload["messages"][0]["role"] == "system"
    assert payload["messages"][-1]["content"] == "now"


@pytest.mark.integration
def test_lite_mode_forwards_images_to_ollama(
    client: TestClient, mock_api: respx.MockRouter
) -> None:
    route = mock_api.post("http://ollama.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=make_ollama_chat_response("ok"))
    )

    response = client.post(
        "/v1/chat",
        json={
            "project_id": "iot",
            "user_message": "Analyze this image",
            "model_mode": "lite",
            "images": ["aGVsbG8="],
        },
    )

    assert response.status_code == 200
    payload = _last_payload(route)
    assert payload["messages"][-1]["images"] == ["aGVsbG8="]


@pytest.mark.integration
def test_normal_mode_does_not_forward_images_to_ollama(
    client: TestClient, mock_api: respx.MockRouter
) -> None:
    route = mock_api.post("http://ollama.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=make_ollama_chat_response("ok"))
    )

    response = client.post(
        "/v1/chat",
        json={
            "project_id": "iot",
            "user_message": "Ignore this image",
            "model_mode": "normal",
            "images": ["aGVsbG8="],
        },
    )

    assert response.status_code == 200
    payload = _last_payload(route)
    assert "images" not in payload["messages"][-1]
