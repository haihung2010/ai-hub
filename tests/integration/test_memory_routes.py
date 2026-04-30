"""Memory admin/debug route tests."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.services.pinned_memory_service import PinnedMemoryService
from app.services.summary_service import SummaryService
from app.services.user_service import UserService


@pytest.mark.integration
def test_memory_endpoint_requires_api_key(client: TestClient) -> None:
    client.headers.clear()

    response = client.get("/v1/memory", params={"tenant_id": "default", "project_id": "vehix", "user_name": "hung"})

    assert response.status_code == 401


@pytest.mark.integration
def test_memory_endpoint_lists_pinned_memory_and_summary(client: TestClient) -> None:
    users = UserService()
    user = users.get_or_create_user("hung-memory-route", "default")
    pinned = PinnedMemoryService()
    pinned.upsert_memory("default", "vehix", user.id, "mqtt", "Vehix uses MQTT")
    SummaryService().upsert_summary(user.id, "vehix", "User manages Vehix IoT.", "default")

    response = client.get(
        "/v1/memory",
        params={"tenant_id": "default", "project_id": "vehix", "user_name": "hung-memory-route"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["user"]["name"] == "hung-memory-route"
    assert payload["summary"]["content"] == "User manages Vehix IoT."
    assert payload["pinned_memories"][0]["value"] == "Vehix uses MQTT"
