"""OpenRouter provider tests."""

from __future__ import annotations

import json

import httpx
import pytest

from app.models.chat import Message
from app.services.providers.openrouter import OpenRouterProvider


@pytest.mark.unit
@pytest.mark.asyncio
async def test_openrouter_provider_posts_openai_compatible_payload() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers.get("authorization")
        seen["referer"] = request.headers.get("http-referer")
        seen["title"] = request.headers.get("x-title")
        seen["path"] = request.url.path
        seen["payload"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "hello from cloud"}}]},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenRouterProvider(
            client=client,
            base_url="https://openrouter.test/api/v1",
            api_key="test-key",
            fallback_models=["openrouter/auto"],
        )
        content = await provider.complete(
            [Message(role="user", content="hello")],
            "free-model",
            0.2,
            {"num_ctx": 8192, "max_tokens": 32},
        )

    assert content == "hello from cloud"
    assert seen["path"] == "/api/v1/chat/completions"
    assert seen["authorization"] == "Bearer test-key"
    assert seen["referer"] == "https://api-aiserver.htechlabsvn.com"
    assert seen["title"] == "AI Hub"
    assert seen["payload"]["model"] == "free-model"
    assert seen["payload"]["models"] == ["free-model", "openrouter/auto"]
    assert seen["payload"]["messages"] == [{"role": "user", "content": "hello"}]
    assert seen["payload"]["max_tokens"] == 32
    assert "num_ctx" not in seen["payload"]
