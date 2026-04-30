"""Pinned memory service tests."""

from __future__ import annotations

import uuid

import pytest

from app.core.database import init_db
from app.services.pinned_memory_service import PinnedMemoryService
from app.services.user_service import UserService


@pytest.mark.unit
def test_pinned_memory_upsert_updates_same_scope() -> None:
    init_db()
    tenant = f"tenant-{uuid.uuid4()}"
    users = UserService()
    user = users.get_or_create_user("hung", tenant)
    service = PinnedMemoryService()

    first = service.upsert_memory(tenant, "vehix", user.id, "mqtt", "uses mqtt")
    second = service.upsert_memory(tenant, "vehix", user.id, "mqtt", "uses mqtt v5")
    memories = service.list_memories(tenant, "vehix", user.id)

    assert second.id == first.id
    assert [memory.value for memory in memories] == ["uses mqtt v5"]


@pytest.mark.unit
def test_pinned_memory_isolated_by_tenant_project_and_user() -> None:
    init_db()
    tenant_a = f"tenant-a-{uuid.uuid4()}"
    tenant_b = f"tenant-b-{uuid.uuid4()}"
    users = UserService()
    user_a = users.get_or_create_user("hung", tenant_a)
    user_b = users.get_or_create_user("hung", tenant_b)
    service = PinnedMemoryService()

    service.upsert_memory(tenant_a, "vehix", user_a.id, "rule", "vehix fact")
    service.upsert_memory(tenant_a, "doden", user_a.id, "rule", "doden fact")
    service.upsert_memory(tenant_b, "vehix", user_b.id, "rule", "other tenant fact")

    memories = service.list_memories(tenant_a, "vehix", user_a.id)

    assert [memory.value for memory in memories] == ["vehix fact"]


@pytest.mark.unit
def test_pinned_memory_format_for_prompt_excludes_inactive() -> None:
    init_db()
    tenant = f"tenant-{uuid.uuid4()}"
    users = UserService()
    user = users.get_or_create_user("hung", tenant)
    service = PinnedMemoryService()
    memory = service.upsert_memory(tenant, "vehix", user.id, "mqtt", "device uses MQTT")
    service.upsert_memory(tenant, "vehix", user.id, "cadence", "telemetry every 5s")
    service.deactivate_memory(memory.id)

    prompt = service.format_for_prompt(tenant, "vehix", user.id)

    assert "telemetry every 5s" in prompt
    assert "device uses MQTT" not in prompt
    assert prompt.startswith("### SYSTEM: PINNED MEMORY ###")
