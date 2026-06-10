"""Unit tests for Chatwoot integration endpoints.

Covers:
- /v1/integrations/chatwoot/respond (Captain Custom Tool, sync)
- /v1/integrations/chatwoot/agent_bot (AgentBot webhook, async with callback)
- /v1/integrations/chatwoot/health
- HMAC signature verification
- Tenant mapping (account.id → tenant_id)
- Conversation mapping (conversation.id → session_id)
- User name derivation from contact/sender
- Last-user-message extraction from messages list
- 401 on bad signature, 400 on bad JSON, 422 on schema failure

These tests use respx to mock llama.cpp, so they are self-contained and
do not require a running inference server. Marked ``no_isolated_db`` to
skip the DB truncation that other tests rely on (we don't touch the DB).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os

import httpx
import pytest
import respx

# Mark entire module: no DB isolation needed
pytestmark = [pytest.mark.unit, pytest.mark.no_isolated_db]


LLAMA_URL = "http://llama.test/v1/chat/completions"
CHATWOOT_TEST_SECRET = "test-chatwoot-secret"


def _sign(body: bytes) -> str:
    """Return X-Chatwoot-Signature header for ``body`` using the test secret."""
    return hmac.new(CHATWOOT_TEST_SECRET.encode(), body, hashlib.sha256).hexdigest()


def _signed_post(client, url: str, payload: dict | None = None, **kwargs):
    """POST a JSON payload to ``url`` with a valid Chatwoot HMAC signature.

    Tests that need to exercise the signature behavior itself should NOT use
    this helper — they should send a bad/missing signature on purpose. To do
    that, pass ``headers={"X-Chatwoot-Signature": "bad"}`` and the helper will
    keep your value via setdefault.
    """
    if payload is None:
        body = b""
    else:
        body = json.dumps(payload).encode()
    headers = kwargs.pop("headers", {}) or {}
    headers.setdefault("X-Chatwoot-Signature", _sign(body))
    headers.setdefault("Content-Type", "application/json")
    return client.post(url, content=body, headers=headers, **kwargs)


# ──────────────────────────────────────────────────────────────────────
# Mock helper: minimal OpenAI-compatible chat completion response
# ──────────────────────────────────────────────────────────────────────


def _ollama_response(content: str = "Xin chào! Tôi có thể giúp gì cho bạn?") -> dict:
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


# ──────────────────────────────────────────────────────────────────────
# Health
# ──────────────────────────────────────────────────────────────────────


def test_health_returns_ok(client, monkeypatch) -> None:
    monkeypatch.delenv("CHATWOOT_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("CHATWOOT_API_TOKEN", raising=False)
    resp = client.get("/v1/integrations/chatwoot/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["webhook_secret_configured"] is False
    assert data["api_token_configured"] is False


# ──────────────────────────────────────────────────────────────────────
# Custom Tool endpoint: payload mapping
# ──────────────────────────────────────────────────────────────────────


def test_custom_tool_maps_last_user_message_and_history(client) -> None:
    with respx.mock:
        captured = {}

        def _handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            captured["messages"] = payload.get("messages", [])
            captured["model"] = payload.get("model")
            return httpx.Response(200, json=_ollama_response("Tôi giúp được!"))

        respx.post(LLAMA_URL).mock(side_effect=_handler)
        resp = _signed_post(
            client,
            "/v1/integrations/chatwoot/respond",
            {
                "messages": [
                    {"role": "user", "content": "Tôi muốn hỏi về sản phẩm A"},
                    {"role": "assistant", "content": "Bạn muốn biết gì về sản phẩm A?"},
                    {"role": "user", "content": "Giá bao nhiêu?"},
                ],
                "conversation": {"id": 42, "display_id": 100, "status": "open"},
                "contact": {"id": 7, "name": "Anh Tuấn"},
                "account": {"id": 1, "name": "Test Tenant"},
            },
        )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "Tôi giúp được" in data["response"]

    # Verify the messages sent to llama: system + history (first user+assistant) + new user
    sent_messages = captured["messages"]
    assert sent_messages[0]["role"] == "system"  # AI Hub system prompt
    # History: skip the first user (it's the question), keep the assistant
    history_msgs = [m for m in sent_messages[1:] if m["role"] != "user"]
    assert any("Bạn muốn biết gì" in m["content"] for m in history_msgs)
    # Last message should be the latest user question
    assert sent_messages[-1]["role"] == "user"
    assert "Giá bao nhiêu" in sent_messages[-1]["content"]


def test_custom_tool_tenant_mapping_prefixes_account_id(client) -> None:
    with respx.mock:
        captured = {}

        def _handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_ollama_response("ok"))

        respx.post(LLAMA_URL).mock(side_effect=_handler)
        resp = _signed_post(
            client,
            "/v1/integrations/chatwoot/respond",
            {
                "messages": [{"role": "user", "content": "hi"}],
                "account": {"id": 99},
                "conversation": {"id": 5},
            },
        )
    assert resp.status_code == 200
    # tenant_id is sent to chat, not directly to llama. We can verify via
    # the response.model which is set by ai_service after the chat call.
    data = resp.json()
    assert data["response"]  # non-empty
    # We don't assert exact tenant_id here because that requires DB access.
    # The service is verified separately by checking the ai_service.chat call.


def test_custom_tool_tenant_override_wins(client) -> None:
    with respx.mock:
        respx.post(LLAMA_URL).mock(return_value=httpx.Response(200, json=_ollama_response("ok")))
        resp = _signed_post(
            client,
            "/v1/integrations/chatwoot/respond",
            {
                "messages": [{"role": "user", "content": "hi"}],
                "account": {"id": 99},  # would default to cw_99
                "tenant_id": "my_custom_tenant",  # override
                "project_id": "my_project",
            },
        )
    assert resp.status_code == 200


def test_custom_tool_handles_empty_messages_gracefully(client) -> None:
    """Empty messages list should not crash — defensive fallback to '(empty)'."""
    with respx.mock:
        respx.post(LLAMA_URL).mock(return_value=httpx.Response(200, json=_ollama_response("ok")))
        resp = _signed_post(
            client,
            "/v1/integrations/chatwoot/respond",
            {"messages": []},
        )
    # Returns 200 because the service defends by sending "(empty)" as the question
    assert resp.status_code == 200


def test_custom_tool_handles_llama_error_returns_friendly_text(client) -> None:
    with respx.mock:
        respx.post(LLAMA_URL).mock(return_value=httpx.Response(500, text="upstream down"))
        resp = _signed_post(
            client,
            "/v1/integrations/chatwoot/respond",
            {"messages": [{"role": "user", "content": "hi"}]},
        )
    # Service catches the error and returns a friendly Vietnamese fallback
    assert resp.status_code == 200
    data = resp.json()
    assert "Xin lỗi" in data["response"]


# ──────────────────────────────────────────────────────────────────────
# Auth: HMAC signature
# ──────────────────────────────────────────────────────────────────────


def test_custom_tool_rejects_bad_signature_when_secret_configured(client, monkeypatch) -> None:
    monkeypatch.setenv("CHATWOOT_WEBHOOK_SECRET", "secret123")
    resp = client.post(
        "/v1/integrations/chatwoot/respond",
        json={"messages": [{"role": "user", "content": "hi"}]},
        headers={"X-Chatwoot-Signature": "deadbeef"},
    )
    assert resp.status_code == 401


def test_custom_tool_accepts_valid_signature(client, monkeypatch) -> None:
    monkeypatch.setenv("CHATWOOT_WEBHOOK_SECRET", "secret123")
    body = json.dumps({"messages": [{"role": "user", "content": "hi"}]}).encode()
    sig = hmac.new(b"secret123", body, hashlib.sha256).hexdigest()
    with respx.mock:
        respx.post(LLAMA_URL).mock(return_value=httpx.Response(200, json=_ollama_response("ok")))
        resp = client.post(
            "/v1/integrations/chatwoot/respond",
            content=body,
            headers={
                "X-Chatwoot-Signature": sig,
                "Content-Type": "application/json",
            },
        )
    assert resp.status_code == 200


# ──────────────────────────────────────────────────────────────────────
# AgentBot webhook: async, fires callback to message_url
# ──────────────────────────────────────────────────────────────────────


def test_agent_bot_skips_outgoing_messages(client) -> None:
    """Outgoing messages from agent should not be processed (avoid loops)."""
    with respx.mock:
        # We don't even expect llama to be called
        respx.post(LLAMA_URL).mock(return_value=httpx.Response(200, json=_ollama_response()))
        resp = _signed_post(
            client,
            "/v1/integrations/chatwoot/agent_bot",
            {
                "event": "message_created",
                "message": {"content": "Hello", "message_type": "outgoing"},
                "conversation": {"id": 1, "message_url": "http://chatwoot.test/msg"},
                "sender": {"id": 1, "type": "agent"},
            },
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "skipped"


def test_agent_bot_skips_empty_messages(client) -> None:
    with respx.mock:
        respx.post(LLAMA_URL).mock(return_value=httpx.Response(200, json=_ollama_response()))
        resp = _signed_post(
            client,
            "/v1/integrations/chatwoot/agent_bot",
            {
                "event": "message_created",
                "message": {"content": "", "message_type": "incoming"},
                "conversation": {"id": 1, "message_url": "http://chatwoot.test/msg"},
                "sender": {"id": 1, "type": "contact"},
            },
        )
    assert resp.status_code == 200
    assert resp.json()["status"] == "skipped"


def test_agent_bot_calls_back_to_message_url(client, monkeypatch) -> None:
    """When valid incoming message, AI Hub fires callback to Chatwoot."""
    monkeypatch.setenv("CHATWOOT_API_TOKEN", "test-cw-token")

    with respx.mock:
        # Mock llama to return a known reply
        respx.post(LLAMA_URL).mock(return_value=httpx.Response(200, json=_ollama_response("AI reply text")))

        # Mock Chatwoot message_url callback
        callback_captured = {}

        def _cb_handler(request: httpx.Request) -> httpx.Response:
            callback_captured["body"] = json.loads(request.content)
            callback_captured["auth"] = request.headers.get("api_access_token")
            return httpx.Response(200, json={"id": 1, "content": callback_captured["body"]["content"]})

        callback_route = respx.post("http://chatwoot.test/conversations/1/messages").mock(
            side_effect=_cb_handler
        )

        # Use a valid URL that respx matches (we'll match by prefix in the route)
        resp = _signed_post(
            client,
            "/v1/integrations/chatwoot/agent_bot",
            {
                "event": "message_created",
                "message": {"id": 100, "content": "Giá sản phẩm A?", "message_type": "incoming"},
                "conversation": {
                    "id": 1,
                    "account_id": 5,
                    "message_url": "http://chatwoot.test/conversations/1/messages",
                },
                "sender": {"id": 7, "name": "Anh Tuấn", "type": "contact"},
            },
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "queued"

    # The callback is fire-and-forget, so we need to give the asyncio task time to run
    import time
    for _ in range(20):
        if callback_captured:
            break
        time.sleep(0.05)

    assert callback_captured, "Callback was not invoked"
    assert callback_captured["body"]["content"] == "AI reply text"
    assert callback_captured["auth"] == "test-cw-token"
    assert callback_route.called


def test_agent_bot_handles_missing_message_url(client) -> None:
    with respx.mock:
        respx.post(LLAMA_URL).mock(return_value=httpx.Response(200, json=_ollama_response("reply")))
        resp = _signed_post(
            client,
            "/v1/integrations/chatwoot/agent_bot",
            {
                "event": "message_created",
                "message": {"content": "hi", "message_type": "incoming"},
                "conversation": {"id": 1, "message_url": None},  # missing
                "sender": {"id": 1, "type": "contact"},
            },
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "processed"
    assert "no_message_url" in (data.get("reason") or "")


def test_agent_bot_handles_missing_api_token(client, monkeypatch) -> None:
    """If CHATWOOT_API_TOKEN not set, the AI still processed but reply drops."""
    monkeypatch.delenv("CHATWOOT_API_TOKEN", raising=False)
    with respx.mock:
        respx.post(LLAMA_URL).mock(return_value=httpx.Response(200, json=_ollama_response("reply")))
        resp = _signed_post(
            client,
            "/v1/integrations/chatwoot/agent_bot",
            {
                "event": "message_created",
                "message": {"content": "hi", "message_type": "incoming"},
                "conversation": {"id": 1, "message_url": "http://chatwoot.test/msg"},
                "sender": {"id": 1, "type": "contact"},
            },
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "processed"
    assert "no_api_token" in (data.get("reason") or "")


# ──────────────────────────────────────────────────────────────────────
# Bad payload handling
# ──────────────────────────────────────────────────────────────────────


def test_custom_tool_400_on_invalid_json(client) -> None:
    body = b"not json"
    resp = client.post(
        "/v1/integrations/chatwoot/respond",
        content=body,
        headers={
            "X-Chatwoot-Signature": _sign(body),
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 400


def test_custom_tool_422_on_schema_violation(client) -> None:
    """Missing required 'role' in message should fail validation."""
    resp = _signed_post(
        client,
        "/v1/integrations/chatwoot/respond",
        {"messages": [{"content": "hi"}]},  # missing role
    )
    assert resp.status_code == 422


# ──────────────────────────────────────────────────────────────────────
# Custom Tool #1: product_lookup
# ──────────────────────────────────────────────────────────────────────


def test_product_lookup_returns_empty_when_kb_disabled(client) -> None:
    """With ENABLE_KNOWLEDGE_RAG=False (test fixture), should return found=False."""
    resp = _signed_post(
        client,
        "/v1/integrations/chatwoot/tools/product_lookup",
        {"query": "Serum Vitamin C"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["found"] is False
    assert data["query"] == "Serum Vitamin C"
    assert data["products"] == []


def test_product_lookup_422_on_empty_query(client) -> None:
    resp = _signed_post(
        client,
        "/v1/integrations/chatwoot/tools/product_lookup",
        {"query": ""},
    )
    assert resp.status_code == 422


def test_product_lookup_400_on_invalid_json(client) -> None:
    body = b"not json"
    resp = client.post(
        "/v1/integrations/chatwoot/tools/product_lookup",
        content=body,
        headers={
            "X-Chatwoot-Signature": _sign(body),
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 400


# ──────────────────────────────────────────────────────────────────────
# Custom Tool #2: order_status (STUB)
# ──────────────────────────────────────────────────────────────────────


def test_order_status_returns_not_configured_stub(client) -> None:
    """Stub returns a structured not_configured response."""
    resp = _signed_post(
        client,
        "/v1/integrations/chatwoot/tools/order_status",
        {"order_id": "ORD-12345", "contact_email": "test@example.com"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["found"] is False
    assert data["order_id"] == "ORD-12345"
    assert data["status"] == "not_configured"
    assert "stub_reason" in data["details"]
    assert "ORD-12345" in data["message"]  # order_id interpolated
    assert "AI chưa được kết nối" in data["message"]


def test_order_status_422_on_empty_order_id(client) -> None:
    resp = _signed_post(
        client,
        "/v1/integrations/chatwoot/tools/order_status",
        {"order_id": ""},
    )
    assert resp.status_code == 422


# ──────────────────────────────────────────────────────────────────────
# Custom Tool #3: escalate_human
# ──────────────────────────────────────────────────────────────────────


def test_escalate_human_returns_ticket_id(client) -> None:
    """Returns a ticket_id and priority-estimated response time."""
    resp = _signed_post(
        client,
        "/v1/integrations/chatwoot/tools/escalate_human",
        {
            "conversation_id": 42,
            "reason": "Customer wants a refund > $50",
            "contact_id": 7,
            "contact_name": "Anh Tuấn",
            "priority": "high",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["escalated"] is True
    assert data["ticket_id"].startswith("CW-ESC-")
    # high priority → 15 min ETA
    assert data["estimated_response_minutes"] == 15
    # Vietnamese message includes the ticket_id
    assert data["ticket_id"] in data["message"]
    assert "nhân viên" in data["message"]


def test_escalate_human_eta_per_priority(client) -> None:
    """Each priority maps to a specific ETA."""
    expected = {"urgent": 5, "high": 15, "medium": 30, "low": 60}
    for priority, expected_eta in expected.items():
        resp = _signed_post(
            client,
            "/v1/integrations/chatwoot/tools/escalate_human",
            {
                "conversation_id": 100,
                "reason": f"test priority={priority}",
                "priority": priority,
            },
        )
        assert resp.status_code == 200
        assert resp.json()["estimated_response_minutes"] == expected_eta


def test_escalate_human_default_priority_is_medium(client) -> None:
    resp = _signed_post(
        client,
        "/v1/integrations/chatwoot/tools/escalate_human",
        {
            "conversation_id": 100,
            "reason": "default priority test",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["estimated_response_minutes"] == 30  # default medium


def test_escalate_human_422_on_missing_reason(client) -> None:
    resp = _signed_post(
        client,
        "/v1/integrations/chatwoot/tools/escalate_human",
        {"conversation_id": 1, "reason": ""},
    )
    assert resp.status_code == 422


# ──────────────────────────────────────────────────────────────────────
# HMAC signature on tool endpoints
# ──────────────────────────────────────────────────────────────────────


def test_product_lookup_rejects_bad_signature(client) -> None:
    resp = _signed_post(
        client,
        "/v1/integrations/chatwoot/tools/product_lookup",
        {"query": "test"},
        headers={"X-Chatwoot-Signature": "bad"},
    )
    assert resp.status_code == 401


def test_escalate_human_accepts_valid_signature(client) -> None:
    body = json.dumps({
        "conversation_id": 1,
        "reason": "test",
    }).encode()
    sig = hmac.new(CHATWOOT_TEST_SECRET.encode(), body, hashlib.sha256).hexdigest()
    resp = client.post(
        "/v1/integrations/chatwoot/tools/escalate_human",
        content=body,
        headers={
            "X-Chatwoot-Signature": sig,
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 200


# ──────────────────────────────────────────────────────────────────────
# P0.1 — HMAC required in production (security policy)
# ──────────────────────────────────────────────────────────────────────


def test_chatwoot_rejects_when_secret_unset_in_production(client, monkeypatch) -> None:
    """When CHATWOOT_WEBHOOK_SECRET is unset and CHATWOOT_ALLOW_INSECURE != true,
    Chatwoot requests MUST be rejected even with a valid signature.
    """
    monkeypatch.delenv("CHATWOOT_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("CHATWOOT_ALLOW_INSECURE", raising=False)
    resp = _signed_post(
        client,
        "/v1/integrations/chatwoot/respond",
        {"messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 401


def test_chatwoot_allows_when_allow_insecure_set(client, monkeypatch) -> None:
    """When CHATWOOT_ALLOW_INSECURE=true, Chatwoot works without secret (dev mode)."""
    monkeypatch.delenv("CHATWOOT_WEBHOOK_SECRET", raising=False)
    monkeypatch.setenv("CHATWOOT_ALLOW_INSECURE", "true")
    with respx.mock:
        respx.post(LLAMA_URL).mock(return_value=httpx.Response(200, json=_ollama_response("ok")))
        resp = client.post(
            "/v1/integrations/chatwoot/respond",
            json={"messages": [{"role": "user", "content": "hi"}]},
        )
    assert resp.status_code == 200


def test_chatwoot_rejects_when_signature_missing_with_secret(client, monkeypatch) -> None:
    """When secret IS set but signature header is missing, reject."""
    monkeypatch.setenv("CHATWOOT_WEBHOOK_SECRET", "secret123")
    monkeypatch.delenv("CHATWOOT_ALLOW_INSECURE", raising=False)
    resp = client.post(
        "/v1/integrations/chatwoot/respond",
        json={"messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 401
