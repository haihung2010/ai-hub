"""Integration tests for streaming error handling — Ollama down during stream."""

from __future__ import annotations

import json as _json

import httpx
import pytest
import respx
from fastapi.testclient import TestClient


@pytest.mark.integration
def test_ollama_unavailable_during_stream_emits_error_event(
    client: TestClient, mock_api: respx.MockRouter
) -> None:
    mock_api.post("http://llama.test/v1/chat/completions").mock(
        side_effect=httpx.ConnectError("connection refused")
    )
    resp = client.post(
        "/v1/chat",
        json={"project_id": "iot", "user_message": "hi", "stream": True},
    )
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers["content-type"]

    events = [
        _json.loads(line[6:])
        for line in resp.text.splitlines()
        if line.startswith("data: ") and line[6:] != "[DONE]"
    ]
    error_events = [e for e in events if e.get("type") == "error"]
    assert error_events, "expected an error SSE event"
    assert error_events[0]["code"] == 503  # OllamaUnavailable maps to HTTP 503


@pytest.mark.integration
def test_stream_timeout_emits_error_event(
    client: TestClient, mock_api: respx.MockRouter
) -> None:
    mock_api.post("http://llama.test/v1/chat/completions").mock(
        side_effect=httpx.ReadTimeout("timed out")
    )
    resp = client.post(
        "/v1/chat",
        json={"project_id": "iot", "user_message": "hi", "stream": True},
    )
    assert resp.status_code == 200

    events = [
        _json.loads(line[6:])
        for line in resp.text.splitlines()
        if line.startswith("data: ") and line[6:] != "[DONE]"
    ]
    error_events = [e for e in events if e.get("type") == "error"]
    assert error_events
    assert error_events[0]["code"] == 504  # UpstreamTimeout maps to HTTP 504


@pytest.mark.integration
def test_vram_exhausted_during_stream_emits_error_event(
    client: TestClient, mock_api: respx.MockRouter
) -> None:
    mock_api.post("http://llama.test/v1/chat/completions").mock(
        return_value=httpx.Response(500, text="out of memory: VRAM exhausted")
    )
    resp = client.post(
        "/v1/chat",
        json={"project_id": "iot", "user_message": "hi", "stream": True},
    )
    assert resp.status_code == 200

    events = [
        _json.loads(line[6:])
        for line in resp.text.splitlines()
        if line.startswith("data: ") and line[6:] != "[DONE]"
    ]
    error_events = [e for e in events if e.get("type") == "error"]
    assert error_events
    assert error_events[0]["code"] == 503  # VramExhausted maps to HTTP 503


@pytest.mark.integration
def test_stream_ends_with_done_sentinel(
    client: TestClient, mock_api: respx.MockRouter
) -> None:
    sse_body = (
        'data: {"choices": [{"delta": {"content": "hi"}, "finish_reason": null}]}\n\n'
        "data: [DONE]\n\n"
    )
    mock_api.post("http://llama.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, text=sse_body)
    )
    resp = client.post(
        "/v1/chat",
        json={"project_id": "iot", "user_message": "hi", "stream": True},
    )
    assert resp.text.strip().endswith("[DONE]")


@pytest.mark.integration
def test_stream_error_still_ends_with_done_sentinel(
    client: TestClient, mock_api: respx.MockRouter
) -> None:
    mock_api.post("http://llama.test/v1/chat/completions").mock(
        side_effect=httpx.ConnectError("refused")
    )
    resp = client.post(
        "/v1/chat",
        json={"project_id": "iot", "user_message": "hi", "stream": True},
    )
    assert resp.text.strip().endswith("[DONE]")
