"""Unit tests for OpenRouterProvider.stream_complete()."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from app.core.errors import UpstreamError, UpstreamTimeout
from app.models.chat import Message
from app.services.providers.openrouter import OpenRouterProvider

BASE = "http://openrouter.test"
CHAT_URL = f"{BASE}/chat/completions"


def _sse(*chunks: str, done: bool = True) -> str:
    lines = [f"data: {json.dumps({'choices': [{'delta': {'content': c}, 'finish_reason': None}]})}\n\n" for c in chunks]
    if done:
        lines.append("data: [DONE]\n\n")
    return "".join(lines)


async def _collect(provider: OpenRouterProvider, messages: list[Message]) -> list[str]:
    chunks = []
    async for chunk in provider.stream_complete(messages, model="openai/gpt-4o", temperature=0.7):
        chunks.append(chunk)
    return chunks


@pytest.fixture
def provider():
    client = httpx.AsyncClient()
    return OpenRouterProvider(client=client, base_url=BASE, api_key="sk-test")


@pytest.fixture
def provider_no_key():
    client = httpx.AsyncClient()
    return OpenRouterProvider(client=client, base_url=BASE, api_key=None)


@pytest.mark.asyncio
async def test_stream_yields_chunks(provider: OpenRouterProvider) -> None:
    with respx.mock:
        respx.post(CHAT_URL).mock(return_value=httpx.Response(200, text=_sse("Hello", " world")))
        result = await _collect(provider, [Message(role="user", content="hi")])
    assert result == ["Hello", " world"]


@pytest.mark.asyncio
async def test_stream_raises_upstream_error_without_key(provider_no_key: OpenRouterProvider) -> None:
    with pytest.raises(UpstreamError, match="api key"):
        await _collect(provider_no_key, [Message(role="user", content="hi")])


@pytest.mark.asyncio
async def test_stream_raises_upstream_error_on_4xx(provider: OpenRouterProvider) -> None:
    with respx.mock:
        respx.post(CHAT_URL).mock(return_value=httpx.Response(401, text="unauthorized"))
        with pytest.raises(UpstreamError, match="401"):
            await _collect(provider, [Message(role="user", content="hi")])


@pytest.mark.asyncio
async def test_stream_sanitizes_json_error(provider: OpenRouterProvider) -> None:
    with respx.mock:
        respx.post(CHAT_URL).mock(
            return_value=httpx.Response(
                429,
                json={
                    "error": {
                        "code": "rate_limited",
                        "message": "quota exceeded",
                        "metadata": {"account_id": "acct-secret"},
                    }
                },
            )
        )
        with pytest.raises(UpstreamError) as exc:
            await _collect(provider, [Message(role="user", content="hi")])

    detail = str(exc.value)
    assert "rate_limited" in detail
    assert "quota exceeded" in detail
    assert "acct-secret" not in detail


@pytest.mark.asyncio
async def test_stream_raises_upstream_timeout(provider: OpenRouterProvider) -> None:
    with respx.mock:
        respx.post(CHAT_URL).mock(side_effect=httpx.ReadTimeout("timed out"))
        with pytest.raises(UpstreamTimeout):
            await _collect(provider, [Message(role="user", content="hi")])


@pytest.mark.asyncio
async def test_stream_skips_malformed_json(provider: OpenRouterProvider) -> None:
    body = (
        "data: bad\n\n"
        'data: {"choices": [{"delta": {"content": "yes"}, "finish_reason": null}]}\n\n'
        "data: [DONE]\n\n"
    )
    with respx.mock:
        respx.post(CHAT_URL).mock(return_value=httpx.Response(200, text=body))
        result = await _collect(provider, [Message(role="user", content="hi")])
    assert result == ["yes"]


@pytest.mark.asyncio
async def test_stream_sends_auth_header(provider: OpenRouterProvider) -> None:
    with respx.mock:
        route = respx.post(CHAT_URL).mock(return_value=httpx.Response(200, text=_sse("hi")))
        await _collect(provider, [Message(role="user", content="hi")])
    assert route.calls[0].request.headers["Authorization"] == "Bearer sk-test"


@pytest.mark.asyncio
async def test_stream_skips_empty_content(provider: OpenRouterProvider) -> None:
    body = (
        'data: {"choices": [{"delta": null, "finish_reason": null}]}\n\n'
        'data: {"choices": [{"delta": {"content": "a"}, "finish_reason": null}]}\n\n'
        "data: [DONE]\n\n"
    )
    with respx.mock:
        respx.post(CHAT_URL).mock(return_value=httpx.Response(200, text=body))
        result = await _collect(provider, [Message(role="user", content="hi")])
    assert result == ["a"]


@pytest.mark.asyncio
async def test_stream_raises_upstream_error_on_http_error(provider: OpenRouterProvider) -> None:
    with respx.mock:
        respx.post(CHAT_URL).mock(side_effect=httpx.HTTPError("connection reset"))
        with pytest.raises(UpstreamError):
            await _collect(provider, [Message(role="user", content="hi")])
