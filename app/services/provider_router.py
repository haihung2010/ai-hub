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

    async def select(self, task: TaskType, project_id: str) -> ProviderCapability:
        """Pick the highest-priority healthy provider that supports `task`.

        `project_id` is accepted for future per-project overrides; current
        implementation ignores it (priority is global).

        Raises NoProviderError if no provider matches.
        """
        candidates = [p for p in self._providers if task in p.supports]
        for p in candidates:
            if await self._is_healthy(p):
                return p
        raise NoProviderError(
            f"No healthy provider supports task={task.value} "
            f"(tried {len(candidates)} providers)"
        )

    async def _is_healthy(self, p: ProviderCapability) -> bool:
        """Check provider health, with TTL cache to avoid hammering.

        Default stub: assume healthy. Tests monkeypatch this. Task 8
        replaces with real httpx-based check.
        """
        now = time.monotonic()
        cached = _health_cache.get(p.name)
        if cached and (now - cached[1]) < self._ttl:
            return cached[0]
        # default stub: healthy. Real impl in follow-up task.
        _health_cache[p.name] = (True, now)
        return True
