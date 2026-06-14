"""Verify cross-tenant access is blocked at the route layer (P5.2, 2026-06-14).

Each fix below wires ``app.utils.tenant_guard.resolve_tenant`` (or
``assert_entity_tenant``) into a route that previously trusted a
client-supplied tenant_id. A non-admin key bound to tenant ``foo``
must NOT be able to read or write data in tenant ``bar`` by passing
``?tenant_id=bar`` or by knowing a path-param id from tenant ``bar``.

The master key (X-API-KEY matching settings.api_key) is a special case:
its ``api_key_tenant_id`` is ``None`` and ``api_key_is_admin`` is True,
so it legitimately sees across tenants. We exercise both paths.

Failure posture: mismatch returns 403 (request-time guard) or 404
(entity-tenant mismatch, after a fetch — we use 404 so we don't leak
existence of foreign-tenant rows).
"""

from __future__ import annotations

import hashlib
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.database import get_db_connection


# ── Fixtures / helpers ─────────────────────────────────────────────────


def _insert_key(
    raw_key: str,
    *,
    tenant_id: str = "default",
    is_admin: bool = False,
) -> str:
    key_id = f"key_{uuid4().hex}"
    key_hash = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
    with get_db_connection() as conn:
        conn.execute(
            "INSERT INTO api_keys (id, key_hash, name, tenant_id, is_admin, allow_external, rpm_limit, max_parallel_requests, enabled) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 1)",
            (key_id, key_hash, "test key", tenant_id, int(is_admin), 1, 60, 2),
        )
        conn.commit()
    return key_id


def _insert_user(name: str, tenant_id: str) -> str:
    user_id = f"user_{uuid4().hex}"
    with get_db_connection() as conn:
        conn.execute(
            "INSERT INTO users (id, tenant_id, name) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
            (user_id, tenant_id, name),
        )
        conn.commit()
    return user_id


def _use_key(client: TestClient, raw_key: str) -> None:
    client.headers.clear()
    client.headers.update({"X-API-KEY": raw_key})


# ── users.py ───────────────────────────────────────────────────────────


@pytest.mark.integration
def test_user_sessions_blocks_cross_tenant_query(client: TestClient) -> None:
    """GET /v1/users/{user_name}/sessions — claimed tenant must match key."""
    _insert_user("alice-foo", "foo")
    _insert_user("alice-bar", "bar")
    foo_key = f"vh_{uuid4().hex}"
    _insert_key(foo_key, tenant_id="foo")

    _use_key(client, foo_key)
    response = client.get(
        "/v1/users/alice-bar/sessions",
        params={"tenant_id": "bar", "project_id": "iot"},
    )
    assert response.status_code == 403, response.text
    assert "tenant_id mismatch" in response.text.lower()


@pytest.mark.integration
def test_user_sessions_allows_master_key_across_tenants(client: TestClient) -> None:
    """Master key has no bound tenant → may list any tenant's sessions."""
    # The conftest already attached the master key on the client.
    response = client.get(
        "/v1/users/whoever/sessions",
        params={"tenant_id": "anything", "project_id": "iot"},
    )
    # Returns [] because no user exists, NOT 403 — proves the
    # tenant guard let the master key through.
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.integration
def test_user_history_blocks_cross_tenant(client: TestClient) -> None:
    _insert_user("ghost-bar", "bar")
    foo_key = f"vh_{uuid4().hex}"
    _insert_key(foo_key, tenant_id="foo")
    _use_key(client, foo_key)

    response = client.delete(
        "/v1/users/ghost-bar/history",
        params={"tenant_id": "bar", "project_id": "iot"},
    )
    assert response.status_code == 403


@pytest.mark.integration
def test_memory_boundary_blocks_cross_tenant(client: TestClient) -> None:
    _insert_user("ghost-bar", "bar")
    foo_key = f"vh_{uuid4().hex}"
    _insert_key(foo_key, tenant_id="foo")
    _use_key(client, foo_key)

    response = client.post(
        "/v1/users/ghost-bar/memory-boundary",
        params={"tenant_id": "bar", "project_id": "iot"},
    )
    assert response.status_code == 403


# ── memory.py ──────────────────────────────────────────────────────────


@pytest.mark.integration
def test_memory_endpoint_blocks_cross_tenant(client: TestClient) -> None:
    _insert_user("ghost-bar", "bar")
    foo_key = f"vh_{uuid4().hex}"
    _insert_key(foo_key, tenant_id="foo")
    _use_key(client, foo_key)

    response = client.get(
        "/v1/memory",
        params={"tenant_id": "bar", "project_id": "iot", "user_name": "ghost-bar"},
    )
    assert response.status_code == 403


# ── predictions.py ─────────────────────────────────────────────────────


@pytest.mark.integration
def test_predictions_blocks_cross_tenant(client: TestClient) -> None:
    _insert_user("ghost-bar", "bar")
    foo_key = f"vh_{uuid4().hex}"
    _insert_key(foo_key, tenant_id="foo")
    _use_key(client, foo_key)

    response = client.get(
        "/v1/predictions",
        params={
            "tenant_id": "bar",
            "project_id": "stock_prediction",
            "user_name": "ghost-bar",
        },
    )
    assert response.status_code == 403


# ── knowledge.py (entity-tenant check on path-param card_id) ───────────


def _insert_card(card_id: str, tenant_id: str, project_id: str = "iot") -> None:
    suffix = uuid4().hex[:8]
    full_id = f"{card_id}-{suffix}"
    with get_db_connection() as conn:
        conn.execute(
            "INSERT INTO knowledge_cards (id, tenant_id, project_id, knowledge_domain, title, content, source_type, trust_level, status) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'active')",
            (full_id, tenant_id, project_id, "general", f"title-{card_id}-{suffix}", "c", "manual", 3),
        )
        conn.commit()
    return full_id


@pytest.mark.integration
def test_knowledge_related_blocks_cross_tenant(client: TestClient) -> None:
    card_id = _insert_card("card-bar-1", "bar")
    foo_key = f"vh_{uuid4().hex}"
    _insert_key(foo_key, tenant_id="foo")
    _use_key(client, foo_key)

    response = client.get(f"/v1/knowledge/cards/{card_id}/related")
    # Foreign-tenant card fetch returns 404 (not 403) so we don't
    # leak existence of bar's rows to foo.
    assert response.status_code == 404


@pytest.mark.integration
def test_knowledge_relink_blocks_cross_tenant(client: TestClient) -> None:
    card_id = _insert_card("card-bar-2", "bar")
    foo_key = f"vh_{uuid4().hex}"
    _insert_key(foo_key, tenant_id="foo")
    _use_key(client, foo_key)

    response = client.post(f"/v1/knowledge/cards/{card_id}/relink")
    assert response.status_code == 404


@pytest.mark.integration
def test_knowledge_related_allows_admin_key(client: TestClient) -> None:
    card_id = _insert_card("card-bar-3", "bar")
    admin_key = f"vh_{uuid4().hex}"
    _insert_key(admin_key, tenant_id="bar", is_admin=True)
    _use_key(client, admin_key)

    response = client.get(f"/v1/knowledge/cards/{card_id}/related")
    # Admin keys bypass the tenant guard; the route returns 200 with
    # possibly-empty related list (no relations seeded).
    assert response.status_code == 200


# ── knowledge.py list — force-override to bound tenant ─────────────────


@pytest.mark.integration
def test_knowledge_list_blocks_cross_tenant(client: TestClient) -> None:
    """A foo-bound key claiming tenant=bar must be rejected. Otherwise
    the client could probe for cards in foreign tenants."""
    foo_card = _insert_card(f"card-foo-{uuid4().hex[:6]}", "foo")
    bar_card = _insert_card(f"card-bar-{uuid4().hex[:6]}", "bar")

    foo_key = f"vh_{uuid4().hex}"
    _insert_key(foo_key, tenant_id="foo")
    _use_key(client, foo_key)

    response = client.get(
        "/v1/knowledge/cards",
        params={"project_id": "iot", "tenant_id": "bar"},
    )
    # Tenant mismatch returns 403 — same posture as users/memory routes.
    assert response.status_code == 403
    # The bar card must NEVER have been readable from foo's key.
    leaked = client.get(
        "/v1/knowledge/cards",
        params={"project_id": "iot", "tenant_id": "foo"},
    )
    assert leaked.status_code == 200
    ids = [c["id"] for c in leaked.json()["cards"]]
    assert foo_card in ids
    assert bar_card not in ids


@pytest.mark.integration
def test_knowledge_list_allows_admin_to_read_own_tenant(client: TestClient) -> None:
    """Admin key scoped to bar can list bar's cards (we only need
    admin bypass for cross-tenant reads; in-tenant reads work for any
    key bound to the right tenant)."""
    bar_card = _insert_card(f"card-bar-{uuid4().hex[:6]}", "bar")

    admin_key = f"vh_{uuid4().hex}"
    _insert_key(admin_key, tenant_id="bar", is_admin=True)
    _use_key(client, admin_key)

    response = client.get(
        "/v1/knowledge/cards",
        params={"project_id": "iot", "tenant_id": "bar"},
    )
    assert response.status_code == 200
    assert any(c["id"] == bar_card for c in response.json()["cards"])


# ── skills.py — entity-tenant check on skill_id ───────────────────────


def _insert_skill(skill_id: str, project_id: str, tenant_id: str) -> str:
    """Insert a skill row. Returns the actual id (with uuid suffix)."""
    import json as _json
    suffix = uuid4().hex[:8]
    full_id = f"{skill_id}-{suffix}"
    with get_db_connection() as conn:
        conn.execute(
            "INSERT INTO skills (id, tenant_id, project_id, name, description, trigger_patterns_json, prompt_template, expected_behavior, test_cases_json, version, is_active) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 1, 1)",
            (
                full_id,
                tenant_id,
                project_id,
                f"skill-{full_id}",
                "desc",
                _json.dumps([]),
                "",
                "",
                _json.dumps([]),
            ),
        )
        conn.commit()
    return full_id


@pytest.mark.integration
def test_skill_get_blocks_cross_tenant(client: TestClient) -> None:
    skill_id = _insert_skill("skill-bar-1", "iot", "bar")
    foo_key = f"vh_{uuid4().hex}"
    _insert_key(foo_key, tenant_id="foo")
    _use_key(client, foo_key)

    response = client.get(f"/v1/projects/iot/skills/{skill_id}")
    assert response.status_code == 404


@pytest.mark.integration
def test_skill_patch_blocks_cross_tenant(client: TestClient) -> None:
    skill_id = _insert_skill("skill-bar-2", "iot", "bar")
    foo_key = f"vh_{uuid4().hex}"
    _insert_key(foo_key, tenant_id="foo")
    _use_key(client, foo_key)

    response = client.patch(
        f"/v1/projects/iot/skills/{skill_id}",
        json={"description": "hijack attempt"},
    )
    assert response.status_code == 404


@pytest.mark.integration
def test_skill_delete_blocks_cross_tenant(client: TestClient) -> None:
    skill_id = _insert_skill("skill-bar-3", "iot", "bar")
    foo_key = f"vh_{uuid4().hex}"
    _insert_key(foo_key, tenant_id="foo")
    _use_key(client, foo_key)

    response = client.delete(f"/v1/projects/iot/skills/{skill_id}")
    assert response.status_code == 404


@pytest.mark.integration
def test_skill_get_allows_admin(client: TestClient) -> None:
    skill_id = _insert_skill("skill-bar-4", "iot", "bar")
    admin_key = f"vh_{uuid4().hex}"
    _insert_key(admin_key, tenant_id="bar", is_admin=True)
    _use_key(client, admin_key)

    response = client.get(f"/v1/projects/iot/skills/{skill_id}")
    assert response.status_code == 200
    assert response.json()["id"] == skill_id
