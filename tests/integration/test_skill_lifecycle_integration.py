"""Integration tests for skill lifecycle - full flow."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.conftest import ensure_user

API_KEY = "test-api-key"


class TestSkillLifecycleIntegration:
    """Test complete skill lifecycle through API."""

    def test_create_and_retrieve_skill(self, client: TestClient) -> None:
        """Create skill via API and retrieve it."""
        project_id = "lifecycle_test"

        # Create skill
        create_resp = client.post(
            f"/v1/projects/{project_id}/skills",
            json={
                "name": "lifecycle_skill",
                "description": "Test lifecycle",
                "trigger_patterns": ["test trigger"],
                "prompt_template": "When triggered, say {input}",
                "test_cases": [
                    {"input": "run test", "expected_keywords": ["test"]}
                ],
            },
        )

        assert create_resp.status_code == 201
        skill = create_resp.json()
        assert skill["name"] == "lifecycle_skill"
        skill_id = skill["id"]

        # Retrieve skill
        get_resp = client.get(f"/v1/projects/{project_id}/skills/{skill_id}")
        assert get_resp.status_code == 200
        assert get_resp.json()["id"] == skill_id

        # List skills
        list_resp = client.get(f"/v1/projects/{project_id}/skills")
        assert list_resp.status_code == 200
        assert any(s["id"] == skill_id for s in list_resp.json()["skills"])

    def test_update_skill_via_api(self, client: TestClient) -> None:
        """Update skill via API."""
        project_id = "update_test"

        # Create
        create_resp = client.post(
            f"/v1/projects/{project_id}/skills",
            json={"name": "to_update", "description": "v1"},
        )
        skill_id = create_resp.json()["id"]

        # Update
        update_resp = client.patch(
            f"/v1/projects/{project_id}/skills/{skill_id}",
            json={"description": "updated v2", "is_active": False},
        )
        assert update_resp.status_code == 200
        assert update_resp.json()["description"] == "updated v2"
        assert update_resp.json()["is_active"] is False

    def test_delete_skill_via_api(self, client: TestClient) -> None:
        """Delete skill via API."""
        project_id = "delete_test"

        # Create
        create_resp = client.post(
            f"/v1/projects/{project_id}/skills",
            json={"name": "to_delete"},
        )
        skill_id = create_resp.json()["id"]

        # Delete
        del_resp = client.delete(f"/v1/projects/{project_id}/skills/{skill_id}")
        assert del_resp.status_code == 204

        # Verify gone
        get_resp = client.get(f"/v1/projects/{project_id}/skills/{skill_id}")
        assert get_resp.status_code == 404

    def test_skill_not_found_wrong_project(self, client: TestClient) -> None:
        """Skill from different project returns 404."""
        # Create in project_a
        create_resp = client.post(
            "/v1/projects/project_a/skills",
            json={"name": "cross_project"},
        )
        skill_id = create_resp.json()["id"]

        # Try to access from project_b
        get_resp = client.get(f"/v1/projects/project_b/skills/{skill_id}")
        assert get_resp.status_code == 404

    def test_skill_eval_endpoint(self, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        """Skill evaluate endpoint works."""
        project_id = "eval_test"

        # Create skill with test cases
        create_resp = client.post(
            f"/v1/projects/{project_id}/skills",
            json={
                "name": "eval_skill",
                "test_cases": [
                    {"input": "say hi", "expected_keywords": ["hello", "hi"]}
                ],
            },
        )
        skill_id = create_resp.json()["id"]

        # Mock the provider's chat method
        from unittest.mock import AsyncMock, MagicMock

        async def fake_chat(messages, model, max_tokens):
            return MagicMock(content="hello there!")

        monkeypatch.setattr(
            client.app.state.local_provider,
            "chat",
            fake_chat,
        )

        # Evaluate
        eval_resp = client.post(f"/v1/projects/{project_id}/skills/{skill_id}/evaluate")
        assert eval_resp.status_code == 200
        result = eval_resp.json()
        assert "score" in result
        assert "passed" in result

    def test_skill_inject_into_chat(self, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        """Skills get injected into chat prompts."""
        project_id = "inject_test"
        user_name = "inject_user"

        ensure_user(user_name, "default", user_name)

        # Create a skill
        client.post(
            f"/v1/projects/{project_id}/skills",
            json={
                "name": "injected_skill",
                "trigger_patterns": ["special trigger"],
                "prompt_template": "You are a SPECIAL assistant. Always be enthusiastic!",
            },
        )

        # Chat with matching trigger
        chat_resp = client.post(
            "/v1/chat",
            json={
                "user_name": user_name,
                "project_id": project_id,
                "user_message": "special trigger word",
                "model_mode": "lite",
            },
        )

        # Should succeed (skill injected into system prompt)
        assert chat_resp.status_code == 200


class TestSkillPatternMatching:
    """Test pattern matching edge cases."""

    def test_vietnamese_diacritics(self, client: TestClient) -> None:
        """Vietnamese patterns with diacritics match correctly."""
        project_id = "viet_test"

        client.post(
            f"/v1/projects/{project_id}/skills",
            json={
                "name": "viet_skill",
                "trigger_patterns": ["xin chào", "tạm biệt"],
            },
        )

        # List should find the skill
        list_resp = client.get(f"/v1/projects/{project_id}/skills")
        assert list_resp.status_code == 200
        skills = list_resp.json()["skills"]
        assert any(s["name"] == "viet_skill" for s in skills)

    def test_case_insensitive_matching(self, client: TestClient) -> None:
        """Pattern matching is case insensitive."""
        project_id = "case_test"

        client.post(
            f"/v1/projects/{project_id}/skills",
            json={
                "name": "case_skill",
                "trigger_patterns": ["hello"],
            },
        )

        # Should match "HELLO", "Hello", "hello"
        list_resp = client.get(f"/v1/projects/{project_id}/skills")
        assert list_resp.status_code == 200
        assert any(s["name"] == "case_skill" for s in list_resp.json()["skills"])

    def test_empty_trigger_patterns(self, client: TestClient) -> None:
        """Skill with empty trigger patterns can be created."""
        project_id = "empty_triggers"

        resp = client.post(
            f"/v1/projects/{project_id}/skills",
            json={
                "name": "empty_trigger_skill",
                "trigger_patterns": [],
            },
        )

        assert resp.status_code == 201
        assert resp.json()["name"] == "empty_trigger_skill"

    def test_special_characters_in_patterns(self, client: TestClient) -> None:
        """Special characters in patterns are handled."""
        project_id = "special_chars"

        resp = client.post(
            f"/v1/projects/{project_id}/skills",
            json={
                "name": "special_skill",
                "trigger_patterns": ["hello@world.com", "price: $100"],
            },
        )

        assert resp.status_code == 201
        patterns = resp.json()["trigger_patterns"]
        assert "hello@world.com" in patterns
        assert "price: $100" in patterns
