"""Unit tests for A2A (Agent2Agent) routes.

Covers:
- GET /v1/a2a/agent-card: discovery, skills, capabilities
- POST /v1/a2a/jsonrpc: JSON-RPC 2.0 envelope, all 4 methods,
  error codes (-32600, -32601, -32700, -32001, -32003), task lifecycle.

These tests use respx to mock llama.cpp, so they are self-contained.
Marked ``no_isolated_db`` to skip DB truncation.
"""
from __future__ import annotations

import json
import uuid

import httpx
import pytest
import respx

pytestmark = [pytest.mark.unit, pytest.mark.no_isolated_db]


LLAMA_URL = "http://llama.test/v1/chat/completions"


def _ollama_response(content: str = "Xin chào!") -> dict:
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "model": "test-model:latest",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
    }


def _send_message_params(message: str, **kwargs) -> dict:
    """Build a valid SendMessage params dict."""
    return {
        "message": {"role": "user", "parts": [{"kind": "text", "text": message}]},
        **kwargs,
    }


# ──────────────────────────────────────────────────────────────────────
# AgentCard discovery
# ──────────────────────────────────────────────────────────────────────


def test_agent_card_discovery(client) -> None:
    """GET /v1/a2a/agent-card returns a valid AgentCard manifest."""
    resp = client.get("/v1/a2a/agent-card")
    assert resp.status_code == 200
    card = resp.json()
    # Required fields
    assert card["name"]
    assert card["description"]
    assert card["version"] == "1.0.0"
    assert card["url"].endswith("/v1/a2a/jsonrpc")
    assert card["preferred_transport"] == "http+jsonrpc"
    # Auth
    assert "apiKey" in card["authentication"]["schemes"]
    # Skills (3 expected)
    skill_ids = {s["id"] for s in card["skills"]}
    assert "fanpage_chat" in skill_ids
    assert "product_lookup" in skill_ids
    assert "escalate_human" in skill_ids
    # Capabilities
    assert card["capabilities"]["streaming"] is False


# ──────────────────────────────────────────────────────────────────────
# JSON-RPC envelope
# ──────────────────────────────────────────────────────────────────────


def test_jsonrpc_parse_error(client) -> None:
    """-32700 Parse error on invalid JSON body."""
    resp = client.post(
        "/v1/a2a/jsonrpc",
        content=b"not json",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 200  # JSON-RPC errors return 200 with error body
    body = resp.json()
    assert body["error"]["code"] == -32700


def test_jsonrpc_invalid_request(client) -> None:
    """-32600 Invalid request when envelope is malformed."""
    resp = client.post(
        "/v1/a2a/jsonrpc",
        json={"foo": "bar"},  # missing jsonrpc/method
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["error"]["code"] == -32600


def test_jsonrpc_method_not_found(client) -> None:
    """-32601 Method not found for unknown method."""
    resp = client.post(
        "/v1/a2a/jsonrpc",
        json={"jsonrpc": "2.0", "method": "NonExistent", "id": 1},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["error"]["code"] == -32601
    assert "NonExistent" in body["error"]["message"]


# ──────────────────────────────────────────────────────────────────────
# SendMessage
# ──────────────────────────────────────────────────────────────────────


def test_send_message_returns_task(client) -> None:
    """SendMessage: returns a Task with the agent's reply in history."""
    with respx.mock:
        respx.post(LLAMA_URL).mock(
            return_value=httpx.Response(200, json=_ollama_response("Xin chào bạn!"))
        )
        resp = client.post(
            "/v1/a2a/jsonrpc",
            json={
                "jsonrpc": "2.0",
                "id": "req-1",
                "method": "SendMessage",
                "params": _send_message_params("Xin chào"),
            },
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["jsonrpc"] == "2.0"
    assert body["id"] == "req-1"
    task = body["result"]
    assert task["status"]["state"] == "completed"
    # History: 1 user message + 1 agent reply
    assert len(task["history"]) == 2
    assert task["history"][0]["role"] == "user"
    assert task["history"][1]["role"] == "agent"
    # Artifact: agent reply
    assert len(task["artifacts"]) == 1
    assert task["artifacts"][0]["name"] == "assistant_reply"
    assert "Xin chào bạn" in task["artifacts"][0]["parts"][0]["text"]


def test_send_message_invalid_params(client) -> None:
    """-32602 Invalid params when message is missing."""
    resp = client.post(
        "/v1/a2a/jsonrpc",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "SendMessage",
            "params": {"foo": "bar"},  # missing message
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["error"]["code"] == -32602


# ──────────────────────────────────────────────────────────────────────
# GetTask
# ──────────────────────────────────────────────────────────────────────


def test_get_task_retrieves_existing(client) -> None:
    """GetTask: returns the same task that SendMessage just created."""
    with respx.mock:
        respx.post(LLAMA_URL).mock(
            return_value=httpx.Response(200, json=_ollama_response("ok"))
        )
        send = client.post(
            "/v1/a2a/jsonrpc",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "SendMessage",
                "params": _send_message_params("hello"),
            },
        )
    task = send.json()["result"]
    task_id = task["id"]

    resp = client.post(
        "/v1/a2a/jsonrpc",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "GetTask",
            "params": {"id": task_id},
        },
    )
    assert resp.status_code == 200
    assert resp.json()["result"]["id"] == task_id


def test_get_task_not_found(client) -> None:
    """-32001 TaskNotFound for unknown task ID."""
    resp = client.post(
        "/v1/a2a/jsonrpc",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "GetTask",
            "params": {"id": "nonexistent-task-id"},
        },
    )
    assert resp.status_code == 200
    assert resp.json()["error"]["code"] == -32001


def test_get_task_invalid_params_missing_id(client) -> None:
    """-32602 Invalid params when id is missing."""
    resp = client.post(
        "/v1/a2a/jsonrpc",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "GetTask",
            "params": {},
        },
    )
    assert resp.status_code == 200
    assert resp.json()["error"]["code"] == -32602


# ──────────────────────────────────────────────────────────────────────
# ListTasks
# ──────────────────────────────────────────────────────────────────────


def test_list_tasks_returns_array(client) -> None:
    """ListTasks: returns {'tasks': [...]} wrapper."""
    resp = client.post(
        "/v1/a2a/jsonrpc",
        json={"jsonrpc": "2.0", "id": 1, "method": "ListTasks", "params": {}},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "tasks" in body["result"]
    assert isinstance(body["result"]["tasks"], list)


# ──────────────────────────────────────────────────────────────────────
# CancelTask
# ──────────────────────────────────────────────────────────────────────


def test_cancel_task_marks_canceled(client) -> None:
    """CancelTask: marks an active task as CANCELED."""
    with respx.mock:
        respx.post(LLAMA_URL).mock(
            return_value=httpx.Response(200, json=_ollama_response("ok"))
        )
        send = client.post(
            "/v1/a2a/jsonrpc",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "SendMessage",
                "params": _send_message_params("hi"),
            },
        )
    task_id = send.json()["result"]["id"]

    cancel = client.post(
        "/v1/a2a/jsonrpc",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "CancelTask",
            "params": {"id": task_id},
        },
    )
    assert cancel.status_code == 200
    # Completed tasks can't be cancelled (UnsupportedOperation)
    assert cancel.json()["error"]["code"] == -32003


def test_cancel_task_not_found(client) -> None:
    """-32001 TaskNotFound for unknown task ID."""
    resp = client.post(
        "/v1/a2a/jsonrpc",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "CancelTask",
            "params": {"id": "unknown-id"},
        },
    )
    assert resp.status_code == 200
    assert resp.json()["error"]["code"] == -32001


# ──────────────────────────────────────────────────────────────────────
# Integration: A2A response goes through real AI Hub chat pipeline
# ──────────────────────────────────────────────────────────────────────


def test_send_message_passes_correct_user_message_to_llama(client) -> None:
    """Verify the A2A text part is what gets sent to AI Hub / llama.cpp."""
    with respx.mock:
        captured = {}

        def _handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            captured["messages"] = payload.get("messages", [])
            return httpx.Response(200, json=_ollama_response("ack"))

        respx.post(LLAMA_URL).mock(side_effect=_handler)
        client.post(
            "/v1/a2a/jsonrpc",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "SendMessage",
                "params": _send_message_params("Giá sản phẩm A bao nhiêu?"),
            },
        )
    msgs = captured["messages"]
    assert msgs[-1]["role"] == "user"
    assert "Giá sản phẩm A bao nhiêu" in msgs[-1]["content"]
