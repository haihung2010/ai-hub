"""Provider Protocol shared by Ollama (local) and OpenRouter (cloud) backends."""

from collections.abc import AsyncIterator
from typing import Protocol

from app.models.chat import Message


class ChatProvider(Protocol):
    name: str

    async def complete(
        self,
        messages: list[Message],
        model: str,
        temperature: float,
        options: dict | None = None,
    ) -> str: ...

    async def stream_complete(
        self,
        messages: list[Message],
        model: str,
        temperature: float,
        options: dict | None = None,
    ) -> AsyncIterator[str]: ...
