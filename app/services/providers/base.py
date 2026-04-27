"""Provider Protocol shared by Ollama (local) and 9Router (cloud) backends."""

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
