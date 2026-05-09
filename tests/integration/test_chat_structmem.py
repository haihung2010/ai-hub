"""Integration tests for StructMem-backed chat context."""

from __future__ import annotations

import uuid

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.core.database import get_db_connection
from app.main import create_app
from app.middleware.security import AuthFailureTracker, InMemoryRateLimiter
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
    limiter = InMemoryRateLimiter(limit=settings.rate_limit_per_minute)
    tracker = AuthFailureTracker(limit=settings.auth_failure_limit, block_seconds=settings.auth_failure_block_seconds)
    app = create_app(settings=settings, limiter=limiter, failure_tracker=tracker)
    with TestClient(app) as client:
        client.headers.update({"X-API-KEY": settings.api_key})
        route = mock_api.post("http://llama.test/v1/chat/completions").mock(
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
                "SELECT id FROM users WHERE tenant_id = %s AND name = %s",
                (tenant_id, user_name),
            ).fetchone()
            assert user is not None
            session = conn.execute(
                "SELECT id FROM sessions WHERE tenant_id = %s AND user_id = %s",
                (tenant_id, user["id"]),
            ).fetchone()
            episode_id = f"episode-{uuid.uuid4()}"
            conn.execute(
                "INSERT INTO memory_episodes (id, user_id, tenant_id, project_id, session_id, start_message_id, end_message_id, source_text) VALUES (%s, %s, %s, %s, %s, 1, 2, 'test')",
                (episode_id, user["id"], tenant_id, "vehix", session["id"]),
            )
            conn.execute(
                "INSERT INTO memory_items (id, episode_id, user_id, tenant_id, project_id, memory_type, content, salience) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    f"memory-{uuid.uuid4()}",
                    episode_id,
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
