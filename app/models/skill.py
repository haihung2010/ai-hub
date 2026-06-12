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
    def from_row(row) -> SkillRecord:
        """Build a SkillRecord from a psycopg row.

        Accepts BOTH tuple rows (legacy) AND dict rows (psycopg3
        dict_row, which is what ``get_db_connection`` actually
        returns). Tuple access via ``row[0]`` raised KeyError on
        dict rows, which broke every PATCH /admin/skills/{id}
        call before P1.8's fix. Dict access via ``row["col"]``
        works for both because tuples also support integer keys.
        """
        def _g(key, idx, default=""):
            if isinstance(row, dict):
                return row.get(key, default)
            return row[idx] if idx < len(row) else default

        return SkillRecord(
            id=_g("id", 0),
            tenant_id=_g("tenant_id", 1),
            project_id=_g("project_id", 2),
            name=_g("name", 3),
            description=_g("description", 4, "") or "",
            trigger_patterns_json=_g("trigger_patterns_json", 5, "[]") or "[]",
            prompt_template=_g("prompt_template", 6, "") or "",
            expected_behavior=_g("expected_behavior", 7, "") or "",
            test_cases_json=_g("test_cases_json", 8, "[]") or "[]",
            version=_g("version", 9, 1) or 1,
            is_active=bool(_g("is_active", 10, True)),
            created_at=_g("created_at", 11),
            updated_at=_g("updated_at", 12),
            last_evaluated_at=_g("last_evaluated_at", 13),
            eval_score=_g("eval_score", 14, 0.0) if _g("eval_score", 14, 0.0) is not None else 0.0,
        )


@dataclass
class SkillEvaluationResult:
    skill_id: str
    passed: int
    failed: int
    total: int
    score: float
    details: list[str] = field(default_factory=list)
