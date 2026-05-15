"""Tests for LlamaCppLoadBalancer."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import respx

from app.services.providers.llama_cpp import LlamaCppProvider
from app.services.providers.load_balancer import LlamaCppLoadBalancer, _free_slots


def _make_real_provider(url: str = "http://llama.test/v1") -> LlamaCppProvider:
    """Create a real LlamaCppProvider with mock httpx client."""
    client = MagicMock(spec=httpx.AsyncClient)
    return LlamaCppProvider(client=client, openai_url=url)


class TestFreeSlots:
    @respx.mock
    @pytest.mark.asyncio
    async def test_returns_free_count(self):
        respx.get("http://llama.test/slots").respond(json=[
            {"id": 0, "is_processing": False},
            {"id": 1, "is_processing": True},
            {"id": 2, "is_processing": False},
        ])
        async with httpx.AsyncClient() as client:
            result = await _free_slots(client, "http://llama.test/slots")
        assert result == 2

    @respx.mock
    @pytest.mark.asyncio
    async def test_returns_minus_one_on_error(self):
        respx.get("http://llama.test/slots").respond(status_code=500)
        async with httpx.AsyncClient() as client:
            result = await _free_slots(client, "http://llama.test/slots")
        assert result == -1

    @respx.mock
    @pytest.mark.asyncio
    async def test_returns_minus_one_on_timeout(self):
        import httpx as hx
        respx.get("http://llama.test/slots").side_effect = hx.TimeoutException("timeout")
        async with httpx.AsyncClient() as client:
            result = await _free_slots(client, "http://llama.test/slots")
        assert result == -1


class TestLoadBalancerInit:
    def test_name_is_llama_cpp(self):
        p1 = _make_real_provider()
        lb = LlamaCppLoadBalancer(
            client=MagicMock(spec=httpx.AsyncClient),
            providers=[p1],
            slots_urls=["http://n1/slots"],
        )
        assert lb.name == "llama_cpp"

    def test_mismatched_lengths_raises(self):
        p1 = _make_real_provider()
        with pytest.raises(ValueError):
            LlamaCppLoadBalancer(
                client=MagicMock(spec=httpx.AsyncClient),
                providers=[p1],
                slots_urls=["http://n1/slots", "http://n2/slots"],
            )


class TestLoadBalancerPick:
    @respx.mock
    @pytest.mark.asyncio
    async def test_picks_provider_with_most_free_slots(self):
        respx.get("http://n1/slots").respond(json=[
            {"id": 0, "is_processing": True},
        ])
        respx.get("http://n2/slots").respond(json=[
            {"id": 0, "is_processing": False},
            {"id": 1, "is_processing": False},
        ])
        p1 = _make_real_provider("http://n1/v1")
        p2 = _make_real_provider("http://n2/v1")
        async with httpx.AsyncClient() as client:
            lb = LlamaCppLoadBalancer(
                client=client,
                providers=[p1, p2],
                slots_urls=["http://n1/slots", "http://n2/slots"],
            )
            picked = await lb._pick()
        assert picked is p2

    @respx.mock
    @pytest.mark.asyncio
    async def test_falls_back_to_first_when_all_unreachable(self):
        respx.get("http://n1/slots").respond(status_code=500)
        respx.get("http://n2/slots").respond(status_code=500)
        p1 = _make_real_provider("http://n1/v1")
        p2 = _make_real_provider("http://n2/v1")
        async with httpx.AsyncClient() as client:
            lb = LlamaCppLoadBalancer(
                client=client,
                providers=[p1, p2],
                slots_urls=["http://n1/slots", "http://n2/slots"],
            )
            picked = await lb._pick()
        assert picked is p1


class TestLoadBalancerComplete:
    @respx.mock
    @pytest.mark.asyncio
    async def test_complete_delegates_to_picked_provider(self):
        respx.get("http://n1/slots").respond(json=[{"id": 0, "is_processing": False}])
        p1 = _make_real_provider("http://n1/v1")
        p1.complete = AsyncMock(return_value="hello from p1")
        async with httpx.AsyncClient() as client:
            lb = LlamaCppLoadBalancer(
                client=client, providers=[p1], slots_urls=["http://n1/slots"],
            )
            result = await lb.complete(
                [MagicMock(role="user", content="hi")], "test-model", 0.5
            )
        assert result == "hello from p1"


class TestLoadBalancerStreamComplete:
    @respx.mock
    @pytest.mark.asyncio
    async def test_stream_delegates_to_picked_provider(self):
        respx.get("http://n1/slots").respond(json=[{"id": 0, "is_processing": False}])

        async def fake_stream(*args, **kwargs):
            yield "chunk1"
            yield "chunk2"

        p1 = _make_real_provider("http://n1/v1")
        p1.stream_complete = fake_stream
        async with httpx.AsyncClient() as client:
            lb = LlamaCppLoadBalancer(
                client=client, providers=[p1], slots_urls=["http://n1/slots"],
            )
            chunks = []
            async for chunk in lb.stream_complete(
                [MagicMock(role="user", content="hi")], "test-model", 0.5
            ):
                chunks.append(chunk)
        assert chunks == ["chunk1", "chunk2"]


class TestLoadBalancerListModels:
    @respx.mock
    @pytest.mark.asyncio
    async def test_aggregates_models_from_all_providers(self):
        p1 = _make_real_provider("http://n1/v1")
        p1.list_models = AsyncMock(return_value=[{"id": "model-a"}])
        p2 = _make_real_provider("http://n2/v1")
        p2.list_models = AsyncMock(return_value=[{"id": "model-b"}])
        lb = LlamaCppLoadBalancer(
            client=MagicMock(spec=httpx.AsyncClient),
            providers=[p1, p2],
            slots_urls=["http://n1/slots", "http://n2/slots"],
        )
        models = await lb.list_models()
        ids = [m["id"] for m in models]
        assert "model-a" in ids
        assert "model-b" in ids

    @respx.mock
    @pytest.mark.asyncio
    async def test_skips_failed_provider(self):
        p1 = _make_real_provider("http://n1/v1")
        p1.list_models = AsyncMock(side_effect=Exception("down"))
        p2 = _make_real_provider("http://n2/v1")
        p2.list_models = AsyncMock(return_value=[{"id": "model-ok"}])
        lb = LlamaCppLoadBalancer(
            client=MagicMock(spec=httpx.AsyncClient),
            providers=[p1, p2],
            slots_urls=["http://n1/slots", "http://n2/slots"],
        )
        models = await lb.list_models()
        ids = [m["id"] for m in models]
        assert "model-ok" in ids
