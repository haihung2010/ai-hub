"""OpenRouter provider using the OpenAI-compatible chat completions API."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.core.errors import UpstreamError, UpstreamTimeout
from app.models.chat import Message

logger = logging.getLogger(__name__)


class OpenRouterProvider:
    name = "openrouter"

    @staticmethod
    def _safe_error_message(resp: httpx.Response) -> str:
        try:
            payload: dict[str, Any] = resp.json()
        except ValueError:
            return resp.text[:200]
        error = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(error, dict):
            message = str(error.get("message") or "provider returned error")
            code = error.get("code")
            return f"code={code} message={message[:220]}"
        return str(payload)[:200]

    def __init__(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        api_key: str,
        fallback_models: list[str] | None = None,
        app_title: str = "AI Hub",
        site_url: str = "https://api-aiserver.htechlabsvn.com",
    ) -> None:
        self._client = client
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._fallback_models = fallback_models or []
        self._app_title = app_title
        self._site_url = site_url

    async def complete(
        self,
        messages: list[Message],
        model: str,
        temperature: float,
        options: dict | None = None,
    ) -> str:
        if not self._api_key:
            raise UpstreamError("openrouter api key is not configured")

        payload = {
            "model": model,
            "messages": [m.model_dump(exclude_none=True) for m in messages],
            "temperature": temperature,
            "stream": False,
        }
        if self._fallback_models:
            payload["models"] = [model, *self._fallback_models]
        if options:
            # OpenRouter/OpenAI-compatible providers generally do not accept Ollama's
            # num_ctx. Only pass standard/common options explicitly added by callers.
            for key in ("max_tokens", "top_p"):
                if key in options:
                    payload[key] = options[key]

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": self._site_url,
            "X-Title": self._app_title,
        }

        try:
            resp = await self._client.post(
                f"{self._base_url}/chat/completions",
                json=payload,
                headers=headers,
            )
        except httpx.ReadTimeout as exc:
            raise UpstreamTimeout(f"openrouter read timeout: {exc}") from exc
        except httpx.HTTPError as exc:
            raise UpstreamError(f"openrouter transport error: {exc}") from exc

        if resp.status_code >= 400:
            # Never include request headers/API key or upstream account metadata.
            raise UpstreamError(
                f"openrouter {resp.status_code}: {self._safe_error_message(resp)}"
            )

        data = resp.json()
        choices = data.get("choices") or []
        if not choices:
            raise UpstreamError("openrouter returned no choices")
        message = choices[0].get("message") or {}
        content = message.get("content")
        if not content:
            raise UpstreamError("openrouter returned empty content")
        return content

    async def stream_complete(
        self,
        messages: list[Message],
        model: str,
        temperature: float,
        options: dict | None = None,
    ) -> AsyncIterator[str]:
        if not self._api_key:
            raise UpstreamError("openrouter api key is not configured")

        payload: dict[str, Any] = {
            "model": model,
            "messages": [m.model_dump(exclude_none=True) for m in messages],
            "temperature": temperature,
            "stream": True,
        }
        if self._fallback_models:
            payload["models"] = [model, *self._fallback_models]
        if options:
            for key in ("max_tokens", "top_p"):
                if key in options:
                    payload[key] = options[key]

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": self._site_url,
            "X-Title": self._app_title,
        }

        try:
            async with self._client.stream(
                "POST",
                f"{self._base_url}/chat/completions",
                json=payload,
                headers=headers,
            ) as resp:
                if resp.status_code >= 400:
                    await resp.aread()
                    raise UpstreamError(
                        f"openrouter {resp.status_code}: {self._safe_error_message(resp)}"
                    )
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data = line[6:]
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                        content = (chunk["choices"][0]["delta"] or {}).get("content") or ""
                        if content:
                            yield content
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue
        except httpx.ReadTimeout as exc:
            raise UpstreamTimeout(f"openrouter stream timeout: {exc}") from exc
        except httpx.HTTPError as exc:
            raise UpstreamError(f"openrouter stream transport error: {exc}") from exc
