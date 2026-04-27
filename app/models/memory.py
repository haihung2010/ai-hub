from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

MemoryType = Literal["episodic", "semantic", "relational", "procedural"]


@dataclass(frozen=True)
class MemoryEpisodeRecord:
    id: str
    user_id: str
    tenant_id: str
    project_id: str
    session_id: str
    start_message_id: int
    end_message_id: int
    source_text: str
    event_time: str
    created_at: str


@dataclass(frozen=True)
class MemoryItemRecord:
    id: str
    episode_id: str
    user_id: str
    tenant_id: str
    project_id: str
    memory_type: MemoryType
    subject: str | None
    predicate: str | None
    object: str | None
    content: str
    salience: float
    valid_from: str | None
    valid_to: str | None
    last_accessed_at: str | None
    created_at: str


@dataclass(frozen=True)
class MemoryConsolidationRecord:
    id: str
    user_id: str
    tenant_id: str
    project_id: str
    scope_key: str
    source_episode_ids: str
    content: str
    version: int
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class RetrievedMemoryBundle:
    procedural: list[MemoryItemRecord]
    semantic: list[MemoryItemRecord]
    relational: list[MemoryItemRecord]
    episodic: list[MemoryItemRecord]
    consolidated: list[MemoryConsolidationRecord]
