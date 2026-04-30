"""llama.cpp provider using the OpenAI-compatible chat completions API."""

from __future__ import annotations

import html
import json
import logging
import re
from collections.abc import AsyncIterator

import httpx

from app.core.errors import OllamaUnavailable, UpstreamError, UpstreamTimeout, VramExhausted
from app.models.chat import Message

logger = logging.getLogger(__name__)

_ALLOWED_OPTIONS = {"max_tokens", "top_p"}
_CHANNEL_ARTIFACT_RE = re.compile(r"(?:&lt;|<)(?:\\|)?channel(?:\\|&gt;|\\|>|\\||\||&gt;|>)?", re.IGNORECASE)
_CHANNEL_STOP_SEQUENCES = ["<|channel>", "<|channel|>", "<channel|>", "&lt;|channel&gt;", "&lt;channel|&gt;"]


class LlamaCppProvider:
    name = "llama_cpp"

    def __init__(self, client: httpx.AsyncClient, openai_url: str) -> None:
        self._client = client
        self._openai_url = openai_url.rstrip("/")

    @staticmethod
    def _sanitize_content(content: str, *, strip: bool = True) -> str:
        for _ in range(2):
            content = re.sub(r"(?is)^\s*&lt;\|channel(?:\|&gt;|&gt;)?[^\n]*", "", content)
            content = re.sub(r"(?is)^\s*&lt;channel\|&gt;[^\n]*", "", content)
            content = re.sub(r"&lt;\|[^\n&]*(?:\|&gt;|&gt;)?", "", content)
            content = re.sub(r"&lt;channel\|&gt;", "", content, flags=re.IGNORECASE)
            content = html.unescape(content)
            content = re.sub(r"(?is)^\s*<\|channel(?:\|>|>)?[^\n]*", "", content)
            content = re.sub(r"(?is)^\s*<channel\|>[^\n]*", "", content)
            content = re.sub(r"<\|[^\n>]*(?:\|>|>)?", "", content)
            content = re.sub(r"<channel\|>", "", content, flags=re.IGNORECASE)
        content = re.sub(r"(?m)^\s*text(?:acular)?[-\w{}.:\"')]*\s*", "", content, flags=re.IGNORECASE)
        return content.strip() if strip else content

    @classmethod
    def _sanitize_value(cls, value):
        if isinstance(value, str):
            return cls._sanitize_content(value)
        if isinstance(value, list):
            return [cls._sanitize_value(item) for item in value]
        if isinstance(value, dict):
            return {key: cls._sanitize_value(item) for key, item in value.items()}
        return value

    @classmethod
    def _serialize_message(cls, message: Message) -> dict:
        return cls._sanitize_value(message.model_dump(exclude_none=True))

    @classmethod
    def _payload(
        cls,
        messages: list[Message],
        model: str,
        temperature: float,
        stream: bool,
        options: dict | None,
    ) -> dict:
        payload = {
            "model": model,
            "messages": [cls._serialize_message(m) for m in messages],
            "temperature": temperature,
            "stream": stream,
            "stop": _CHANNEL_STOP_SEQUENCES,
        }
        if options:
            payload.update({key: value for key, value in options.items() if key in _ALLOWED_OPTIONS})
        return payload

    @classmethod
    def _channel_artifact_paths(cls, value, path: str = "$") -> list[tuple[str, str]]:
        if isinstance(value, str):
            if _CHANNEL_ARTIFACT_RE.search(value):
                return [(path, value[:200])]
            return []
        if isinstance(value, list):
            paths: list[tuple[str, str]] = []
            for index, item in enumerate(value):
                paths.extend(cls._channel_artifact_paths(item, f"{path}[{index}]"))
            return paths
        if isinstance(value, dict):
            paths = []
            for key, item in value.items():
                if key == "stop":
                    continue
                paths.extend(cls._channel_artifact_paths(item, f"{path}.{key}"))
            return paths
        return []

    @classmethod
    def _guard_payload(cls, payload: dict) -> dict:
        artifacts = cls._channel_artifact_paths(payload)
        if artifacts:
            for path, preview in artifacts[:5]:
                logger.error("llama.cpp payload contains channel artifact path=%s preview=%r", path, preview)
            raise UpstreamError("llama.cpp payload contains channel artifact after sanitization")
        return payload

    @staticmethod
    def _raise_for_status(resp: httpx.Response) -> None:
        if resp.status_code < 400:
            return
        body = resp.text.lower()
        if resp.status_code == 500 and ("out of memory" in body or "vram" in body or "cuda error" in body):
            raise VramExhausted(resp.text)
        raise UpstreamError(f"llama.cpp {resp.status_code}: {resp.text[:200]}")

    async def complete(
        self,
        messages: list[Message],
        model: str,
        temperature: float,
        options: dict | None = None,
    ) -> str:
        try:
            payload = self._guard_payload(self._payload(messages, model, temperature, False, options))
            resp = await self._client.post(
                f"{self._openai_url}/chat/completions",
                json=payload,
            )
        except httpx.ConnectError as exc:
            logger.warning("llama.cpp connect error: %s", exc)
            raise OllamaUnavailable(str(exc)) from exc
        except httpx.ReadTimeout as exc:
            raise UpstreamTimeout(f"llama.cpp read timeout: {exc}") from exc
        except httpx.HTTPError as exc:
            raise UpstreamError(f"llama.cpp transport error: {exc}") from exc

        self._raise_for_status(resp)
        data = resp.json()
        choices = data.get("choices") or []
        if not choices:
            raise UpstreamError("llama.cpp returned no choices")
        return self._sanitize_content(choices[0]["message"]["content"])

    async def stream_complete(
        self,
        messages: list[Message],
        model: str,
        temperature: float,
        options: dict | None = None,
    ) -> AsyncIterator[str]:
        try:
            payload = self._guard_payload(self._payload(messages, model, temperature, True, options))
            async with self._client.stream(
                "POST",
                f"{self._openai_url}/chat/completions",
                json=payload,
            ) as resp:
                if resp.status_code >= 400:
                    body = await resp.aread()
                    text = body.decode(errors="replace")
                    lowered = text.lower()
                    if resp.status_code == 500 and (
                        "out of memory" in lowered or "vram" in lowered or "cuda error" in lowered
                    ):
                        raise VramExhausted(text)
                    raise UpstreamError(f"llama.cpp {resp.status_code}: {text[:200]}")
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
                            yield self._sanitize_content(content, strip=False)
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue
        except httpx.ConnectError as exc:
            logger.warning("llama.cpp stream connect error: %s", exc)
            raise OllamaUnavailable(str(exc)) from exc
        except httpx.ReadTimeout as exc:
            raise UpstreamTimeout(f"llama.cpp stream timeout: {exc}") from exc
        except httpx.HTTPError as exc:
            raise UpstreamError(f"llama.cpp stream transport error: {exc}") from exc

    async def list_models(self) -> list[str]:
        try:
            resp = await self._client.get(f"{self._openai_url}/models")
        except httpx.ConnectError as exc:
            raise OllamaUnavailable(str(exc)) from exc
        except httpx.HTTPError as exc:
            raise UpstreamError(str(exc)) from exc

        self._raise_for_status(resp)
        data = resp.json()
        return [m["id"] for m in data.get("data", []) if "id" in m]
