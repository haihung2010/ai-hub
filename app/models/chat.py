"""Pydantic request/response schemas for the /v1/chat endpoint."""

from typing import Literal

from pydantic import BaseModel, Field

ProviderName = Literal["local", "cloud", "gemini"]
ModelMode = Literal["lite", "normal", "external"]


class Message(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1)
    images: list[str] | None = None


class ChatRequest(BaseModel):
    project_id: str = Field(min_length=1, max_length=64)
    tenant_id: str = Field(default="default", min_length=1, max_length=64)
    user_name: str | None = Field(default=None, min_length=1, max_length=64)
    user_message: str = Field(min_length=1)
    images: list[str] | None = None
    history: list[Message] = Field(default_factory=list)
    session_id: str | None = None
    stream: bool = False
    provider: ProviderName | None = None
    model_mode: ModelMode = Field(default="lite")
    allow_external: bool | None = Field(default=None)
    enable_search: bool = Field(default=False)
    max_tokens: int | None = Field(default=None, ge=1, le=8192)
    priority: int = Field(default=0, ge=0, le=10)


class ChatResponse(BaseModel):
    project_id: str
    session_id: str
    model: str
    provider: str
    content: str
    user_id: str | None = None
    route: str = "local"
    latency_ms: float = 0.0
    queue_wait_ms: float | None = None
    route_reason: str | None = None
    fallback_used: bool = False
    sources: list[str] = Field(default_factory=list)
    usage: dict[str, object] | None = None
