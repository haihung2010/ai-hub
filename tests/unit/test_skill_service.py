"""SkillService and skill model unit tests."""

import pytest

from app.models.skill import SkillRecord, SkillEvaluationResult
from app.services.skill_service import SkillService


@pytest.mark.unit
def test_skill_record_from_row() -> None:
    import datetime
    row = (
        "skill123", "tenant1", "proj1", "order_check",
        "check order status",
        '[".*order.*status.*"]',
        "When customer asks about order status, check the order system first.",
        "Always confirm order ID before sharing status.",
        '[{"input": "where is my order", "expected_keywords": ["order id", "tracking"]}]',
        2, True,
        datetime.datetime(2026, 1, 1, 12, 0, 0),
        datetime.datetime(2026, 1, 2, 12, 0, 0),
        datetime.datetime(2026, 1, 3, 12, 0, 0),
        0.85,
    )
    skill = SkillRecord.from_row(row)
    assert skill.id == "skill123"
    assert skill.name == "order_check"
    assert skill.tenant_id == "tenant1"
    assert skill.project_id == "proj1"
    assert skill.version == 2
    assert skill.is_active is True
    assert skill.eval_score == 0.85
    assert skill.prompt_template == "When customer asks about order status, check the order system first."


@pytest.mark.unit
def test_skill_evaluation_result() -> None:
    result = SkillEvaluationResult(
        skill_id="s1", passed=3, failed=1, total=4,
        score=0.75, details=["PASS x3", "FAIL x1"],
    )
    assert result.score == 0.75
    assert result.total == 4
    assert len(result.details) == 2


@pytest.mark.unit
def test_format_prompt_for_skill() -> None:
    from app.models.skill import SkillRecord
    service = SkillService()
    skill = SkillRecord(
        id="s1", tenant_id="t1", project_id="p1", name="order_status",
        prompt_template="Always check order ID first.",
    )
    formatted = service.format_prompt_for_skill(skill)
    assert "order_status" in formatted
    assert "Always check order ID first" in formatted
    assert "### SKILL:" in formatted


@pytest.mark.unit
def test_format_prompt_empty_template() -> None:
    from app.models.skill import SkillRecord
    service = SkillService()
    skill = SkillRecord(id="s1", tenant_id="t1", project_id="p1", name="empty_skill")
    assert service.format_prompt_for_skill(skill) == ""


@pytest.mark.unit
def test_strip_diacritics() -> None:
    service = SkillService()
    # đặt hàng → dat hang (d stays, ặ → a)
    result = service._strip_diacritics("Tôi muốn đặt hàng")
    assert result == "toi muon at hang"
    # Basic ASCII also works
    assert service._strip_diacritics("hello world") == "hello world"
