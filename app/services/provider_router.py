"""Provider router: capability + priority based selection with health caching.

Replaces ad-hoc provider init in main.py. See docs/superpowers/specs/
2026-06-20-aihub-quality-fixes-design.md for context.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum


class TaskType(str, Enum):
    """Categories of LLM task, used to filter providers by capability."""
    CHAT = "chat"
    STRUCTMEM = "structmem"
    SUMMARY = "summary"
    CONTEXTUALIZE = "contextualize"
    VISION = "vision"


@dataclass(frozen=True)
class ProviderCapability:
    """A single LLM provider's static configuration."""
    name: str
    base_url: str
    priority: int  # 1=highest, 10=lowest (cloud fallback)
    supports: set[TaskType] = field(default_factory=set)
    health_url: str | None = None  # defaults to base_url.rsplit("/v1", 1)[0] + "/health"


class NoProviderError(RuntimeError):
    """Raised when no healthy provider supports the requested task."""


# Module-level health cache: {provider_name: (is_healthy: bool, checked_at: float)}
_health_cache: dict[str, tuple[bool, float]] = {}


class ProviderRouter:
    """Selects a provider for a given (task, project_id) tuple.

    Sort providers by priority, return first healthy + capable. Caches health
    checks for 30s by default to avoid hammering llama.cpp /health endpoints.
    """

    def __init__(
        self,
        providers: list[ProviderCapability],
        health_check_ttl_sec: int = 30,
    ):
        if not providers:
            raise ValueError("providers list must not be empty")
        self._providers = sorted(providers, key=lambda p: p.priority)
        self._ttl = health_check_ttl_sec
