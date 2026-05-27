"""Virtual API key behavior for small-team policy enforcement."""

from __future__ import annotations

import hashlib
from uuid import uuid4

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.core.database import get_db_connection
from tests.conftest import make_ollama_chat_response


def _insert_virtual_key(raw_key: str, *, allow_external: bool = False, rpm_limit: int = 60) -> str:
    key_id = f"key_{uuid4().hex}"
    key_hash = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
    with get_db_connection() as conn:
        conn.execute(
            "INSERT INTO api_keys (id, key_hash, name, tenant_id, allow_external, rpm_limit, max_parallel_requests) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (key_id, key_hash, "test key", "default", int(allow_external), rpm_limit, 2),
        )
        conn.commit()
    return key_id


@pytest.mark.integration
def test_virtual_api_key_can_call_local_chat(client: TestClient, mock_api: respx.MockRouter) -> None:
    raw_key = f"vh_{uuid4().hex}"
    key_id = _insert_virtual_key(raw_key, allow_external=False)
    client.headers.clear()
    client.headers.update({"X-API-KEY": raw_key})
    mock_api.post("http://llama.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=make_ollama_chat_response("local ok"))
    )

    response = client.post(
        "/v1/chat",
        json={"project_id": "iot", "user_message": "hi"},
    )

    assert response.status_code == 200
    assert response.json()["provider"] == "llama_cpp"
    with get_db_connection() as conn:
        row = conn.execute(
            "SELECT api_key_id FROM usage_events WHERE api_key_id = %s ORDER BY created_at DESC LIMIT 1",
            (key_id,),
        ).fetchone()
    assert row is not None


@pytest.mark.integration
def test_virtual_api_key_uses_its_own_rpm_limit(client: TestClient, mock_api: respx.MockRouter) -> None:
    raw_key = f"vh_{uuid4().hex}"
    _insert_virtual_key(raw_key, allow_external=False, rpm_limit=1)
    client.headers.clear()
    client.headers.update({"X-API-KEY": raw_key})
    mock_api.post("http://llama.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=make_ollama_chat_response("local ok"))
    )

    first = client.post("/v1/chat", json={"project_id": "iot", "user_message": "one"})
    second = client.post("/v1/chat", json={"project_id": "iot", "user_message": "two"})

    assert first.status_code == 200
    assert second.status_code == 429


@pytest.mark.integration
def test_virtual_api_key_denies_external_when_policy_disallows(client: TestClient) -> None:
    raw_key = f"vh_{uuid4().hex}"
    _insert_virtual_key(raw_key, allow_external=False)
    client.headers.clear()
    client.headers.update({"X-API-KEY": raw_key})

    response = client.post(
        "/v1/chat",
        json={
            "project_id": "test",
            "user_message": "use cloud",
            "provider": "cloud",
            "allow_external": True,
        },
    )

    assert response.status_code == 403
    assert "external" in response.json()["detail"].lower()
