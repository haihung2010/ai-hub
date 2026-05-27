"""Skill record model — MUSE-Autoskill-style reusable, testable skill units."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class SkillRecord:
    id: str
    tenant_id: str
    project_id: str
    name: str
    description: str = ""
    trigger_patterns_json: str = "[]"
    prompt_template: str = ""
    expected_behavior: str = ""
    test_cases_json: str = "[]"
    version: int = 1
    is_active: bool = True
    created_at: datetime | None = None
    updated_at: datetime | None = None
    last_evaluated_at: datetime | None = None
    eval_score: float = 0.0

    @staticmethod
    def from_row(row: tuple) -> SkillRecord:
        return SkillRecord(
            id=row[0],
            tenant_id=row[1],
            project_id=row[2],
            name=row[3],
            description=row[4] or "",
            trigger_patterns_json=row[5] or "[]",
            prompt_template=row[6] or "",
            expected_behavior=row[7] or "",
            test_cases_json=row[8] or "[]",
            version=row[9] or 1,
            is_active=bool(row[10]),
            created_at=row[11],
            updated_at=row[12],
            last_evaluated_at=row[13],
            eval_score=row[14] if row[14] is not None else 0.0,
        )


@dataclass
class SkillEvaluationResult:
    skill_id: str
    passed: int
    failed: int
    total: int
    score: float
    details: list[str] = field(default_factory=list)
