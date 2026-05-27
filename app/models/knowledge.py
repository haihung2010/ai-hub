from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field, field_validator

KnowledgeStatus = Literal["active", "draft", "archived"]


class KnowledgeCardCreate(BaseModel):
    project_id: str = Field(min_length=1, max_length=64)
    tenant_id: str = Field(default="default", min_length=1, max_length=64)
    knowledge_domain: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=200)
    summary: str = ""
    content: str = Field(min_length=1)
    source_type: str = Field(default="manual", min_length=1, max_length=80)
    trust_level: int = Field(default=3, ge=1, le=5)
    status: KnowledgeStatus = "active"
    version: int = Field(default=1, ge=1)
    effective_from: str | None = None
    effective_to: str | None = None
    tags: list[str] = Field(default_factory=list)
    owner: str | None = Field(default=None, max_length=120)

    @field_validator("project_id", "tenant_id", "knowledge_domain", "title", "source_type", mode="before")
    @classmethod
    def _strip_required_text(cls, value: str) -> str:
        if not isinstance(value, str):
            raise ValueError("value must be a string")
        stripped = value.strip()
        if not stripped:
            raise ValueError("value cannot be empty")
        return stripped

    @field_validator("summary", "content", mode="before")
    @classmethod
    def _strip_text(cls, value: str) -> str:
        if not isinstance(value, str):
            raise ValueError("value must be a string")
        return value.strip()

    @field_validator("tags", mode="before")
    @classmethod
    def _normalize_tags(cls, value: list[str] | str | None) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [tag.strip() for tag in value.split(",") if tag.strip()]
        return [tag.strip() for tag in value if tag.strip()]


@dataclass(frozen=True)
class KnowledgeCardRecord:
    id: str
    tenant_id: str
    project_id: str
    knowledge_domain: str
    title: str
    summary: str
    content: str
    source_type: str
    trust_level: int
    status: KnowledgeStatus
    version: int
    effective_from: str | None
    effective_to: str | None
    tags: list[str]
    owner: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class KnowledgeChunkRecord:
    id: str
    card_id: str
    tenant_id: str
    project_id: str
    chunk_index: int
    content: str
    token_estimate: int
    created_at: str


class KnowledgeSearchRequest(BaseModel):
    project_id: str = Field(min_length=1, max_length=64)
    tenant_id: str = Field(default="default", min_length=1, max_length=64)
    query: str = Field(min_length=1)
    knowledge_domain: str | None = Field(default=None, min_length=1, max_length=80)
    limit: int = Field(default=4, ge=1, le=10)


class KnowledgeSearchResult(BaseModel):
    card_id: str
    chunk_id: str
    project_id: str
    knowledge_domain: str
    title: str
    summary: str
    content: str
    source_type: str
    trust_level: int
    version: int
    score: float
    tags: list[str] = Field(default_factory=list)
    embedding: bytes | None = Field(default=None, exclude=True)
