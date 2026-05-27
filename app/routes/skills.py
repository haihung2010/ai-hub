"""Skill registry API routes — MUSE-Autoskill lifecycle endpoints."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.models.skill import SkillEvaluationResult

router = APIRouter(prefix="/v1/projects", tags=["skills"])


# ── Request/Response models ────────────────────────────────────────────────────


class CreateSkillRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    description: str = Field(default="")
    trigger_patterns: list[str] = Field(default_factory=list)
    prompt_template: str = Field(default="")
    expected_behavior: str = Field(default="")
    test_cases: list[dict] = Field(default_factory=list)


class UpdateSkillRequest(BaseModel):
    description: str | None = None
    trigger_patterns: list[str] | None = None
    prompt_template: str | None = None
    expected_behavior: str | None = None
    test_cases: list[dict] | None = None
    is_active: bool | None = None


class SkillResponse(BaseModel):
    id: str
    tenant_id: str
    project_id: str
    name: str
    description: str
    trigger_patterns: list[str]
    prompt_template: str
    expected_behavior: str
    test_cases: list[dict]
    version: int
    is_active: bool
    created_at: str | None
    updated_at: str | None
    last_evaluated_at: str | None
    eval_score: float


class SkillListResponse(BaseModel):
    skills: list[SkillResponse]
    total: int


class EvaluationResultResponse(BaseModel):
    skill_id: str
    passed: int
    failed: int
    total: int
    score: float
    details: list[str]


def _skill_to_response(skill) -> SkillResponse:
    return SkillResponse(
        id=skill.id,
        tenant_id=skill.tenant_id,
        project_id=skill.project_id,
        name=skill.name,
        description=skill.description or "",
        trigger_patterns=json.loads(skill.trigger_patterns_json or "[]"),
        prompt_template=skill.prompt_template or "",
        expected_behavior=skill.expected_behavior or "",
        test_cases=json.loads(skill.test_cases_json or "[]"),
        version=skill.version,
        is_active=skill.is_active,
        created_at=skill.created_at.isoformat() if skill.created_at else None,
        updated_at=skill.updated_at.isoformat() if skill.updated_at else None,
        last_evaluated_at=skill.last_evaluated_at.isoformat() if skill.last_evaluated_at else None,
        eval_score=skill.eval_score,
    )


# ── Routes ────────────────────────────────────────────────────────────────────────


@router.get("/{project_id}/skills", response_model=SkillListResponse)
async def list_skills(project_id: str, request: Request, include_inactive: bool = False) -> SkillListResponse:
    """List all skills for a project."""
    from app.services.skill_service import SkillService

    tenant_id = getattr(request.state, "api_key_tenant_id", "default")
    service = SkillService()
    skills = service.list_skills(tenant_id, project_id, include_inactive=include_inactive)
    return SkillListResponse(
        skills=[_skill_to_response(s) for s in skills],
        total=len(skills),
    )


@router.post("/{project_id}/skills", response_model=SkillResponse, status_code=201)
async def create_skill(project_id: str, request: Request, payload: CreateSkillRequest) -> SkillResponse:
    """Create a new skill for a project."""
    from app.services.skill_service import SkillService

    tenant_id = getattr(request.state, "api_key_tenant_id", "default")
    service = SkillService()
    skill = service.create(
        tenant_id=tenant_id,
        project_id=project_id,
        name=payload.name,
        description=payload.description,
        trigger_patterns=payload.trigger_patterns,
        prompt_template=payload.prompt_template,
        expected_behavior=payload.expected_behavior,
        test_cases=payload.test_cases,
    )
    return _skill_to_response(skill)


@router.patch("/{project_id}/skills/{skill_id}", response_model=SkillResponse)
async def update_skill(
    project_id: str,
    skill_id: str,
    request: Request,
    payload: UpdateSkillRequest,
) -> SkillResponse:
    """Update an existing skill."""
    from app.services.skill_service import SkillService

    service = SkillService()
    existing = service.get_skill(skill_id)
    if not existing or existing.project_id != project_id:
        raise HTTPException(status_code=404, detail="Skill not found")

    updated = service.update(
        skill_id=skill_id,
        description=payload.description,
        trigger_patterns=payload.trigger_patterns,
        prompt_template=payload.prompt_template,
        expected_behavior=payload.expected_behavior,
        test_cases=payload.test_cases,
        is_active=payload.is_active,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Skill not found")
    return _skill_to_response(updated)


@router.delete("/{project_id}/skills/{skill_id}", response_model=None, status_code=204)
async def delete_skill(project_id: str, skill_id: str, request: Request) -> None:
    """Delete a skill."""
    from app.services.skill_service import SkillService

    service = SkillService()
    existing = service.get_skill(skill_id)
    if not existing or existing.project_id != project_id:
        raise HTTPException(status_code=404, detail="Skill not found")
    service.delete(skill_id)


@router.post("/{project_id}/skills/{skill_id}/evaluate", response_model=EvaluationResultResponse)
async def evaluate_skill(project_id: str, skill_id: str, request: Request) -> EvaluationResultResponse:
    """Run skill test cases and update eval_score."""
    from app.services.skill_service import SkillService
    from app.main import get_ai_service

    service = SkillService()
    existing = service.get_skill(skill_id)
    if not existing or existing.project_id != project_id:
        raise HTTPException(status_code=404, detail="Skill not found")

    ai_service = get_ai_service()
    if not ai_service:
        raise HTTPException(status_code=503, detail="AI service not available")

    provider = ai_service._local
    result = await service.evaluate_skill(skill_id, provider, model="local-gemma4-e4b-q4")
    return EvaluationResultResponse(
        skill_id=result.skill_id,
        passed=result.passed,
        failed=result.failed,
        total=result.total,
        score=result.score,
        details=result.details,
    )


@router.get("/{project_id}/skills/{skill_id}", response_model=SkillResponse)
async def get_skill(project_id: str, skill_id: str, request: Request) -> SkillResponse:
    """Get a single skill by ID."""
    from app.services.skill_service import SkillService

    service = SkillService()
    skill = service.get_skill(skill_id)
    if not skill or skill.project_id != project_id:
        raise HTTPException(status_code=404, detail="Skill not found")
    return _skill_to_response(skill)
