"""End-to-end chat flow with httpx mocked via respx."""

from __future__ import annotations

from uuid import uuid4

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.core.database import get_db_connection
from tests.conftest import make_ollama_chat_response


def _last_payload(route: respx.Route) -> dict:
    import json as _json

    return _json.loads(route.calls.last.request.content)


@pytest.mark.integration
def test_iot_happy_path(client: TestClient, mock_api: respx.MockRouter) -> None:
    mock_api.post("http://llama.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=make_ollama_chat_response("AQI 120: deo khau trang."))
    )
    resp = client.post(
        "/v1/chat",
        json={"project_id": "iot", "user_message": "AQI hom nay?"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["project_id"] == "iot"
    assert body["provider"] == "llama_cpp"
    assert "AQI" in body["content"]


@pytest.mark.integration
def test_vehix_happy_path(client: TestClient, mock_api: respx.MockRouter) -> None:
    mock_api.post("http://llama.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=make_ollama_chat_response("Gia thue xe 7 cho 1tr/ngay."))
    )
    resp = client.post(
        "/v1/chat",
        json={"project_id": "vehix", "user_message": "Gia xe 7 cho?"},
    )
    assert resp.status_code == 200
    assert "xe" in resp.json()["content"].lower()


@pytest.mark.integration
def test_stock_prediction_happy_path(client: TestClient, mock_api: respx.MockRouter) -> None:
    route = mock_api.post("http://llama.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=make_ollama_chat_response("1. Mã/cổ phiếu: VNM"))
    )

    resp = client.post(
        "/v1/chat",
        json={
            "tenant_id": "stock",
            "project_id": "stock_prediction",
            "user_message": "du doan VNM",
        },
    )

    assert resp.status_code == 200
    payload = _last_payload(route)
    assert "chứng khoán" in payload["messages"][0]["content"]


@pytest.mark.integration
def test_session_id_requires_user_name(client: TestClient, mock_api: respx.MockRouter) -> None:
    mock_api.post("http://llama.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=make_ollama_chat_response("ok"))
    )
    first = client.post(
        "/v1/chat",
        json={
            "tenant_id": "stock",
            "project_id": "stock_prediction",
            "user_name": "session-owner",
            "user_message": "du doan VNM",
        },
    )
    assert first.status_code == 200

    second = client.post(
        "/v1/chat",
        json={
            "tenant_id": "stock",
            "project_id": "stock_prediction",
            "session_id": first.json()["session_id"],
            "user_message": "reuse session",
        },
    )

    assert second.status_code == 403


@pytest.mark.integration
def test_session_id_cannot_cross_tenants(client: TestClient, mock_api: respx.MockRouter) -> None:
    mock_api.post("http://llama.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=make_ollama_chat_response("ok"))
    )
    first = client.post(
        "/v1/chat",
        json={
            "tenant_id": "stock",
            "project_id": "stock_prediction",
            "user_name": "tenant-user",
            "user_message": "du doan VNM",
        },
    )
    assert first.status_code == 200

    second = client.post(
        "/v1/chat",
        json={
            "tenant_id": "other",
            "project_id": "stock_prediction",
            "user_name": "tenant-user",
            "session_id": first.json()["session_id"],
            "user_message": "reuse session",
        },
    )

    assert second.status_code == 403


@pytest.mark.integration
def test_unknown_project_is_404(client: TestClient, mock_api: respx.MockRouter) -> None:
    resp = client.post(
        "/v1/chat",
        json={"project_id": "ghost", "user_message": "hi"},
    )
    assert resp.status_code == 404


@pytest.mark.integration
def test_local_provider_down_is_503(client: TestClient, mock_api: respx.MockRouter) -> None:
    mock_api.post("http://llama.test/v1/chat/completions").mock(
        side_effect=httpx.ConnectError("refused")
    )
    resp = client.post(
        "/v1/chat",
        json={
            "tenant_id": "error-tenant",
            "project_id": "iot",
            "user_name": "local-down-user",
            "user_message": "hi",
        },
    )
    assert resp.status_code == 503
    assert "local provider" in resp.json()["detail"].lower()


@pytest.mark.integration
def test_ollama_failure_does_not_persist_empty_assistant_message(
    client: TestClient, mock_api: respx.MockRouter
) -> None:
    mock_api.post("http://llama.test/v1/chat/completions").mock(
        side_effect=httpx.ConnectError("refused")
    )

    tenant_id = f"failed-save-{uuid4().hex}"
    resp = client.post(
        "/v1/chat",
        json={
            "tenant_id": tenant_id,
            "project_id": "iot",
            "user_name": "failed-save-user",
            "user_message": "hi",
        },
    )

    assert resp.status_code == 503
    with get_db_connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM messages m "
            "JOIN sessions s ON m.session_id = s.id "
            "WHERE s.tenant_id = ? AND s.project_id = ?",
            (tenant_id, "iot"),
        ).fetchone()
    assert row["cnt"] == 0


@pytest.mark.integration
def test_ollama_timeout_is_504(client: TestClient, mock_api: respx.MockRouter) -> None:
    mock_api.post("http://llama.test/v1/chat/completions").mock(
        side_effect=httpx.ReadTimeout("slow")
    )
    resp = client.post(
        "/v1/chat",
        json={"project_id": "iot", "user_message": "hi"},
    )
    assert resp.status_code == 504


@pytest.mark.integration
def test_ollama_vram_is_503(client: TestClient, mock_api: respx.MockRouter) -> None:
    mock_api.post("http://llama.test/v1/chat/completions").mock(
        return_value=httpx.Response(500, text="model requires more memory than available, out of memory")
    )
    resp = client.post(
        "/v1/chat",
        json={"project_id": "iot", "user_message": "hi"},
    )
    assert resp.status_code == 503
    assert "vram" in resp.json()["detail"].lower()


@pytest.mark.integration
def test_streaming_returns_sse_events(client: TestClient, mock_api: respx.MockRouter) -> None:
    import json as _json

    sse_body = (
        'data: {"choices": [{"delta": {"content": "Hello"}, "finish_reason": null}]}\n\n'
        'data: {"choices": [{"delta": {"content": " world"}, "finish_reason": null}]}\n\n'
        "data: [DONE]\n\n"
    )
    mock_api.post("http://llama.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, text=sse_body)
    )
    resp = client.post(
        "/v1/chat",
        json={"project_id": "iot", "user_message": "hi", "stream": True},
    )
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers["content-type"]

    events = []
    for line in resp.text.splitlines():
        if line.startswith("data: ") and line[6:] != "[DONE]":
            events.append(_json.loads(line[6:]))

    types = [e["type"] for e in events]
    assert types[0] == "start"
    assert "chunk" in types
    assert types[-1] == "done"

    content = "".join(e.get("content", "") for e in events if e["type"] == "chunk")
    assert content == "Hello world"

    done_event = events[-1]
    assert "session_id" in done_event
    assert "latency_ms" in done_event


@pytest.mark.integration
def test_history_capped_to_settings(client: TestClient, mock_api: respx.MockRouter) -> None:
    route = mock_api.post("http://llama.test/v1/chat/completions").mock(
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
    route = mock_api.post("http://llama.test/v1/chat/completions").mock(
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
    last_msg = payload["messages"][-1]
    # Images are embedded as OpenAI vision content-parts format
    assert isinstance(last_msg["content"], list)
    text_part = last_msg["content"][0]
    image_part = last_msg["content"][1]
    assert text_part["type"] == "text"
    assert image_part["type"] == "image_url"
    assert image_part["image_url"]["url"] == "data:image/jpeg;base64,aGVsbG8="


@pytest.mark.integration
def test_thinking_mode_uses_default_model_without_images(
    client: TestClient, mock_api: respx.MockRouter
) -> None:
    route = mock_api.post("http://llama.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=make_ollama_chat_response("ok"))
    )

    response = client.post(
        "/v1/chat",
        json={
            "project_id": "iot",
            "user_message": "Solve a complex problem",
            "model_mode": "thinking",
        },
    )

    assert response.status_code == 200
    payload = _last_payload(route)
    assert payload["model"] == "test-model:latest"
    assert "images" not in payload["messages"][-1]
