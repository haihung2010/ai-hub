"""Live integration test: ProviderRouter talks to real llama.cpp /health.

Skipped if llama.cpp ports 8080/8081 are not reachable.
"""
import pytest
import httpx
from app.services.provider_router import (
    TaskType, ProviderCapability, ProviderRouter, _health_cache,
)


def _llama_cpp_running():
    try:
        r = httpx.get("http://localhost:8080/health", timeout=2.0)
        return r.status_code == 200
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _llama_cpp_running(),
    reason="llama.cpp on 8080 not running",
)


async def test_live_select_12b_chat():
    _health_cache.clear()
    providers = [
        ProviderCapability("12b", "http://localhost:8080/v1", 1, {TaskType.CHAT}),
        ProviderCapability("e4b", "http://localhost:8081/v1", 2, {TaskType.CHAT, TaskType.STRUCTMEM}),
    ]
    router = ProviderRouter(providers, health_check_ttl_sec=5)
    selected = await router.select(TaskType.CHAT, "fanpage")
    assert selected.name == "12b"
    selected2 = await router.select(TaskType.CHAT, "fanpage")
    assert selected2.name == "12b"


async def test_live_select_structmem_returns_e4b():
    _health_cache.clear()
    providers = [
        ProviderCapability("12b", "http://localhost:8080/v1", 1, {TaskType.CHAT}),
        ProviderCapability("e4b", "http://localhost:8081/v1", 2, {TaskType.CHAT, TaskType.STRUCTMEM, TaskType.SUMMARY}),
    ]
    router = ProviderRouter(providers, health_check_ttl_sec=5)
    selected = await router.select(TaskType.STRUCTMEM, "fanpage")
    assert selected.name == "e4b"
