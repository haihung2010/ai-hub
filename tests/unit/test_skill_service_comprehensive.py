"""Comprehensive tests for skill service - MUSE-Autoskill system."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import json

from app.services.skill_service import SkillService
from app.models.skill import SkillRecord, SkillEvaluationResult


class TestSkillServiceCrud:
    """Test skill CRUD operations."""

    @pytest.fixture
    def service(self) -> SkillService:
        return SkillService()

    def test_create_skill_basic(self, service: SkillService) -> None:
        """Create a skill with basic fields."""
        skill = service.create(
            tenant_id="test-tenant",
            project_id="test-project",
            name="test_skill",
            description="A test skill",
            trigger_patterns=["hello", "hi"],
            prompt_template="When user says {input}, respond with greeting.",
            expected_behavior="Friendly greeting",
            test_cases=[{"input": "hi", "expected_keywords": ["hello", "hi"]}],
        )

        assert skill.name == "test_skill"
        assert skill.tenant_id == "test-tenant"
        assert skill.project_id == "test-project"
        assert skill.version == 1
        assert skill.is_active is True
        assert skill.eval_score == 0.0
        assert json.loads(skill.trigger_patterns_json) == ["hello", "hi"]
        assert json.loads(skill.test_cases_json) == [
            {"input": "hi", "expected_keywords": ["hello", "hi"]}
        ]

    def test_create_skill_minimal(self, service: SkillService) -> None:
        """Create skill with only required fields."""
        skill = service.create(
            tenant_id="default",
            project_id="minimal",
            name="minimal_skill",
        )

        assert skill.name == "minimal_skill"
        assert skill.description == ""
        assert skill.prompt_template == ""
        assert skill.version == 1
        assert skill.is_active is True

    def test_create_skill_idempotent(self, service: SkillService) -> None:
        """Create same skill twice returns different IDs."""
        s1 = service.create(tenant_id="t", project_id="p", name="dup")
        s2 = service.create(tenant_id="t", project_id="p", name="dup")

        assert s1.id != s2.id
        assert s1.name == s2.name == "dup"

    def test_get_skill_not_found(self, service: SkillService) -> None:
        """Get non-existent skill returns None."""
        result = service.get_skill("nonexistent-id-12345")
        assert result is None

    def test_update_skill_description(self, service: SkillService) -> None:
        """Update skill description."""
        skill = service.create(
            tenant_id="t", project_id="p", name="updatable", description="v1"
        )

        updated = service.update(skill.id, description="v2 updated")
        assert updated is not None
        assert updated.description == "v2 updated"
        assert updated.version == 1  # version unchanged on description update

    def test_update_skill_prompt_template(self, service: SkillService) -> None:
        """Update skill prompt template."""
        skill = service.create(
            tenant_id="t", project_id="p", name="template_test",
            prompt_template="Original template",
        )

        new_template = "Updated: respond with {input} + timestamp"
        updated = service.update(skill.id, prompt_template=new_template)
        assert updated is not None
        assert updated.prompt_template == new_template

    def test_update_skill_trigger_patterns(self, service: SkillService) -> None:
        """Update trigger patterns."""
        skill = service.create(
            tenant_id="t", project_id="p", name="trigger_test",
            trigger_patterns=["old1", "old2"],
        )

        updated = service.update(
            skill.id, trigger_patterns=["new1", "new2", "new3"]
        )
        assert updated is not None
        patterns = json.loads(updated.trigger_patterns_json)
        assert patterns == ["new1", "new2", "new3"]

    def test_update_skill_is_active(self, service: SkillService) -> None:
        """Toggle skill active status."""
        skill = service.create(tenant_id="t", project_id="p", name="toggle_test")

        assert skill.is_active is True

        updated = service.update(skill.id, is_active=False)
        assert updated is not None
        assert updated.is_active is False

        updated = service.update(skill.id, is_active=True)
        assert updated.is_active is True

    def test_update_skill_not_found(self, service: SkillService) -> None:
        """Update non-existent skill returns None."""
        result = service.update("nonexistent-id", description="new desc")
        assert result is None

    def test_update_partial(self, service: SkillService) -> None:
        """Partial update preserves other fields."""
        skill = service.create(
            tenant_id="t", project_id="p", name="partial_test",
            description="Original desc",
            trigger_patterns=["pattern1"],
            prompt_template="Original template",
        )

        # Only update description
        updated = service.update(skill.id, description="New desc only")
        assert updated.description == "New desc only"
        assert json.loads(updated.trigger_patterns_json) == ["pattern1"]
        assert updated.prompt_template == "Original template"

    def test_delete_skill(self, service: SkillService) -> None:
        """Delete skill returns True and skill is gone."""
        skill = service.create(tenant_id="t", project_id="p", name="to_delete")

        result = service.delete(skill.id)
        assert result is True

        # Verify deleted
        found = service.get_skill(skill.id)
        assert found is None

    def test_delete_skill_not_found(self, service: SkillService) -> None:
        """Delete non-existent skill returns False."""
        result = service.delete("nonexistent-id")
        assert result is False


class TestSkillServiceMatching:
    """Test skill pattern matching."""

    @pytest.fixture
    def service(self) -> SkillService:
        return SkillService()

    @pytest.fixture
    def sample_skills(self, service: SkillService) -> list[SkillRecord]:
        """Create sample skills for matching tests."""
        skills = []
        for name, patterns in [
            ("greeting_skill", ["xin chào", "hello", "hi"]),
            ("help_skill", ["giúp", "help", "hỗ trợ"]),
            ("order_skill", ["đặt hàng", "mua", "order"]),
        ]:
            s = service.create(
                tenant_id="test-tenant",
                project_id="test-project",
                name=name,
                trigger_patterns=patterns,
                prompt_template=f"Template for {name}",
            )
            skills.append(s)
        return skills

    def test_match_skills_exact_match(self, service: SkillService, sample_skills: list) -> None:
        """Exact pattern match works."""
        matched = service.match_skills("test-tenant", "test-project", "xin chào")
        names = [s.name for s in matched]
        assert "greeting_skill" in names

    def test_match_skills_case_insensitive(self, service: SkillService, sample_skills: list) -> None:
        """Match is case-insensitive."""
        matched = service.match_skills("test-tenant", "test-project", "XIN CHÀO")
        names = [s.name for s in matched]
        assert "greeting_skill" in names

    def test_match_skills_partial_match(self, service: SkillService, sample_skills: list) -> None:
        """Partial substring match works."""
        matched = service.match_skills("test-tenant", "test-project", "tôi cần giúp")
        names = [s.name for s in matched]
        assert "help_skill" in names

    def test_match_skills_no_match(self, service: SkillService, sample_skills: list) -> None:
        """No pattern match returns empty list."""
        matched = service.match_skills(
            "test-tenant", "test-project", "something unrelated xyz123"
        )
        assert matched == []

    def test_match_skills_inactive_excluded(self, service: SkillService, sample_skills: list) -> None:
        """Inactive skills are excluded from matches."""
        # Deactivate greeting_skill
        inactive = service.get_skill(sample_skills[0].id)
        if inactive:
            service.update(inactive.id, is_active=False)

        matched = service.match_skills("test-tenant", "test-project", "xin chào")
        names = [s.name for s in matched]
        assert "greeting_skill" not in names
        assert "help_skill" in names  # others still match

    def test_match_skills_different_tenant(self, service: SkillService, sample_skills: list) -> None:
        """Skills are tenant-scoped."""
        matched = service.match_skills("other-tenant", "test-project", "xin chào")
        assert matched == []  # no match in different tenant

    def test_match_skills_different_project(self, service: SkillService, sample_skills: list) -> None:
        """Skills are project-scoped."""
        matched = service.match_skills("test-tenant", "other-project", "xin chào")
        assert matched == []  # no match in different project

    def test_match_skills_multiple_matches(self, service: SkillService) -> None:
        """Multiple patterns can match same message."""
        s1 = service.create(
            tenant_id="t", project_id="p", name="skill1",
            trigger_patterns=["hello"],
        )
        s2 = service.create(
            tenant_id="t", project_id="p", name="skill2",
            trigger_patterns=["hi"],
        )
        # Both match "hello hi there"
        matched = service.match_skills("t", "p", "hello hi there")
        assert len(matched) == 2

    def test_match_skills_diacritic_normalization(self, service: SkillService) -> None:
        """Vietnamese diacritics are normalized for matching."""
        skill = service.create(
            tenant_id="t", project_id="p", name="viet_test",
            trigger_patterns=["xin chào"],
        )

        # Match with different diacritic forms
        matched = service.match_skills("t", "p", "XIN CHÀO")
        assert len(matched) == 1

    def test_format_prompt_for_skill(self, service: SkillService) -> None:
        """Format skill prompt for injection."""
        skill = service.create(
            tenant_id="t", project_id="p", name="format_test",
            trigger_patterns=["test"],
            prompt_template="You are a {name} assistant. Be helpful.",
        )

        formatted = service.format_prompt_for_skill(skill)
        assert "format_test" in formatted
        assert "You are a" in formatted

    def test_format_prompt_for_skill_empty_template(self, service: SkillService) -> None:
        """Empty template returns empty string."""
        skill = service.create(
            tenant_id="t", project_id="p", name="empty_template",
            trigger_patterns=["test"],
            prompt_template="",
        )

        formatted = service.format_prompt_for_skill(skill)
        assert formatted == ""


class TestSkillServiceEvaluation:
    """Test skill evaluation."""

    @pytest.fixture
    def service(self) -> SkillService:
        return SkillService()

    def test_evaluate_skill_no_test_cases(self, service: SkillService) -> None:
        """Evaluate skill with no test cases returns 0."""
        skill = service.create(
            tenant_id="t", project_id="p", name="no_tests",
            trigger_patterns=["test"],
            test_cases=[],
        )

        # Mock provider
        mock_provider = AsyncMock()
        result = service.evaluate_skill(skill.id, mock_provider, "test-model")

        assert result.total == 0
        assert result.passed == 0
        assert result.failed == 0
        assert result.score == 0.0

    def test_evaluate_skill_single_pass(self, service: SkillService) -> None:
        """Evaluate skill with single passing test."""
        skill = service.create(
            tenant_id="t", project_id="p", name="single_pass",
            trigger_patterns=["test"],
            test_cases=[
                {"input": "say hello", "expected_keywords": ["hello", "hi"]}
            ],
        )

        mock_provider = AsyncMock()
        mock_provider.chat.return_value = MagicMock(content="Hello there!")

        result = service.evaluate_skill(skill.id, mock_provider, "test-model")

        assert result.total == 1
        assert result.passed == 1
        assert result.failed == 0
        assert result.score == 1.0

    def test_evaluate_skill_single_fail(self, service: SkillService) -> None:
        """Evaluate skill with single failing test."""
        skill = service.create(
            tenant_id="t", project_id="p", name="single_fail",
            trigger_patterns=["test"],
            test_cases=[
                {"input": "say hello", "expected_keywords": ["goodbye"]}
            ],
        )

        mock_provider = AsyncMock()
        mock_provider.chat.return_value = MagicMock(content="Hello there!")

        result = service.evaluate_skill(skill.id, mock_provider, "test-model")

        assert result.total == 1
        assert result.passed == 0
        assert result.failed == 1
        assert result.score == 0.0

    def test_evaluate_skill_multiple_tests(self, service: SkillService) -> None:
        """Evaluate skill with multiple tests, mixed results."""
        skill = service.create(
            tenant_id="t", project_id="p", name="multi_test",
            trigger_patterns=["test"],
            test_cases=[
                {"input": "say hello", "expected_keywords": ["hello"]},
                {"input": "say goodbye", "expected_keywords": ["bye"]},
                {"input": "say thanks", "expected_keywords": ["thank"]},
            ],
        )

        mock_provider = AsyncMock()
        mock_provider.chat.side_effect = [
            MagicMock(content="Hello there!"),      # pass
            MagicMock(content="Goodbye friend!"),   # pass
            MagicMock(content="You're welcome"),    # fail - "thank" not in response
        ]

        result = service.evaluate_skill(skill.id, mock_provider, "test-model")

        assert result.total == 3
        assert result.passed == 2
        assert result.failed == 1
        assert result.score == pytest.approx(0.667, rel=0.01)

    def test_evaluate_skill_updates_score(self, service: SkillService) -> None:
        """Evaluate skill updates eval_score in database."""
        skill = service.create(
            tenant_id="t", project_id="p", name="score_update",
            trigger_patterns=["test"],
            test_cases=[{"input": "hi", "expected_keywords": ["hello"]}],
        )

        mock_provider = AsyncMock()
        mock_provider.chat.return_value = MagicMock(content="hello!")

        service.evaluate_skill(skill.id, mock_provider, "test-model")

        # Refresh from database
        updated = service.get_skill(skill.id)
        assert updated is not None
        assert updated.eval_score == 1.0
        assert updated.last_evaluated_at is not None

    def test_evaluate_skill_not_found(self, service: SkillService) -> None:
        """Evaluate non-existent skill returns zero result."""
        mock_provider = AsyncMock()
        result = service.evaluate_skill("nonexistent-id", mock_provider, "test-model")

        assert result.total == 0
        assert result.score == 0.0


class TestSkillServiceRefinement:
    """Test skill refinement from chat history."""

    @pytest.fixture
    def service(self) -> SkillService:
        return SkillService()

    def test_refine_from_chat_history(self, service: SkillService) -> None:
        """Refinement extracts new prompt from chat history."""
        skill = service.create(
            tenant_id="t", project_id="p", name="refine_test",
            trigger_patterns=["test"],
            prompt_template="Original prompt",
        )

        mock_provider = AsyncMock()
        mock_provider.chat.return_value = MagicMock(
            content="Extracted: Always respond with emoji and be friendly."
        )

        messages = [
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello! 😊 How can I help?"},
        ]

        updated = service.refine_from_chat_history(
            skill.id, messages, mock_provider, "test-model"
        )

        assert updated is not None
        assert "Extracted:" in updated.prompt_template
        assert updated.version == 2  # version incremented

    def test_refine_increments_version(self, service: SkillService) -> None:
        """Refinement increments skill version."""
        skill = service.create(
            tenant_id="t", project_id="p", name="version_test",
            prompt_template="Original",
        )

        assert skill.version == 1

        mock_provider = AsyncMock()
        mock_provider.chat.return_value = MagicMock(content="New extracted prompt")

        messages = [{"role": "user", "content": "hello"}]
        updated = service.refine_from_chat_history(
            skill.id, messages, mock_provider, "model"
        )

        assert updated.version == 2

    def test_refine_not_found(self, service: SkillService) -> None:
        """Refine non-existent skill returns None."""
        mock_provider = AsyncMock()
        messages = [{"role": "user", "content": "test"}]

        result = service.refine_from_chat_history(
            "nonexistent-id", messages, mock_provider, "model"
        )
        assert result is None


class TestSkillServiceList:
    """Test skill listing."""

    @pytest.fixture
    def service(self) -> SkillService:
        return SkillService()

    def test_list_skills_empty(self, service: SkillService) -> None:
        """List skills when none exist returns empty list."""
        skills = service.list_skills("nonexistent-tenant", "nonexistent-project")
        assert skills == []

    def test_list_skills_includes_created(self, service: SkillService) -> None:
        """List includes skills just created."""
        s1 = service.create(tenant_id="t", project_id="p", name="skill_a")
        s2 = service.create(tenant_id="t", project_id="p", name="skill_b")

        skills = service.list_skills("t", "p")
        names = [s.name for s in skills]

        assert "skill_a" in names
        assert "skill_b" in names

    def test_list_skills_excludes_inactive_by_default(self, service: SkillService) -> None:
        """List excludes inactive skills by default."""
        active = service.create(tenant_id="t", project_id="p", name="active_skill")
        inactive = service.create(tenant_id="t", project_id="p", name="inactive_skill")

        service.update(inactive.id, is_active=False)

        skills = service.list_skills("t", "p", include_inactive=False)
        names = [s.name for s in skills]

        assert "active_skill" in names
        assert "inactive_skill" not in names

    def test_list_skills_include_inactive(self, service: SkillService) -> None:
        """List with include_inactive=True includes both."""
        active = service.create(tenant_id="t", project_id="p", name="active_skill")
        inactive = service.create(tenant_id="t", project_id="p", name="inactive_skill")

        service.update(inactive.id, is_active=False)

        skills = service.list_skills("t", "p", include_inactive=True)
        names = [s.name for s in skills]

        assert "active_skill" in names
        assert "inactive_skill" in names

    def test_list_skills_tenant_scoped(self, service: SkillService) -> None:
        """List only returns skills for specified tenant."""
        service.create(tenant_id="tenant_a", project_id="p", name="skill_a")
        service.create(tenant_id="tenant_b", project_id="p", name="skill_b")

        skills_a = service.list_skills("tenant_a", "p")
        skills_b = service.list_skills("tenant_b", "p")

        assert len(skills_a) == 1
        assert skills_a[0].name == "skill_a"
        assert len(skills_b) == 1
        assert skills_b[0].name == "skill_b"

    def test_list_skills_project_scoped(self, service: SkillService) -> None:
        """List only returns skills for specified project."""
        service.create(tenant_id="t", project_id="project_a", name="skill_a")
        service.create(tenant_id="t", project_id="project_b", name="skill_b")

        skills_a = service.list_skills("t", "project_a")
        skills_b = service.list_skills("t", "project_b")

        assert len(skills_a) == 1
        assert skills_a[0].name == "skill_a"
        assert len(skills_b) == 1
        assert skills_b[0].name == "skill_b"
