"""Lightweight fact extraction for fanpage chatbot - simpler and faster than StructMem."""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Sequence

from app.core.database import DEFAULT_TENANT_ID, get_db_connection
from app.models.chat import Message

logger = logging.getLogger(__name__)

FANPAGE_FACT_EXTRACTION_PROMPT = """Extract key facts from this conversation for a fanpage chatbot.
Return strict JSON with a single key "facts" mapping to a list of objects with:
- fact: The actual fact (1-2 sentences, clear and specific)
- category: One of: preference, behavior, interest, context, other
- confidence: 0.0-1.0 confidence score

Only extract durable, reusable facts. Ignore greetings, chit-chat, and temporary context.
Focus on user preferences, interests, and important context about the user or fanpage.
"""


class FactExtractionService:
    def _build_source_text(self, messages: Sequence[tuple[int, Message]]) -> str:
        return "\n".join(f"{message.role}: {message.content}" for _, message in messages)

    def _build_prompt(self, messages: Sequence[tuple[int, Message]]) -> list[Message]:
        return [
            Message(role="system", content=FANPAGE_FACT_EXTRACTION_PROMPT),
            Message(role="user", content=self._build_source_text(messages)),
        ]

    def _parse_payload(self, payload: str) -> list[dict]:
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError:
            logger.warning("Fact extraction returned invalid JSON")
            return []

        facts = parsed.get("facts", [])
        return facts if isinstance(facts, list) else []

    def _insert_facts(
        self,
        *,
        user_id: str,
        tenant_id: str,
        project_id: str,
        session_id: str,
        facts: list[dict],
    ) -> int:
        if not facts:
            return 0

        with get_db_connection() as conn:
            count = 0
            for fact in facts:
                fact_text = str(fact.get("fact", "")).strip()
                if not fact_text:
                    continue
                conn.execute(
                    "INSERT INTO fanpage_facts (id, user_id, tenant_id, project_id, session_id, fact, category, confidence) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        str(uuid.uuid4()),
                        user_id,
                        tenant_id,
                        project_id,
                        session_id,
                        fact_text,
                        fact.get("category", "other"),
                        float(fact.get("confidence", 0.5) or 0.5),
                    ),
                )
                count += 1
            conn.commit()
        return count

    async def extract_and_store(
        self,
        *,
        user_id: str,
        tenant_id: str = DEFAULT_TENANT_ID,
        project_id: str,
        session_id: str,
        messages: Sequence[tuple[int, Message]],
        provider,
        model: str,
    ) -> int:
        if not messages:
            return 0

        prompt_messages = self._build_prompt(messages)
        payload = await provider.complete(prompt_messages, model, 0.2)
        facts = self._parse_payload(payload)
        count = self._insert_facts(
            user_id=user_id,
            tenant_id=tenant_id,
            project_id=project_id,
            session_id=session_id,
            facts=facts,
        )
        logger.info(
            "Extracted and stored %d facts user=%s project=%s session=%s",
            count,
            user_id,
            project_id,
            session_id,
        )
        return count
