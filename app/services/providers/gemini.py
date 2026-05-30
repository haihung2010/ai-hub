"""Google Gemini provider via AI Studio API."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.core.errors import UpstreamError, UpstreamTimeout
from app.models.chat import Message

logger = logging.getLogger(__name__)

_GEMINI_API = "https://generativelanguage.googleapis.com/v1beta"


class GeminiProvider:
    name = "gemini"

    def __init__(
        self,
        client: httpx.AsyncClient,
        api_key: str,
        base_url: str = _GEMINI_API,
    ) -> None:
        self._client = client
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key

    async def complete(
        self,
        messages: list[Message],
        model: str,
        temperature: float,
        options: dict | None = None,
    ) -> str:
        if not self._api_key:
            raise UpstreamError("gemini api key is not configured")

        # Convert messages to Gemini format
        contents = self._convert_messages(messages)

        payload = {
            "contents": contents,
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": options.get("max_tokens", 2048) if options else 2048,
            },
        }

        try:
            resp = await self._client.post(
                f"{self._base_url}/models/{model}:generateContent?key={self._api_key}",
                json=payload,
                timeout=60.0,
            )
        except httpx.ReadTimeout as exc:
            raise UpstreamTimeout(f"gemini read timeout: {exc}") from exc
        except httpx.HTTPError as exc:
            raise UpstreamError(f"gemini transport error: {exc}") from exc

        if resp.status_code >= 400:
            try:
                err = resp.json()
                msg = err.get("error", {}).get("message", str(err))[:200]
            except Exception:
                msg = resp.text[:200]
            raise UpstreamError(f"gemini {resp.status_code}: {msg}")

        data = resp.json()
        candidates = data.get("candidates") or []
        if not candidates:
            raise UpstreamError("gemini returned no candidates")

        parts = candidates[0].get("content", {}).get("parts", [])
        if not parts:
            raise UpstreamError("gemini returned empty content")

        return parts[0].get("text", "")

    async def stream_complete(
        self,
        messages: list[Message],
        model: str,
        temperature: float,
        options: dict | None = None,
    ) -> AsyncIterator[str]:
        if not self._api_key:
            raise UpstreamError("gemini api key is not configured")

        contents = self._convert_messages(messages)

        payload = {
            "contents": contents,
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": options.get("max_tokens", 2048) if options else 2048,
            },
        }

        try:
            async with self._client.stream(
                "POST",
                f"{self._base_url}/models/{model}:streamGenerateContent?key={self._api_key}&alt=sse",
                json=payload,
                timeout=60.0,
            ) as resp:
                if resp.status_code >= 400:
                    await resp.aread()
                    raise UpstreamError(f"gemini stream {resp.status_code}")
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data_str = line[5:].strip()
                    if not data_str or data_str == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_str)
                        candidates = chunk.get("candidates") or []
                        if candidates:
                            parts = candidates[0].get("content", {}).get("parts", [])
                            for part in parts:
                                if "text" in part:
                                    yield part["text"]
                    except json.JSONDecodeError:
                        continue
        except httpx.ReadTimeout as exc:
            raise UpstreamTimeout(f"gemini stream timeout: {exc}") from exc
        except httpx.HTTPError as exc:
            raise UpstreamError(f"gemini stream transport error: {exc}") from exc

    @staticmethod
    def _convert_messages(messages: list[Message]) -> list[dict[str, Any]]:
        """Convert Message list to Gemini contents format."""
        contents = []
        for msg in messages:
            role = "user" if msg.role in ("user", "system") else "model"
            content = msg.content if isinstance(msg.content, str) else ""
            # Handle images if present
            images = getattr(msg, "images", None) or []
            parts = [{"text": content}] if content else []
            for img_b64 in images:
                url = img_b64 if img_b64.startswith("data:") else f"data:image/jpeg;base64,{img_b64}"
                parts.append({"inlineData": {"mimeType": "image/jpeg", "data": img_b64}})
            contents.append({"role": role, "parts": parts})
        return contents