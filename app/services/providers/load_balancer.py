"""Round-robin + least-loaded balancer across multiple LlamaCppProvider instances."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

import httpx

from app.core.errors import UpstreamError
from app.models.chat import Message
from app.services.providers.llama_cpp import LlamaCppProvider

logger = logging.getLogger(__name__)

_SLOTS_TIMEOUT = httpx.Timeout(1.0)


async def _free_slots(client: httpx.AsyncClient, slots_url: str) -> int:
    """Return number of non-processing slots, or -1 on error."""
    try:
        resp = await client.get(slots_url, timeout=_SLOTS_TIMEOUT)
        if resp.status_code != 200:
            return -1
        slots = resp.json()
        return sum(1 for s in slots if not s.get("is_processing", False))
    except Exception:
        return -1


class LlamaCppLoadBalancer:
    """Routes requests to the LlamaCppProvider with the most free slots."""

    name = "llama_cpp"

    def __init__(
        self,
        client: httpx.AsyncClient,
        providers: list[LlamaCppProvider],
        slots_urls: list[str],
    ) -> None:
        if len(providers) != len(slots_urls):
            raise ValueError("providers and slots_urls must have the same length")
        self._client = client
        self._providers = providers
        self._slots_urls = slots_urls

    async def _pick(self) -> LlamaCppProvider:
        counts = await asyncio.gather(*[_free_slots(self._client, url) for url in self._slots_urls])
        logger.debug("llama.cpp slot counts: %s", list(zip(self._slots_urls, counts)))
        best_idx = max(range(len(counts)), key=lambda i: counts[i])
        if counts[best_idx] < 0:
            logger.warning("all llama.cpp backends unreachable")
            raise UpstreamError("no healthy providers available")
        return self._providers[best_idx]

    async def complete(
        self,
        messages: list[Message],
        model: str,
        temperature: float,
        options: dict | None = None,
    ) -> str:
        provider = await self._pick()
        return await provider.complete(messages, model, temperature, options)

    async def stream_complete(
        self,
        messages: list[Message],
        model: str,
        temperature: float,
        options: dict | None = None,
    ) -> AsyncIterator[str]:
        provider = await self._pick()
        async for chunk in provider.stream_complete(messages, model, temperature, options):
            yield chunk

    async def list_models(self) -> list[str]:
        results: list[str] = []
        for provider in self._providers:
            try:
                results.extend(await provider.list_models())
            except Exception:
                pass
        return results