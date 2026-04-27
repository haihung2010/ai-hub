"""Integration tests for StructMem-backed chat context."""

from __future__ import annotations

import uuid

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.core.database import get_db_connection
from app.main import create_app
from tests.conftest import make_ollama_chat_response
from tests.integration.test_chat_endpoint import _last_payload


@pytest.mark.integration
def test_chat_injects_structmem_system_blocks(
    settings,
    mock_api: respx.MockRouter,
) -> None:
    settings.enable_structmem = True
    tenant_id = f"tenant-{uuid.uuid4()}"
    user_name = f"Hung-{uuid.uuid4()}"
    app = create_app(settings=settings)
    with TestClient(app) as client:
        client.headers.update({"X-API-KEY": settings.api_key})
        route = mock_api.post("http://ollama.test/v1/chat/completions").mock(
            return_value=httpx.Response(200, json=make_ollama_chat_response("I know you."))
        )

        first = client.post(
            "/v1/chat",
            json={
                "project_id": "vehix",
                "tenant_id": tenant_id,
                "user_name": user_name,
                "user_message": "I manage internal outsourcing projects.",
            },
        )
        assert first.status_code == 200

        with get_db_connection() as conn:
            user = conn.execute(
                "SELECT id FROM users WHERE tenant_id = ? AND name = ?",
                (tenant_id, user_name),
            ).fetchone()
            assert user is not None
            conn.execute(
                "INSERT INTO memory_items (id, episode_id, user_id, tenant_id, project_id, memory_type, content, salience) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    f"memory-{uuid.uuid4()}",
                    f"episode-{uuid.uuid4()}",
                    user["id"],
                    tenant_id,
                    "vehix",
                    "semantic",
                    "The user manages internal outsourcing projects.",
                    1.0,
                ),
            )
            conn.commit()

        second = client.post(
            "/v1/chat",
            json={
                "project_id": "vehix",
                "tenant_id": tenant_id,
                "user_name": user_name,
                "user_message": "Who am I?",
            },
        )

        assert second.status_code == 200
        payload = _last_payload(route)
        system_messages = [message["content"] for message in payload["messages"] if message["role"] == "system"]
        assert any("### SYSTEM: SEMANTIC MEMORY ###" in content for content in system_messages)
        assert any("The user manages internal outsourcing projects." in content for content in system_messages)
