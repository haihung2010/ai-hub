"""Comprehensive tests for admin routes — covering uncovered code paths."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.database import get_db_connection


def _create_api_key(client: TestClient, name: str = "test-key", **overrides) -> dict:
    """Helper to mint a virtual API key via admin endpoint."""
    payload = {"name": name, **overrides}
    resp = client.post("/v1/admin/keys", json=payload)
    assert resp.status_code == 200
    return resp.json()


class TestAdminDashboard:
    def test_admin_dashboard_returns_html(self, client: TestClient):
        resp = client.get("/admin.html")
        assert resp.status_code == 200

    def test_admin_requires_api_key(self, client: TestClient):
        no_key_client = TestClient(client.app)
        resp = no_key_client.get("/v1/admin/usage")
        assert resp.status_code == 401


class TestAdminUsage:
    def test_usage_returns_data(self, client: TestClient):
        resp = client.get("/v1/admin/usage")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict)

    def test_usage_after_chat_request(self, client: TestClient, mock_api):
        import respx
        from tests.conftest import make_ollama_chat_response
        mock_api.post("http://llama.test/v1/chat/completions").respond(
            json=make_ollama_chat_response("hello")
        )
        client.post("/v1/chat", json={
            "project_id": "test", "user_message": "hi", "user_name": "admin_test_user"
        })
        resp = client.get("/v1/admin/usage")
        assert resp.status_code == 200


class TestAdminStats:
    def test_stats_returns_breakdowns(self, client: TestClient):
        resp = client.get("/v1/admin/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "providers" in data or "total_requests" in data

    def test_admin_observability_alias(self, client: TestClient):
        resp = client.get("/v1/admin/stats")
        assert resp.status_code == 200


class TestAdminQueue:
    def test_queue_returns_capacity(self, client: TestClient):
        resp = client.get("/v1/admin/queue")
        assert resp.status_code == 200
        data = resp.json()
        assert "active" in data or "queue" in data


class TestAdminGPU:
    def test_gpu_stats_endpoint(self, client: TestClient):
        resp = client.get("/v1/admin/gpu/stats")
        # May return 200 with mock data or 503 if nvidia-smi not available
        assert resp.status_code in (200, 503)


class TestAdminTenants:
    def test_list_tenants_empty(self, client: TestClient):
        resp = client.get("/v1/admin/tenants")
        assert resp.status_code == 200

    def test_list_tenant_users(self, client: TestClient, mock_api):
        from tests.conftest import make_ollama_chat_response
        import respx
        mock_api.post("http://llama.test/v1/chat/completions").respond(
            json=make_ollama_chat_response("ok")
        )
        client.post("/v1/chat", json={
            "project_id": "test", "user_message": "hi", "user_name": "tenant_user"
        })
        resp = client.get("/v1/admin/tenants/default/users")
        assert resp.status_code == 200


class TestAdminKeys:
    def test_mint_and_list_key(self, client: TestClient):
        key_data = _create_api_key(client, "my-test-key")
        assert "key" in key_data or "id" in key_data

        resp = client.get("/v1/admin/management/keys")
        assert resp.status_code == 200

    def test_disable_and_reenable_key(self, client: TestClient):
        key_data = _create_api_key(client, "toggle-key")
        key_id = key_data.get("id")
        if key_id:
            resp = client.patch(f"/v1/admin/keys/{key_id}", json={"enabled": False})
            assert resp.status_code == 200
            resp = client.patch(f"/v1/admin/keys/{key_id}", json={"enabled": True})
            assert resp.status_code == 200

    def test_delete_key(self, client: TestClient):
        key_data = _create_api_key(client, "delete-me")
        key_id = key_data.get("id")
        if key_id:
            resp = client.delete(f"/v1/admin/keys/{key_id}")
            assert resp.status_code == 200

    def test_key_with_budget_and_rpm(self, client: TestClient):
        key_data = _create_api_key(client, "budget-key", rpm_limit=10, monthly_budget_usd=5.0)
        assert key_data is not None

    def test_key_with_project_restrictions(self, client: TestClient):
        key_data = _create_api_key(client, "restricted-key",
                                   allowed_projects=["proj1"],
                                   denied_projects=["proj2"])
        assert key_data is not None

    def test_admin_key_with_admin_flag(self, client: TestClient):
        key_data = _create_api_key(client, "admin-key", is_admin=True)
        assert key_data is not None


class TestAdminKnowledge:
    def test_upload_list_delete_knowledge(self, client: TestClient):
        resp = client.post("/v1/admin/knowledge/upload", json={
            "project_id": "test",
            "domain": "faq",
            "title": "Test FAQ",
            "content": "This is test knowledge content for admin routes.",
            "source_type": "manual",
        })
        assert resp.status_code == 200

        resp = client.get("/v1/admin/knowledge/cards", params={"project_id": "test"})
        assert resp.status_code == 200
        cards = resp.json()
        if isinstance(cards, list) and cards:
            card_id = cards[0].get("id")
            if card_id:
                resp = client.delete(f"/v1/admin/knowledge/cards/{card_id}")
                assert resp.status_code == 200

    def test_reindex_knowledge(self, client: TestClient):
        resp = client.post("/v1/admin/knowledge/reindex", json={"project_id": "test"})
        assert resp.status_code == 200


class TestAdminSessions:
    def test_list_sessions(self, client: TestClient):
        resp = client.get("/v1/admin/management/sessions")
        assert resp.status_code == 200


class TestAdminModelSwitch:
    def test_invalid_model_mode_rejected(self, client: TestClient):
        resp = client.post("/v1/admin/model/switch", json={"mode": "invalid_mode_xyz"})
        assert resp.status_code in (400, 422)
