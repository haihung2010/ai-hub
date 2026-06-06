"""Unit tests for MiniMax M3 provider.

All tests mock httpx so no real API call is made. Live integration is
covered by tests/integration/test_minimax_provider_live.py.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.core.errors import UpstreamError, UpstreamTimeout
from app.models.chat import Message
from app.services.providers.minimax import MiniMaxProvider


def _msg(role: str, content: str) -> Message:
    return Message(role=role, content=content)


def _captured_body(client: AsyncMock) -> dict[str, Any]:
    """Return the request body from the most recent .post() call.

    Tolerates both the mock case (dict passed as ``json=...``) and the
    real httpx case (dict already serialized to a JSON string).
    """
    payload = client.post.call_args.kwargs["json"]
    if isinstance(payload, (str, bytes, bytearray)):
        return json.loads(payload)
    return payload


def _mock_client(response: httpx.Response) -> AsyncMock:
    """Build an async httpx.AsyncClient whose .post() returns the given response."""
    client = AsyncMock(spec=httpx.AsyncClient)
    client.post = AsyncMock(return_value=response)
    return client


def _ok_response(body: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, json=body, request=httpx.Request("POST", "https://x"))


def _error_response(status: int, body: dict[str, Any] | None = None) -> httpx.Response:
    return httpx.Response(
        status, json=body or {"error": {"message": "boom"}}, request=httpx.Request("POST", "https://x")
    )


# ── Authentication & headers ────────────────────────────────────────────


class TestAuth:
    @pytest.mark.unit
    def test_name_is_minimax(self) -> None:
        client = _mock_client(_ok_response({"content": [{"text": "ok"}]}))
        provider = MiniMaxProvider(client=client, api_key="sk-x", model="MiniMax-M3")
        assert provider.name == "minimax"

    @pytest.mark.unit
    def test_missing_api_key_raises(self) -> None:
        client = _mock_client(_ok_response({"content": [{"text": "ok"}]}))
        provider = MiniMaxProvider(client=client, api_key="", model="MiniMax-M3")
        with pytest.raises(UpstreamError, match="api key is not configured"):
            import asyncio
            asyncio.run(provider.complete([_msg("user", "hi")], "MiniMax-M3", 0.3))

    @pytest.mark.unit
    async def test_sends_x_api_key_header(self) -> None:
        client = _mock_client(_ok_response({"content": [{"text": "ok"}]}))
        provider = MiniMaxProvider(
            client=client, api_key="sk-test-123", model="MiniMax-M3"
        )
        await provider.complete([_msg("user", "hi")], "MiniMax-M3", 0.3)
        call = client.post.call_args
        headers = call.kwargs.get("headers") or (call.args[1] if len(call.args) > 1 else {})
        assert headers.get("x-api-key") == "sk-test-123"
        assert headers.get("anthropic-version") == "2023-06-01"


# ── Request payload shape (Anthropic Messages API) ─────────────────────


class TestPayloadShape:
    @pytest.mark.unit
    async def test_uses_messages_endpoint(self) -> None:
        client = _mock_client(_ok_response({"content": [{"text": "ok"}]}))
        provider = MiniMaxProvider(
            client=client, api_key="sk-x", model="MiniMax-M3"
        )
        await provider.complete([_msg("user", "hi")], "MiniMax-M3", 0.3)
        url = client.post.call_args.args[0]
        assert url.endswith("/v1/messages")

    @pytest.mark.unit
    async def test_passes_model_and_max_tokens(self) -> None:
        client = _mock_client(_ok_response({"content": [{"text": "ok"}]}))
        provider = MiniMaxProvider(
            client=client, api_key="sk-x", model="MiniMax-M3"
        )
        await provider.complete(
            [_msg("user", "hi")], "MiniMax-M3", 0.3, options={"max_tokens": 256}
        )
        body = _captured_body(client)
        assert body["model"] == "MiniMax-M3"
        assert body["max_tokens"] == 256
        assert body["temperature"] == 0.3

    @pytest.mark.unit
    async def test_separates_system_from_messages(self) -> None:
        """Anthropic Messages API uses top-level 'system', not a system message."""
        client = _mock_client(_ok_response({"content": [{"text": "ok"}]}))
        provider = MiniMaxProvider(
            client=client, api_key="sk-x", model="MiniMax-M3", enable_caching=False
        )
        await provider.complete(
            [_msg("system", "You are a helpful bot."), _msg("user", "hi")],
            "MiniMax-M3",
            0.3,
        )
        body = _captured_body(client)
        assert body["system"] == "You are a helpful bot."
        # System message should NOT also appear in messages list
        roles = [m["role"] for m in body["messages"]]
        assert "system" not in roles
        assert roles == ["user"]


# ── Prompt caching (cache_control injection) ───────────────────────────


class TestPromptCaching:
    @pytest.mark.unit
    async def test_caching_disabled_sends_no_cache_control(self) -> None:
        client = _mock_client(_ok_response({"content": [{"text": "ok"}]}))
        provider = MiniMaxProvider(
            client=client, api_key="sk-x", model="MiniMax-M3", enable_caching=False
        )
        await provider.complete(
            [_msg("system", "sys"), _msg("user", "hi")], "MiniMax-M3", 0.3
        )
        body = _captured_body(client)
        assert "cache_control" not in json.dumps(body)

    @pytest.mark.unit
    async def test_caching_enabled_marks_system_block(self) -> None:
        client = _mock_client(_ok_response({"content": [{"text": "ok"}]}))
        provider = MiniMaxProvider(
            client=client, api_key="sk-x", model="MiniMax-M3", enable_caching=True
        )
        await provider.complete(
            [_msg("system", "You are a helpful bot."), _msg("user", "hi")],
            "MiniMax-M3",
            0.3,
        )
        body = _captured_body(client)
        # System becomes a list-of-blocks with cache_control on the first
        assert isinstance(body["system"], list)
        assert body["system"][0]["cache_control"] == {"type": "ephemeral"}
        assert body["system"][0]["text"] == "You are a helpful bot."

    @pytest.mark.unit
    async def test_caching_enabled_marks_last_message(self) -> None:
        client = _mock_client(_ok_response({"content": [{"text": "ok"}]}))
        provider = MiniMaxProvider(
            client=client, api_key="sk-x", model="MiniMax-M3", enable_caching=True
        )
        await provider.complete(
            [_msg("user", "first"), _msg("assistant", "ack"), _msg("user", "follow-up")],
            "MiniMax-M3",
            0.3,
        )
        body = _captured_body(client)
        messages = body["messages"]
        assert "cache_control" not in messages[0]
        assert "cache_control" not in messages[1]
        assert messages[-1]["cache_control"] == {"type": "ephemeral"}

    @pytest.mark.unit
    async def test_caching_with_no_system_still_works(self) -> None:
        """Edge case: no system message → only the last user msg is marked."""
        client = _mock_client(_ok_response({"content": [{"text": "ok"}]}))
        provider = MiniMaxProvider(
            client=client, api_key="sk-x", model="MiniMax-M3", enable_caching=True
        )
        await provider.complete([_msg("user", "hi")], "MiniMax-M3", 0.3)
        body = _captured_body(client)
        # 'system' field is absent or empty when no system message present
        assert body.get("system") in (None, "")
        # Last message marked
        assert body["messages"][-1]["cache_control"] == {"type": "ephemeral"}


# ── Response parsing ────────────────────────────────────────────────────


class TestResponseParsing:
    @pytest.mark.unit
    async def test_extracts_text_from_content_blocks(self) -> None:
        body = {"content": [{"type": "text", "text": "Hello!"}]}
        client = _mock_client(_ok_response(body))
        provider = MiniMaxProvider(client=client, api_key="sk-x", model="MiniMax-M3")
        result = await provider.complete([_msg("user", "hi")], "MiniMax-M3", 0.3)
        assert result == "Hello!"

    @pytest.mark.unit
    async def test_concatenates_multiple_text_blocks(self) -> None:
        body = {"content": [
            {"type": "text", "text": "First. "},
            {"type": "text", "text": "Second."},
        ]}
        client = _mock_client(_ok_response(body))
        provider = MiniMaxProvider(client=client, api_key="sk-x", model="MiniMax-M3")
        result = await provider.complete([_msg("user", "hi")], "MiniMax-M3", 0.3)
        assert result == "First. Second."

    @pytest.mark.unit
    async def test_ignores_thinking_blocks(self) -> None:
        """Anthropic-compatible APIs may return 'thinking' blocks; skip them."""
        body = {"content": [
            {"type": "thinking", "thinking": "internal"},
            {"type": "text", "text": "Visible answer."},
        ]}
        client = _mock_client(_ok_response(body))
        provider = MiniMaxProvider(client=client, api_key="sk-x", model="MiniMax-M3")
        result = await provider.complete([_msg("user", "hi")], "MiniMax-M3", 0.3)
        assert result == "Visible answer."

    @pytest.mark.unit
    async def test_4xx_raises_upstream_error(self) -> None:
        client = _mock_client(_error_response(401, {"error": {"message": "bad key"}}))
        provider = MiniMaxProvider(client=client, api_key="sk-x", model="MiniMax-M3")
        with pytest.raises(UpstreamError, match="401"):
            await provider.complete([_msg("user", "hi")], "MiniMax-M3", 0.3)

    @pytest.mark.unit
    async def test_5xx_raises_upstream_error(self) -> None:
        client = _mock_client(_error_response(503, {"error": {"message": "down"}}))
        provider = MiniMaxProvider(client=client, api_key="sk-x", model="MiniMax-M3")
        with pytest.raises(UpstreamError, match="503"):
            await provider.complete([_msg("user", "hi")], "MiniMax-M3", 0.3)

    @pytest.mark.unit
    async def test_read_timeout_raises_upstream_timeout(self) -> None:
        client = AsyncMock(spec=httpx.AsyncClient)
        client.post = AsyncMock(side_effect=httpx.ReadTimeout("timed out"))
        provider = MiniMaxProvider(client=client, api_key="sk-x", model="MiniMax-M3")
        with pytest.raises(UpstreamTimeout, match="read timeout"):
            await provider.complete([_msg("user", "hi")], "MiniMax-M3", 0.3)


# ── Streaming ──────────────────────────────────────────────────────────


class TestStreaming:
    @pytest.mark.unit
    async def test_stream_yields_text_deltas(self) -> None:
        # Build a minimal SSE response
        chunks = [
            'data: {"type":"content_block_start","content_block":{"type":"text","text":""}}\n\n',
            'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"Hel"}}\n\n',
            'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"lo"}}\n\n',
            'data: {"type":"message_stop"}\n\n',
        ]
        body = "".join(chunks).encode()
        resp = httpx.Response(
            200, content=body, headers={"content-type": "text/event-stream"},
            request=httpx.Request("POST", "https://x"),
        )
        # Use a context-manager mock for stream()
        client = AsyncMock(spec=httpx.AsyncClient)
        stream_cm = AsyncMock()
        stream_cm.__aenter__ = AsyncMock(return_value=resp)
        stream_cm.__aexit__ = AsyncMock(return_value=None)
        stream_cm.aiter_lines = resp.aiter_lines
        client.stream = MagicMock(return_value=stream_cm)
        provider = MiniMaxProvider(client=client, api_key="sk-x", model="MiniMax-M3")

        collected = []
        async for token in provider.stream_complete(
            [_msg("user", "hi")], "MiniMax-M3", 0.3
        ):
            collected.append(token)
        assert "".join(collected) == "Hello"
