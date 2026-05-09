from __future__ import annotations

import json

import pytest

from app.core.database import get_db_connection
from app.models.chat import ChatRequest, Message
from app.models.failure_risk import RiskPolicyDecision
from app.services.failure_risk_service import FailureRiskService
from app.services.history_service import HistoryService


@pytest.mark.unit
def test_failure_risk_detects_current_data_without_search() -> None:
    service = FailureRiskService()
    req = ChatRequest(project_id="test", user_message="Gia vang hom nay the nao?")

    risk = service.evaluate(
        req=req,
        messages=[Message(role="user", content=req.user_message)],
        summary=None,
        memory_bundle=None,
        pinned_memory_block=None,
        provider_name="local",
        model="lite",
        history_count=0,
        history_cap=20,
        search_injected=False,
    )

    assert risk.score >= 0.25
    assert risk.level in {"low", "medium", "high"}
    assert "tool_needed" in risk.risk_types
    assert risk.recommended_action == "enable_search"


@pytest.mark.unit
def test_failure_risk_policy_stays_passive_by_default() -> None:
    service = FailureRiskService()
    req = ChatRequest(project_id="test", user_message="Analyze and compare this strategy")
    risk = service.evaluate(
        req=req,
        messages=[Message(role="user", content=req.user_message)],
        summary=None,
        memory_bundle=None,
        pinned_memory_block=None,
        provider_name="local",
        model="lite",
        history_count=20,
        history_cap=20,
        search_injected=True,
    )

    decision = service.decide(risk, log_only=True, enable_actions=True, enable_search_action=True)

    assert risk.level in {"medium", "high"}
    assert decision.applied is False
    assert decision.action == risk.recommended_action


@pytest.mark.unit
def test_failure_risk_record_persists_json_payload() -> None:
    service = FailureRiskService()
    risk = service.evaluate(
        req=ChatRequest(project_id="test", user_message="latest stock price today"),
        messages=[Message(role="user", content="latest stock price today")],
        summary=None,
        memory_bundle=None,
        pinned_memory_block=None,
        provider_name="local",
        model="lite",
        history_count=0,
        history_cap=20,
        search_injected=False,
    )

    session_id = HistoryService().create_session("test", tenant_id="default")
    record_id = service.record(
        tenant_id="default",
        project_id="test",
        user_id=None,
        session_id=session_id,
        risk=risk,
        decision=RiskPolicyDecision(action="enable_search", applied=False),
        route_before="local",
        route_after="local",
        model_before="lite",
        model_after="lite",
    )

    with get_db_connection() as conn:
        row = conn.execute("SELECT * FROM failure_risk_events WHERE id = %s", (record_id,)).fetchone()

    assert row is not None
    assert row["risk_level"] == risk.level
    assert json.loads(row["risk_types_json"]) == risk.risk_types
    assert row["recommended_action"] == "enable_search"
