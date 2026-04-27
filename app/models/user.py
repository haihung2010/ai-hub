"""Typed records for users and sessions used by the user/summary pipeline."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class UserRecord:
    id: str
    tenant_id: str
    name: str


@dataclass(frozen=True)
class SessionRecord:
    id: str
    project_id: str
    user_id: str | None
    created_at: str
    last_message_preview: str | None = None
