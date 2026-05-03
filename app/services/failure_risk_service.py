"""Failure-risk scoring and persistence for chat requests."""

from __future__ import annotations

import json
import logging
import re
from uuid import uuid4

from app.core.database import get_db_connection
from app.models.chat import ChatRequest, Message
from app.models.failure_risk import FailureRiskResult, RiskPolicyDecision
from app.models.memory import RetrievedMemoryBundle

logger = logging.getLogger(__name__)

_CURRENT_DATA_RE = re.compile(
    r"\b(today|latest|current|now|news|price|rate|weather|stock|crypto|btc|gold|"
    r"hom nay|hien tai|bay gio|moi nhat|tin|gia|thoi tiet|chung khoan|vang)\b",
    re.IGNORECASE,
)
_MULTI_STEP_RE = re.compile(
    r"\b(analy[sz]e|compare|plan|strategy|debug|optimi[sz]e|multi[- ]%sstep|"
    r"phan tich|so sanh|chien luoc|toi uu|ke hoach|nhieu buoc)\b",
    re.IGNORECASE,
)
_MEMORY_RECALL_RE = re.compile(
    r"\b(remember|recall|what did i|last time|truoc do|luc nay|nho|da noi gi|tom tat lai)\b",
    re.IGNORECASE,
)


class FailureRiskService:
    def __init__(
        self,
        *,
        high_threshold: float = 0.6,
        medium_threshold: float = 0.3,
        history_pressure_ratio: float = 0.8,
    ) -> None:
        self._high_threshold = high_threshold
        self._medium_threshold = medium_threshold
        self._history_pressure_ratio = history_pressure_ratio

    def evaluate(
        self,
        *,
        req: ChatRequest,
        messages: list[Message],
        summary: str | None,
        memory_bundle: RetrievedMemoryBundle | None,
        pinned_memory_block: str | None,
        provider_name: str,
        model: str,
        history_count: int,
        history_cap: int,
        search_injected: bool,
        local_queue_locked: bool = False,
        external_allowed: bool = False,
    ) -> FailureRiskResult:
        score = 0.0
        risk_types: list[str] = []
        reasons: list[str] = []
        text = req.user_message

        def add(points: float, risk_type: str, reason: str) -> None:
            nonlocal score
            score += points
            if risk_type not in risk_types:
                risk_types.append(risk_type)
            reasons.append(reason)

        if _CURRENT_DATA_RE.search(text) and not search_injected:
            add(0.25, "tool_needed", "Request appears to need current/external data but no search context was injected")

        if req.model_mode == "lite" and _MULTI_STEP_RE.search(text):
            add(0.2, "weak_model", "Lite model selected for a multi-step reasoning or analysis request")

        if history_cap > 0 and history_count >= max(1, int(history_cap * self._history_pressure_ratio)):
            add(0.15, "context_pressure", "Recent history is close to the configured context cap")

        if history_count > history_cap and not summary and not memory_bundle:
            add(0.2, "missing_long_memory", "Older conversation history may exist but no summary or StructMem bundle is available")

        if _MEMORY_RECALL_RE.search(text) and not req.user_name and not req.session_id:
            add(0.15, "memory_recall", "Memory-recall request has no user/session identity to anchor retrieval")

        if pinned_memory_block and summary and self._has_text_overlap_conflict(pinned_memory_block, summary):
            add(0.1, "memory_conflict", "Pinned memory and summary both present with overlapping preference language")

        if local_queue_locked and not external_allowed:
            add(0.2, "capacity_privacy", "Local queue is saturated and policy does not allow external fallback")

        if provider_name != "local" and req.allow_external is False:
            add(0.25, "policy_conflict", "External provider selected while request explicitly disallows external use")

        score = min(1.0, round(score, 3))
        level = "high" if score >= self._high_threshold else "medium" if score >= self._medium_threshold else "low"
        recommended_action = self._recommend_action(risk_types, level, search_injected)
        return FailureRiskResult(
            score=score,
            level=level,
            risk_types=risk_types,
            reasons=reasons,
            recommended_action=recommended_action,
        )

    @staticmethod
    def _has_text_overlap_conflict(a: str, b: str) -> bool:
        markers = ("prefer", "preference", "always", "never", "thich", "muon", "khong")
        a_lower = a.lower()
        b_lower = b.lower()
        return any(marker in a_lower and marker in b_lower for marker in markers)

    @staticmethod
    def _recommend_action(risk_types: list[str], level: str, search_injected: bool) -> str:
        if "tool_needed" in risk_types and not search_injected:
            return "enable_search"
        if level == "high" and any(item in risk_types for item in ("missing_long_memory", "memory_recall", "memory_conflict")):
            return "ask_clarification"
        if level in {"medium", "high"}:
            return "inject_risk_context"
        return "none"

    def decide(self, risk: FailureRiskResult, *, log_only: bool, enable_actions: bool, enable_search_action: bool) -> RiskPolicyDecision:
        if log_only or not enable_actions or risk.recommended_action == "none":
            return RiskPolicyDecision(action=risk.recommended_action, applied=False)
        if risk.recommended_action == "enable_search" and enable_search_action:
            return RiskPolicyDecision(action="enable_search", applied=True, route_reason_suffix="risk_search")
        if risk.recommended_action == "ask_clarification":
            return RiskPolicyDecision(
                action="ask_clarification",
                applied=True,
                route_reason_suffix="risk_clarification",
                message=(
                    "Mình cần thêm một chút ngữ cảnh để tránh trả lời sai. "
                    "Bạn có thể nói rõ phần dữ liệu/bối cảnh nào cần ưu tiên không%s"
                ),
            )
        if risk.recommended_action == "inject_risk_context":
            return RiskPolicyDecision(action="inject_risk_context", applied=True, route_reason_suffix="risk_context")
        return RiskPolicyDecision(action=risk.recommended_action, applied=False)

    def record(
        self,
        *,
        tenant_id: str,
        project_id: str,
        user_id: str | None,
        session_id: str,
        risk: FailureRiskResult,
        decision: RiskPolicyDecision,
        route_before: str,
        route_after: str,
        model_before: str,
        model_after: str,
    ) -> str:
        record_id = f"risk_{uuid4().hex}"
        with get_db_connection() as conn:
            conn.execute(
                """
                INSERT INTO failure_risk_events (
                    id, tenant_id, project_id, user_id, session_id, risk_score,
                    risk_level, risk_types_json, reasons_json, recommended_action,
                    applied_action, action_applied, route_before, route_after,
                    model_before, model_after
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    record_id,
                    tenant_id,
                    project_id,
                    user_id,
                    session_id,
                    risk.score,
                    risk.level,
                    json.dumps(risk.risk_types, ensure_ascii=False),
                    json.dumps(risk.reasons, ensure_ascii=False),
                    risk.recommended_action,
                    decision.action,
                    int(decision.applied),
                    route_before,
                    route_after,
                    model_before,
                    model_after,
                ),
            )
            conn.commit()
        logger.info(
            "failure_risk_recorded id=%s project=%s score=%s level=%s action=%s applied=%s",
            record_id,
            project_id,
            risk.score,
            risk.level,
            decision.action,
            decision.applied,
        )
        return record_id
