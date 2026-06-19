"""Unit tests for ProviderRouter."""
import pytest
from app.services.provider_router import (
    TaskType,
    ProviderCapability,
    ProviderRouter,
    NoProviderError,
    _health_cache,
)


def test_task_type_enum_values():
    """TaskType enum exposes the 5 task categories."""
    assert TaskType.CHAT.value == "chat"
    assert TaskType.STRUCTMEM.value == "structmem"
    assert TaskType.SUMMARY.value == "summary"
    assert TaskType.CONTEXTUALIZE.value == "contextualize"
    assert TaskType.VISION.value == "vision"


def test_provider_capability_is_frozen():
    """ProviderCapability is immutable (frozen=True)."""
    cap = ProviderCapability(
        name="llama_cpp_12b",
        base_url="http://localhost:8080/v1",
        priority=1,
        supports={TaskType.CHAT, TaskType.CONTEXTUALIZE},
    )
    with pytest.raises((AttributeError, TypeError)):
        cap.priority = 99


def _cap(name, url, priority, supports, healthy=True):
    """Helper: build a ProviderCapability for tests."""
    return ProviderCapability(
        name=name,
        base_url=url,
        priority=priority,
        supports=supports,
        health_url=f"http://mock-{name}/health",
    )


async def test_select_returns_highest_priority_when_all_healthy(monkeypatch):
    """When all providers are healthy, select() returns lowest priority number."""
    async def fake_health(self, p):
        return True
    monkeypatch.setattr(ProviderRouter, "_is_healthy", fake_health)
    _health_cache.clear()

    providers = [
        _cap("e4b", "http://e4b/v1", 2, {TaskType.CHAT}),
        _cap("12b", "http://12b/v1", 1, {TaskType.CHAT}),
    ]
    router = ProviderRouter(providers)
    selected = await router.select(TaskType.CHAT, "fanpage")
    assert selected.name == "12b"


async def test_select_falls_back_when_top_unhealthy(monkeypatch):
    """When 12b is unhealthy, select() returns e4b."""
    async def fake_health(self, p):
        return p.name != "12b"
    monkeypatch.setattr(ProviderRouter, "_is_healthy", fake_health)
    _health_cache.clear()

    providers = [
        _cap("e4b", "http://e4b/v1", 2, {TaskType.CHAT}),
        _cap("12b", "http://12b/v1", 1, {TaskType.CHAT}),
    ]
    router = ProviderRouter(providers)
    selected = await router.select(TaskType.CHAT, "fanpage")
    assert selected.name == "e4b"


async def test_select_skips_provider_without_capability(monkeypatch):
    """A provider not supporting the task is filtered out even if high priority."""
    async def fake_health(self, p):
        return True
    monkeypatch.setattr(ProviderRouter, "_is_healthy", fake_health)
    _health_cache.clear()

    providers = [
        _cap("12b", "http://12b/v1", 1, {TaskType.CHAT}),  # does NOT support VISION
        _cap("e2b", "http://e2b/v1", 3, {TaskType.VISION}),
    ]
    router = ProviderRouter(providers)
    selected = await router.select(TaskType.VISION, "fanpage")
    assert selected.name == "e2b"


async def test_select_raises_no_provider_when_all_unhealthy(monkeypatch):
    """No healthy + capable provider → NoProviderError."""
    async def fake_health(self, p):
        return False
    monkeypatch.setattr(ProviderRouter, "_is_healthy", fake_health)
    _health_cache.clear()

    providers = [_cap("12b", "http://12b/v1", 1, {TaskType.CHAT})]
    router = ProviderRouter(providers)
    with pytest.raises(NoProviderError) as exc_info:
        await router.select(TaskType.CHAT, "fanpage")
    assert "chat" in str(exc_info.value).lower()


async def test_health_cache_avoids_recheck(monkeypatch):
    """A second call within TTL should not re-invoke the underlying check."""
    import time as _time
    call_count = {"n": 0}

    async def fake_health(self, p):
        # Mirror real _is_healthy: respect cache, only call on miss.
        now = _time.monotonic()
        cached = _health_cache.get(p.name)
        if cached and (now - cached[1]) < self._ttl:
            return cached[0]
        call_count["n"] += 1
        _health_cache[p.name] = (True, now)
        return True
    monkeypatch.setattr(ProviderRouter, "_is_healthy", fake_health)
    _health_cache.clear()

    providers = [_cap("12b", "http://12b/v1", 1, {TaskType.CHAT})]
    router = ProviderRouter(providers, health_check_ttl_sec=30)

    await router.select(TaskType.CHAT, "fanpage")  # 1st call
    await router.select(TaskType.CHAT, "fanpage")  # 2nd (cached)
    await router.select(TaskType.CHAT, "fanpage")  # 3rd (cached)
    assert call_count["n"] == 1


async def test_minimax_fallback_when_all_local_down(monkeypatch):
    """When all local providers unhealthy, falls back to MiniMax cloud."""
    async def fake_health(self, p):
        return p.name == "minimax_m3"
    monkeypatch.setattr(ProviderRouter, "_is_healthy", fake_health)
    _health_cache.clear()

    providers = [
        _cap("12b", "http://12b/v1", 1, {TaskType.CHAT}),
        _cap("e4b", "http://e4b/v1", 2, {TaskType.CHAT}),
        _cap("minimax_m3", "https://api.minimax.io/v1", 10, {TaskType.CHAT}),
    ]
    router = ProviderRouter(providers)
    selected = await router.select(TaskType.CHAT, "fanpage")
    assert selected.name == "minimax_m3"
