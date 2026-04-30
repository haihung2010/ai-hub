"""llama.cpp provider tests."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from app.core.errors import OllamaUnavailable, UpstreamError, UpstreamTimeout, VramExhausted
from app.models.chat import Message
from app.services.providers.llama_cpp import LlamaCppProvider

BASE = "http://llama.test"
CHAT_URL = f"{BASE}/v1/chat/completions"
MODELS_URL = f"{BASE}/v1/models"


def _sse(*chunks: str, done: bool = True) -> str:
    lines = [f"data: {json.dumps({'choices': [{'delta': {'content': c}, 'finish_reason': None}]})}\n\n" for c in chunks]
    if done:
        lines.append("data: [DONE]\n\n")
    return "".join(lines)


async def _collect(provider: LlamaCppProvider, messages: list[Message]) -> list[str]:
    chunks = []
    async for chunk in provider.stream_complete(messages, model="local-model", temperature=0.7):
        chunks.append(chunk)
    return chunks


@pytest.fixture
def provider() -> LlamaCppProvider:
    client = httpx.AsyncClient()
    return LlamaCppProvider(client=client, openai_url=f"{BASE}/v1")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_llama_cpp_posts_openai_compatible_payload() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["payload"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, json={"choices": [{"message": {"content": "hello local"}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = LlamaCppProvider(client=client, openai_url=f"{BASE}/v1")
        content = await provider.complete(
            [Message(role="user", content="hello")],
            "local-model",
            0.2,
            {"num_ctx": 8192, "max_tokens": 32, "top_p": 0.9},
        )

    assert content == "hello local"
    assert seen["path"] == "/v1/chat/completions"
    assert seen["payload"]["model"] == "local-model"
    assert seen["payload"]["messages"] == [{"role": "user", "content": "hello"}]
    assert seen["payload"]["max_tokens"] == 32
    assert seen["payload"]["top_p"] == 0.9
    assert seen["payload"]["stop"] == [
        "<|channel>",
        "<|channel|>",
        "<channel|>",
        "&lt;|channel&gt;",
        "&lt;channel|&gt;",
    ]
    assert "num_ctx" not in seen["payload"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_llama_cpp_stream_yields_chunks(provider: LlamaCppProvider) -> None:
    with respx.mock:
        respx.post(CHAT_URL).mock(return_value=httpx.Response(200, text=_sse("Hello", " world")))
        result = await _collect(provider, [Message(role="user", content="hi")])
    assert result == ["Hello", " world"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_llama_cpp_stream_skips_malformed_json_lines(provider: LlamaCppProvider) -> None:
    body = (
        "data: not-valid-json\n\n"
        'data: {"choices": [{"delta": {"content": "ok"}, "finish_reason": null}]}\n\n'
        "data: [DONE]\n\n"
    )
    with respx.mock:
        respx.post(CHAT_URL).mock(return_value=httpx.Response(200, text=body))
        result = await _collect(provider, [Message(role="user", content="hi")])
    assert result == ["ok"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_llama_cpp_raises_unavailable_on_connect_error(provider: LlamaCppProvider) -> None:
    with respx.mock:
        respx.post(CHAT_URL).mock(side_effect=httpx.ConnectError("refused"))
        with pytest.raises(OllamaUnavailable):
            await provider.complete([Message(role="user", content="hi")], "local-model", 0.7)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_llama_cpp_raises_upstream_timeout(provider: LlamaCppProvider) -> None:
    with respx.mock:
        respx.post(CHAT_URL).mock(side_effect=httpx.ReadTimeout("timed out"))
        with pytest.raises(UpstreamTimeout):
            await provider.complete([Message(role="user", content="hi")], "local-model", 0.7)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_llama_cpp_raises_vram_exhausted_on_oom(provider: LlamaCppProvider) -> None:
    with respx.mock:
        respx.post(CHAT_URL).mock(return_value=httpx.Response(500, text="CUDA error: out of memory"))
        with pytest.raises(VramExhausted):
            await provider.complete([Message(role="user", content="hi")], "local-model", 0.7)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_llama_cpp_raises_upstream_error_on_http_error(provider: LlamaCppProvider) -> None:
    with respx.mock:
        respx.post(CHAT_URL).mock(return_value=httpx.Response(404, text="not found"))
        with pytest.raises(UpstreamError):
            await provider.complete([Message(role="user", content="hi")], "local-model", 0.7)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_llama_cpp_sanitizes_channel_artifacts_in_messages() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["payload"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = LlamaCppProvider(client=client, openai_url=f"{BASE}/v1")
        await provider.complete([Message(role="user", content="&lt;|channel&gt;bad")], "local-model", 0.7)

    assert seen["payload"]["messages"] == [{"role": "user", "content": ""}]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_llama_cpp_lists_models(provider: LlamaCppProvider) -> None:
    with respx.mock:
        respx.get(MODELS_URL).mock(return_value=httpx.Response(200, json={"data": [{"id": "local-model"}]}))
        result = await provider.list_models()
    assert result == ["local-model"]
