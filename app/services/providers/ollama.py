"""Ollama provider: uses the OpenAI-compatible /v1/chat/completions endpoint
for completions, and the native /api/tags endpoint to list installed models.
"""

from __future__ import annotations

import logging

import httpx

from app.core.errors import (
    OllamaUnavailable,
    UpstreamError,
    UpstreamTimeout,
    VramExhausted,
)
from app.models.chat import Message

logger = logging.getLogger(__name__)


class OllamaProvider:
    name = "ollama"

    def __init__(
        self,
        client: httpx.AsyncClient,
        openai_url: str,
        native_url: str,
    ) -> None:
        self._client = client
        self._openai_url = openai_url.rstrip("/")
        self._native_url = native_url.rstrip("/")

    async def complete(
        self,
        messages: list[Message],
        model: str,
        temperature: float,
        options: dict | None = None,
    ) -> str:
        payload = {
            "model": model,
            "messages": [m.model_dump(exclude_none=True) for m in messages],
            "temperature": temperature,
            "stream": False,
        }
        if options:
            payload.update(options)

        try:
            resp = await self._client.post(
                f"{self._openai_url}/chat/completions", json=payload
            )
        except httpx.ConnectError as exc:
            logger.warning("Ollama connect error: %s", exc)
            raise OllamaUnavailable(str(exc)) from exc
        except httpx.ReadTimeout as exc:
            raise UpstreamTimeout(f"ollama read timeout: {exc}") from exc
        except httpx.HTTPError as exc:
            raise UpstreamError(f"ollama transport error: {exc}") from exc

        if resp.status_code >= 400:
            body = resp.text.lower()
            if resp.status_code == 500 and ("out of memory" in body or "vram" in body):
                raise VramExhausted(resp.text)
            raise UpstreamError(f"ollama {resp.status_code}: {resp.text[:200]}")

        data = resp.json()
        choices = data.get("choices") or []
        if not choices:
            raise UpstreamError("ollama returned no choices")
        return choices[0]["message"]["content"]

    async def list_models(self) -> list[str]:
        try:
            resp = await self._client.get(f"{self._native_url}/api/tags")
        except httpx.ConnectError as exc:
            raise OllamaUnavailable(str(exc)) from exc
        except httpx.HTTPError as exc:
            raise UpstreamError(str(exc)) from exc

        if resp.status_code >= 400:
            raise UpstreamError(f"ollama tags {resp.status_code}")

        data = resp.json()
        return [m["name"] for m in data.get("models", []) if "name" in m]
