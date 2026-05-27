"""MUSE-Autoskill-inspired skill registry service.

Skill lifecycle:
  1. Create  — user/agent registers a skill with trigger patterns + prompt template
  2. Store    — persisted in skills table per (tenant_id, project_id, name)
  3. Evaluate — run test cases against skill output, compute eval_score
  4. Refine   — low eval_score triggers re-extraction from recent chat history
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import TYPE_CHECKING

from app.core.database import get_db_connection
from app.models.skill import SkillEvaluationResult, SkillRecord

if TYPE_CHECKING:
    from app.services.providers.base import ChatProvider

logger = logging.getLogger(__name__)


class SkillService:
    def __init__(self) -> None:
        self._cache: dict[str, list[SkillRecord]] = {}

    # ── CRUD ────────────────────────────────────────────────────────────────

    def create(
        self,
        tenant_id: str,
        project_id: str,
        name: str,
        description: str = "",
        trigger_patterns: list[str] | None = None,
        prompt_template: str = "",
        expected_behavior: str = "",
        test_cases: list[dict] | None = None,
    ) -> SkillRecord:
        import uuid
        sid = uuid.uuid4().hex[:16]
        now = datetime.utcnow()
        trigger_json = json.dumps(trigger_patterns or [])
        test_json = json.dumps(test_cases or [])

        with get_db_connection() as conn:
            conn.execute(
                """
                INSERT INTO skills
                  (id, tenant_id, project_id, name, description,
                   trigger_patterns_json, prompt_template, expected_behavior,
                   test_cases_json, version, is_active, created_at, updated_at, eval_score)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,1,1,%s,%s,0.0)
                """,
                (sid, tenant_id, project_id, name, description,
                 trigger_json, prompt_template, expected_behavior, test_json,
                 now, now),
            )
            conn.commit()

        record = SkillRecord(
            id=sid,
            tenant_id=tenant_id,
            project_id=project_id,
            name=name,
            description=description,
            trigger_patterns_json=trigger_json,
            prompt_template=prompt_template,
            expected_behavior=expected_behavior,
            test_cases_json=test_json,
            version=1,
            is_active=True,
            created_at=now,
            updated_at=now,
            eval_score=0.0,
        )
        self._cache.clear()
        return record

    def list_skills(self, tenant_id: str, project_id: str, include_inactive: bool = False) -> list[SkillRecord]:
        with get_db_connection() as conn:
            if include_inactive:
                rows = conn.execute(
                    "SELECT id,tenant_id,project_id,name,description,trigger_patterns_json,"
                    "prompt_template,expected_behavior,test_cases_json,version,is_active,"
                    "created_at,updated_at,last_evaluated_at,eval_score "
                    "FROM skills WHERE tenant_id=%s AND project_id=%s "
                    "ORDER BY name",
                    (tenant_id, project_id),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id,tenant_id,project_id,name,description,trigger_patterns_json,"
                    "prompt_template,expected_behavior,test_cases_json,version,is_active,"
                    "created_at,updated_at,last_evaluated_at,eval_score "
                    "FROM skills WHERE tenant_id=%s AND project_id=%s AND is_active=1 "
                    "ORDER BY name",
                    (tenant_id, project_id),
                ).fetchall()
        return [SkillRecord.from_row(row) for row in rows]

    def get_skill(self, skill_id: str) -> SkillRecord | None:
        with get_db_connection() as conn:
            row = conn.execute(
                "SELECT id,tenant_id,project_id,name,description,trigger_patterns_json,"
                "prompt_template,expected_behavior,test_cases_json,version,is_active,"
                "created_at,updated_at,last_evaluated_at,eval_score "
                "FROM skills WHERE id=%s",
                (skill_id,),
            ).fetchone()
        return SkillRecord.from_row(row) if row else None

    def update(
        self,
        skill_id: str,
        description: str | None = None,
        trigger_patterns: list[str] | None = None,
        prompt_template: str | None = None,
        expected_behavior: str | None = None,
        test_cases: list[dict] | None = None,
        is_active: bool | None = None,
    ) -> SkillRecord | None:
        fields: list[str] = []
        values: list[object] = []
        if description is not None:
            fields.append("description=%s")
            values.append(description)
        if trigger_patterns is not None:
            fields.append("trigger_patterns_json=%s")
            values.append(json.dumps(trigger_patterns))
        if prompt_template is not None:
            fields.append("prompt_template=%s")
            values.append(prompt_template)
        if expected_behavior is not None:
            fields.append("expected_behavior=%s")
            values.append(expected_behavior)
        if test_cases is not None:
            fields.append("test_cases_json=%s")
            values.append(json.dumps(test_cases))
        if is_active is not None:
            fields.append("is_active=%s")
            values.append(int(is_active))
        if not fields:
            return self.get_skill(skill_id)

        fields.append("updated_at=%s")
        values.append(datetime.utcnow())
        values.append(skill_id)

        with get_db_connection() as conn:
            conn.execute(
                f"UPDATE skills SET {', '.join(f.split('=')[0]+'=%s' for f in fields)}"
                f" WHERE id=%s",
                values,
            )
            conn.commit()

        self._cache.clear()
        return self.get_skill(skill_id)

    def delete(self, skill_id: str) -> bool:
        with get_db_connection() as conn:
            conn.execute("DELETE FROM skills WHERE id=%s", (skill_id,))
            conn.commit()
        self._cache.clear()
        return True

    # ── Skill matching ────────────────────────────────────────────────────────

    def match_skills(self, tenant_id: str, project_id: str, text: str) -> list[SkillRecord]:
        """Return all active skills whose trigger patterns match the given text."""
        skills = self.list_skills(tenant_id, project_id, include_inactive=False)
        normalized = self._strip_diacritics(text.lower())
        matched: list[SkillRecord] = []
        for skill in skills:
            try:
                patterns: list[str] = json.loads(skill.trigger_patterns_json)
            except Exception:
                continue
            for pat in patterns:
                if re.search(pat, normalized, re.IGNORECASE):
                    matched.append(skill)
                    break
        return matched

    def format_prompt_for_skill(self, skill: SkillRecord) -> str:
        """Format skill prompt_template for injection into system prompt."""
        if not skill.prompt_template:
            return ""
        return f"### SKILL: {skill.name} ###\n{skill.prompt_template.strip()}\n"

    # ── Evaluation ─────────────────────────────────────────────────────────

    async def evaluate_skill(
        self,
        skill_id: str,
        provider: ChatProvider,
        model: str,
    ) -> SkillEvaluationResult:
        """Run test cases defined in the skill against the given provider.

        Each test case: {input: str, expected_keywords: list[str]}
        A test passes if at least one expected keyword appears in the response.
        """
        skill = self.get_skill(skill_id)
        if not skill:
            return SkillEvaluationResult(skill_id=skill_id, passed=0, failed=0, total=0, score=0.0)

        try:
            test_cases: list[dict] = json.loads(skill.test_cases_json)
        except Exception:
            test_cases = []

        if not test_cases:
            return SkillEvaluationResult(
                skill_id=skill_id, passed=0, failed=0, total=0, score=0.0,
                details=["No test cases defined"],
            )

        passed = failed = 0
        details: list[str] = []

        for tc in test_cases:
            tc_input = tc.get("input", "")
            expected: list[str] = tc.get("expected_keywords", [])
            if not tc_input:
                details.append(f"SKIP empty test case")
                continue

            try:
                response = await provider.chat(
                    messages=[{"role": "user", "content": tc_input}],
                    model=model,
                    max_tokens=200,
                )
                content = response.content if hasattr(response, "content") else str(response)
                found = [kw for kw in expected if kw.lower() in content.lower()]
                if found:
                    passed += 1
                    details.append(f"PASS '{tc_input[:40]}' → found: {found}")
                else:
                    failed += 1
                    details.append(f"FAIL '{tc_input[:40]}' → expected {expected}, got: {content[:80]}")
            except Exception as exc:
                failed += 1
                details.append(f"ERROR '{tc_input[:40]}' → {exc}")

        total = passed + failed
        score = (passed / total) if total > 0 else 0.0
        now = datetime.utcnow()

        with get_db_connection() as conn:
            conn.execute(
                "UPDATE skills SET eval_score=%s, last_evaluated_at=%s WHERE id=%s",
                (score, now, skill_id),
            )
            conn.commit()

        self._cache.clear()
        return SkillEvaluationResult(
            skill_id=skill_id, passed=passed, failed=failed, total=total,
            score=score, details=details,
        )

    async def refine_from_chat_history(
        self,
        skill_id: str,
        messages: list[dict],
        provider: ChatProvider,
        model: str,
    ) -> SkillRecord | None:
        """Re-extract skill prompt_template from recent chat history via LLM.

        Looks at the most recent user/assistant message pairs to update
        the skill's prompt_template based on demonstrated behavior.
        """
        recent = "\n".join(
            f"{m.get('role','')}: {m.get('content','')}" for m in messages[-6:]
        )
        skill = self.get_skill(skill_id)
        if not skill:
            return None

        extraction_prompt = (
            f"Based on this chat history, extract the core instruction/prompt "
            f"that would make an AI respond in the same way. "
            f"Return ONLY the extracted prompt template string (no explanation).\n\n"
            f"Chat history:\n{recent}\n\nExtracted prompt:"
        )

        try:
            response = await provider.chat(
                messages=[{"role": "user", "content": extraction_prompt}],
                model=model,
                max_tokens=300,
            )
            new_template = response.content if hasattr(response, "content") else str(response)
        except Exception as exc:
            logger.warning("Skill refinement failed skill=%s: %s", skill_id, exc)
            return skill

        updated = self.update(skill_id, prompt_template=new_template.strip())
        if updated:
            with get_db_connection() as conn:
                conn.execute(
                    "UPDATE skills SET version=version+1, updated_at=%s WHERE id=%s",
                    (datetime.utcnow(), skill_id),
                )
                conn.commit()
        self._cache.clear()
        return updated or self.get_skill(skill_id)

    # ── Helpers ─────────────────────────────────────────────────────────────

    @staticmethod
    def _strip_diacritics(text: str) -> str:
        import unicodedata
        return unicodedata.normalize("NFD", text).encode("ascii", "ignore").decode("ascii").lower()
