"""9Router provider — OpenAI-compatible proxy with auto-fallback to 40+ providers."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.core.errors import UpstreamError, UpstreamTimeout
from app.models.chat import Message

logger = logging.getLogger(__name__)


class NineRouterProvider:
    name = "ninerouter"

    def __init__(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        api_key: str,
        model: str = "kr/claude-sonnet-4.5",
    ) -> None:
        self._client = client
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model

    async def complete(
        self,
        messages: list[Message],
        model: str,
        temperature: float,
        options: dict | None = None,
    ) -> str:
        if not self._api_key:
            raise UpstreamError("9router api key not configured")

        use_model = model or self._model
        payload: dict[str, Any] = {
            "model": use_model,
            "messages": [m.model_dump(exclude_none=True) for m in messages],
            "temperature": temperature,
            "stream": False,
        }
        if options:
            for key in ("max_tokens", "top_p"):
                if key in options:
                    payload[key] = options[key]

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        try:
            resp = await self._client.post(
                f"{self._base_url}/v1/chat/completions",
                json=payload,
                headers=headers,
            )
        except httpx.ReadTimeout as exc:
            raise UpstreamTimeout(f"9router read timeout: {exc}") from exc
        except httpx.HTTPError as exc:
            raise UpstreamError(f"9router transport error: {exc}") from exc

        if resp.status_code >= 400:
            try:
                err_data = resp.json()
                detail = err_data.get("error", {}).get("message", str(err_data))[:200]
            except Exception:
                detail = resp.text[:200]
            raise UpstreamError(f"9router {resp.status_code}: {detail}")

        data = resp.json()
        choices = data.get("choices") or []
        if not choices:
            raise UpstreamError("9router returned no choices")
        message = choices[0].get("message", {})
        content = message.get("content") or message.get("reasoning") or message.get("text")
        if not content:
            raise UpstreamError("9router returned empty content")
        return content

    async def stream_complete(
        self,
        messages: list[Message],
        model: str,
        temperature: float,
        options: dict | None = None,
    ) -> AsyncIterator[str]:
        if not self._api_key:
            raise UpstreamError("9router api key not configured")

        use_model = model or self._model
        payload: dict[str, Any] = {
            "model": use_model,
            "messages": [m.model_dump(exclude_none=True) for m in messages],
            "temperature": temperature,
            "stream": True,
        }
        if options:
            for key in ("max_tokens", "top_p"):
                if key in options:
                    payload[key] = options[key]

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        try:
            async with self._client.stream(
                "POST",
                f"{self._base_url}/v1/chat/completions",
                json=payload,
                headers=headers,
            ) as resp:
                if resp.status_code >= 400:
                    await resp.aread()
                    try:
                        err_data = resp.json()
                        detail = err_data.get("error", {}).get("message", str(err_data))[:200]
                    except Exception:
                        detail = resp.text[:200]
                    raise UpstreamError(f"9router {resp.status_code}: {detail}")
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data = line[6:]
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                        delta = chunk["choices"][0].get("delta") or {}
                        content = delta.get("content") or delta.get("reasoning") or delta.get("text") or ""
                        if content:
                            yield content
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue
        except httpx.ReadTimeout as exc:
            raise UpstreamTimeout(f"9router stream timeout: {exc}") from exc
        except httpx.HTTPError as exc:
            raise UpstreamError(f"9router stream transport error: {exc}") from exc