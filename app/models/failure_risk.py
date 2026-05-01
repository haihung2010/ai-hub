"""Schemas for failure-risk scoring and policy decisions."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

RiskLevel = Literal["low", "medium", "high"]
RiskAction = Literal["none", "inject_risk_context", "enable_search", "ask_clarification"]


class FailureRiskResult(BaseModel):
    score: float = Field(ge=0.0, le=1.0)
    level: RiskLevel
    risk_types: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    recommended_action: RiskAction = "none"


class RiskPolicyDecision(BaseModel):
    action: RiskAction = "none"
    applied: bool = False
    route_reason_suffix: str | None = None
    message: str | None = None
