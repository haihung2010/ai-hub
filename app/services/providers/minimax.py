"""MiniMax M3 cloud LLM provider (Anthropic-compatible Messages API).

The provider mirrors the interface of OpenRouterProvider but speaks the
Anthropic Messages protocol so we can use explicit ``cache_control`` for
prompt caching. The Token Plan Max subscription key is read from
``MINIMAX_API_KEY``; when empty or ``MINIMAX_ENABLED=false`` the
provider is never constructed.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.core.errors import UpstreamError, UpstreamTimeout
from app.models.chat import Message

logger = logging.getLogger(__name__)


class MiniMaxProvider:
    name = "minimax"

    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        api_key: str,
        model: str,
        base_url: str = "https://api.minimax.io/v1",
        enable_caching: bool = True,
        timeout_seconds: float = 90.0,
    ) -> None:
        self._client = client
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._enable_caching = enable_caching
        self._timeout_seconds = timeout_seconds

    # ── Request building ──────────────────────────────────────────────

    def _build_payload(
        self,
        messages: list[Message],
        temperature: float,
        options: dict | None,
    ) -> dict[str, Any]:
        system_text, non_system = _split_system(messages)
        body: dict[str, Any] = {
            "model": self._model,
            "messages": [m.model_dump(exclude_none=True) for m in non_system],
            "temperature": temperature,
        }
        if system_text:
            body["system"] = self._format_system(system_text)
        if options:
            if "max_tokens" in options and options["max_tokens"]:
                body["max_tokens"] = options["max_tokens"]
        # Cache control: mark the system block (rarely changes) and the
        # last user message (gets a cache hit on the second turn onward).
        if self._enable_caching:
            if "system" in body and isinstance(body["system"], list):
                body["system"][-1]["cache_control"] = {"type": "ephemeral"}
            if body["messages"]:
                body["messages"][-1]["cache_control"] = {"type": "ephemeral"}
        return body

    def _format_system(self, text: str) -> Any:
        if self._enable_caching:
            return [{"type": "text", "text": text}]
        return text

    def _headers(self) -> dict[str, str]:
        return {
            "x-api-key": self._api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }

    # ── Non-streaming ─────────────────────────────────────────────────

    async def complete(
        self,
        messages: list[Message],
        model: str,
        temperature: float,
        options: dict | None = None,
    ) -> str:
        if not self._api_key:
            raise UpstreamError("minimax api key is not configured")
        # Allow caller to override the model for one call (e.g. benchmark)
        if model and model != self._model:
            self._model = model
        payload = self._build_payload(messages, temperature, options)
        try:
            resp = await self._client.post(
                f"{self._base_url}/messages",
                json=payload,
                headers=self._headers(),
                timeout=self._timeout_seconds,
            )
        except httpx.ReadTimeout as exc:
            raise UpstreamTimeout(f"minimax read timeout: {exc}") from exc
        except httpx.HTTPError as exc:
            raise UpstreamError(f"minimax transport error: {exc}") from exc

        if resp.status_code >= 400:
            raise UpstreamError(
                f"minimax {resp.status_code}: {_safe_error(resp)}"
            )
        data = resp.json()
        return _extract_text(data)

    # ── Streaming ─────────────────────────────────────────────────────

    async def stream_complete(
        self,
        messages: list[Message],
        model: str,
        temperature: float,
        options: dict | None = None,
    ) -> AsyncIterator[str]:
        if not self._api_key:
            raise UpstreamError("minimax api key is not configured")
        if model and model != self._model:
            self._model = model
        payload = self._build_payload(messages, temperature, options)
        payload["stream"] = True
        try:
            async with self._client.stream(
                "POST",
                f"{self._base_url}/messages",
                json=payload,
                headers=self._headers(),
                timeout=self._timeout_seconds,
            ) as resp:
                if resp.status_code >= 400:
                    body = await resp.aread()
                    raise UpstreamError(
                        f"minimax {resp.status_code}: "
                        f"{body.decode(errors='replace')[:200]}"
                    )
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    raw = line[6:]
                    if not raw or raw == "[DONE]":
                        continue
                    try:
                        event = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    # Only content_block_delta events carry text fragments
                    if event.get("type") == "content_block_delta":
                        delta = event.get("delta") or {}
                        if delta.get("type") == "text_delta":
                            text = delta.get("text")
                            if text:
                                yield text
        except httpx.ReadTimeout as exc:
            raise UpstreamTimeout(f"minimax stream read timeout: {exc}") from exc
        except httpx.HTTPError as exc:
            raise UpstreamError(
                f"minimax stream transport error: {exc}"
            ) from exc


# ── Module-level helpers ────────────────────────────────────────────────


def _split_system(messages: list[Message]) -> tuple[str, list[Message]]:
    """Return (concatenated_system_text, non_system_messages).

    Anthropic Messages API uses a top-level ``system`` field, not a
    system message. We concatenate any leading system messages (in
    practice there is at most one) so callers that pass them in the
    list still work.
    """
    system_parts: list[str] = []
    rest: list[Message] = []
    for m in messages:
        if m.role == "system":
            system_parts.append(m.content)
        else:
            rest.append(m)
    return ("\n\n".join(system_parts), rest)


def _safe_error(resp: httpx.Response) -> str:
    try:
        payload: dict[str, Any] = resp.json()
    except ValueError:
        return resp.text[:200]
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        return str(error.get("message") or "provider returned error")[:200]
    return str(payload)[:200]


def _extract_text(data: dict[str, Any]) -> str:
    """Concatenate all text blocks in an Anthropic Messages response.

    Thinking blocks and tool-use blocks are ignored.
    """
    blocks = data.get("content") or []
    parts: list[str] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            text = block.get("text")
            if text:
                parts.append(text)
    if not parts:
        logger.warning(
            "minimax returned no text blocks content=%s",
            json.dumps(blocks)[:200],
        )
    return "".join(parts)
