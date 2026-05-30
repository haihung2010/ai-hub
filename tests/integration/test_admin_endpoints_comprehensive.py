"""Comprehensive admin endpoint tests."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.conftest import ensure_user


class TestAdminEndpointsAuth:
    """Admin endpoints require authentication."""

    def test_all_admin_endpoints_require_api_key(self, client: TestClient) -> None:
        """All admin endpoints reject requests without API key."""
        endpoints = [
            ("GET", "/v1/admin/stats"),
            ("GET", "/v1/admin/usage"),
            ("GET", "/v1/admin/queue"),
            ("GET", "/v1/admin/gpu/stats"),
            ("GET", "/v1/admin/health/providers"),
            ("GET", "/v1/admin/tenants"),
            ("GET", "/v1/admin/management/keys"),
            ("GET", "/v1/admin/management/sessions"),
        ]

        client.headers.clear()  # Remove API key

        for method, path in endpoints:
            if method == "GET":
                resp = client.get(path)
            elif method == "POST":
                resp = client.post(path, json={})

            assert resp.status_code == 401, f"{method} {path} should require auth"

    def test_invalid_api_key_rejected(self, client: TestClient) -> None:
        """Invalid API key returns 401."""
        client.headers["X-API-KEY"] = "invalid-key-12345"

        resp = client.get("/v1/admin/stats")
        assert resp.status_code == 401


class TestAdminStatsEndpoint:
    """Admin stats endpoint tests."""

    def test_stats_returns_by_provider(self, client: TestClient) -> None:
        """Stats breakdown by provider."""
        resp = client.get("/v1/admin/stats")
        assert resp.status_code == 200

        data = resp.json()
        assert "by_provider" in data
        assert "by_model" in data
        assert "by_route" in data
        assert "latency" in data

    def test_stats_returns_latency_breakdown(self, client: TestClient) -> None:
        """Stats include latency percentiles."""
        resp = client.get("/v1/admin/stats")
        assert resp.status_code == 200

        data = resp.json()
        latency = data["latency"]
        assert "p50" in latency
        assert "p95" in latency
        assert "p99" in latency
        assert "avg" in latency

    def test_stats_returns_queue_wait_metrics(self, client: TestClient) -> None:
        """Stats include queue wait times."""
        resp = client.get("/v1/admin/stats")
        assert resp.status_code == 200

        data = resp.json()
        assert "queue_wait" in data
        assert data["queue_wait"]["requests"] >= 0

    def test_observability_alias(self, client: TestClient) -> None:
        """Observability is an alias for stats."""
        stats_resp = client.get("/v1/admin/stats")
        obs_resp = client.get("/v1/admin/observability")

        assert stats_resp.status_code == 200
        assert obs_resp.status_code == 200

        # Same structure expected
        assert set(stats_resp.json().keys()) == set(obs_resp.json().keys())


class TestAdminQueueEndpoint:
    """Admin queue endpoint tests."""

    def test_queue_returns_capacity(self, client: TestClient) -> None:
        """Queue shows capacity."""
        resp = client.get("/v1/admin/queue")
        assert resp.status_code == 200

        data = resp.json()
        assert "capacity" in data
        assert data["capacity"] > 0

    def test_queue_returns_active_count(self, client: TestClient) -> None:
        """Queue shows active requests."""
        resp = client.get("/v1/admin/queue")
        assert resp.status_code == 200

        data = resp.json()
        assert "active" in data
        assert "waiting" in data


class TestAdminUsageEndpoint:
    """Admin usage endpoint tests."""

    def test_usage_returns_system_info(self, client: TestClient) -> None:
        """Usage includes system metrics."""
        resp = client.get("/v1/admin/usage")
        assert resp.status_code == 200

        data = resp.json()
        assert "process" in data
        assert "cpu" in data
        assert "memory" in data
        assert "disk" in data

    def test_usage_returns_process_info(self, client: TestClient) -> None:
        """Usage includes process info."""
        resp = client.get("/v1/admin/usage")
        assert resp.status_code == 200

        data = resp.json()
        assert data["process"]["pid"] > 0
        assert data["process"]["rss_mb"] > 0

    def test_usage_returns_memory_info(self, client: TestClient) -> None:
        """Usage includes memory info."""
        resp = client.get("/v1/admin/usage")
        assert resp.status_code == 200

        data = resp.json()
        mem = data["memory"]
        assert "total_mb" in mem
        assert "available_mb" in mem


class TestAdminTenantsEndpoints:
    """Admin tenants endpoint tests."""

    def test_list_tenants(self, client: TestClient) -> None:
        """List all tenants."""
        resp = client.get("/v1/admin/tenants")
        assert resp.status_code == 200

        data = resp.json()
        assert "tenants" in data
        assert isinstance(data["tenants"], list)

    def test_get_tenant_users(self, client: TestClient) -> None:
        """Get users for a specific tenant."""
        tenant_id = "test_tenant"
        ensure_user("testuser", tenant_id, "testuser")

        resp = client.get(f"/v1/admin/tenants/{tenant_id}/users")
        assert resp.status_code == 200

        data = resp.json()
        assert "users" in data


class TestAdminKeysEndpoints:
    """Admin API keys endpoint tests."""

    def test_list_keys(self, client: TestClient) -> None:
        """List all API keys."""
        resp = client.get("/v1/admin/management/keys")
        assert resp.status_code == 200

        data = resp.json()
        assert "keys" in data

    def test_create_key(self, client: TestClient) -> None:
        """Create new API key."""
        resp = client.post(
            "/v1/admin/keys",
            json={
                "name": "test_key_comprehensive",
                "rpm": 100,
                "budget_usd": 10.0,
            },
        )
        assert resp.status_code == 201

        data = resp.json()
        assert "key" in data
        assert data["key"]["name"] == "test_key_comprehensive"

    def test_disable_key(self, client: TestClient) -> None:
        """Disable an API key."""
        # Create a key
        create_resp = client.post(
            "/v1/admin/keys",
            json={"name": "to_disable", "rpm": 100},
        )
        key_id = create_resp.json()["key"]["id"]

        # Disable it
        disable_resp = client.delete(f"/v1/admin/keys/{key_id}")
        assert disable_resp.status_code == 200

    def test_reenable_key(self, client: TestClient) -> None:
        """Re-enable a disabled key."""
        # Create and disable
        create_resp = client.post(
            "/v1/admin/keys",
            json={"name": "to_reenable", "rpm": 100},
        )
        key_id = create_resp.json()["key"]["id"]
        client.delete(f"/v1/admin/keys/{key_id}")

        # Re-enable
        enable_resp = client.patch(
            f"/v1/admin/keys/{key_id}",
            json={"is_active": True},
        )
        assert enable_resp.status_code == 200
        assert enable_resp.json()["is_active"] is True

    def test_update_key_rpm(self, client: TestClient) -> None:
        """Update key rate limit."""
        create_resp = client.post(
            "/v1/admin/keys",
            json={"name": "update_rpm", "rpm": 50},
        )
        key_id = create_resp.json()["key"]["id"]

        update_resp = client.patch(
            f"/v1/admin/keys/{key_id}",
            json={"rpm": 200},
        )
        assert update_resp.status_code == 200
        assert update_resp.json()["rpm"] == 200


class TestAdminSessionsEndpoint:
    """Admin sessions endpoint tests."""

    def test_list_sessions(self, client: TestClient) -> None:
        """List active sessions."""
        resp = client.get("/v1/admin/management/sessions")
        assert resp.status_code == 200

        data = resp.json()
        assert "sessions" in data


class TestAdminKnowledgeEndpoints:
    """Admin knowledge management tests."""

    def test_list_knowledge_cards(self, client: TestClient) -> None:
        """List all knowledge cards."""
        resp = client.get("/v1/knowledge/cards?tenant_id=default&project_id=admin_test")
        assert resp.status_code == 200

        data = resp.json()
        assert "cards" in data

    def test_delete_knowledge_card(self, client: TestClient) -> None:
        """Delete a knowledge card."""
        # Create a card first
        create_resp = client.post(
            "/v1/knowledge/cards",
            json={
                "tenant_id": "default",
                "project_id": "admin_test",
                "title": "To Delete",
                "content": "Delete me",
                "knowledge_domain": "test",
            },
        )
        card_id = create_resp.json()["card"]["id"]

        # Delete it
        del_resp = client.delete(f"/v1/knowledge/cards/{card_id}")
        assert del_resp.status_code == 200


class TestAdminProviderHealth:
    """Admin provider health tests."""

    def test_provider_health_lists_all(self, client: TestClient) -> None:
        """Provider health lists all configured providers."""
        resp = client.get("/v1/admin/health/providers")
        assert resp.status_code == 200

        data = resp.json()
        assert "providers" in data

        # Should have at least llama_cpp
        provider_names = [p["name"] for p in data["providers"]]
        assert "llama_cpp" in provider_names

    def test_model_switch_rejects_invalid(self, client: TestClient) -> None:
        """Model switch rejects invalid mode."""
        resp = client.post("/v1/admin/model/switch", json={"mode": "invalid"})
        assert resp.status_code == 422
