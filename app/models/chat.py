"""Pydantic request/response schemas for the /v1/chat endpoint."""

from typing import Literal

from pydantic import BaseModel, Field

ProviderName = Literal["local", "cloud"]
ModelMode = Literal["normal", "lite"]


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
    model_mode: ModelMode = Field(default="normal")
    enable_search: bool = Field(default=False)


class ChatResponse(BaseModel):
    project_id: str
    session_id: str
    model: str
    provider: str
    content: str
    user_id: str | None = None
