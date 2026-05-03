from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Sequence

from app.core.database import DEFAULT_TENANT_ID, get_db_connection
from app.models.chat import Message

logger = logging.getLogger(__name__)

EXTRACTION_PROMPT = """You extract structured long-term memory from conversations.
Return strict JSON with keys: episodic, semantic, relational, procedural.
Each key maps to a list of objects with fields:
- subject
- predicate
- object
- content
- salience
Only keep durable or reusable information. Ignore chit-chat.
"""


class MemoryExtractionService:
    def _build_source_text(self, messages: Sequence[tuple[int, Message]]) -> str:
        return "\n".join(f"{message.role}: {message.content}" for _, message in messages)

    def _build_prompt(self, messages: Sequence[tuple[int, Message]]) -> list[Message]:
        return [
            Message(role="system", content=EXTRACTION_PROMPT),
            Message(role="user", content=self._build_source_text(messages)),
        ]

    def _parse_payload(self, payload: str) -> dict[str, list[dict]]:
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError:
            logger.warning("StructMem extraction returned invalid JSON")
            return {"episodic": [], "semantic": [], "relational": [], "procedural": []}

        result: dict[str, list[dict]] = {}
        for key in ("episodic", "semantic", "relational", "procedural"):
            values = parsed.get(key, [])
            result[key] = values if isinstance(values, list) else []
        return result

    def _insert_episode(
        self,
        *,
        user_id: str,
        tenant_id: str,
        project_id: str,
        session_id: str,
        start_message_id: int,
        end_message_id: int,
        source_text: str,
    ) -> str:
        episode_id = str(uuid.uuid4())
        with get_db_connection() as conn:
            conn.execute(
                "INSERT INTO memory_episodes (id, user_id, tenant_id, project_id, session_id, start_message_id, end_message_id, source_text) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (episode_id, user_id, tenant_id, project_id, session_id, start_message_id, end_message_id, source_text),
            )
            conn.commit()
        return episode_id

    def _insert_memory_items(
        self,
        *,
        episode_id: str,
        user_id: str,
        tenant_id: str,
        project_id: str,
        extracted: dict[str, list[dict]],
    ) -> None:
        with get_db_connection() as conn:
            for memory_type, values in extracted.items():
                for value in values:
                    content = str(value.get("content", "")).strip()
                    if not content:
                        continue
                    conn.execute(
                        "INSERT INTO memory_items (id, episode_id, user_id, tenant_id, project_id, memory_type, subject, predicate, object, content, salience) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                        (
                            str(uuid.uuid4()),
                            episode_id,
                            user_id,
                            tenant_id,
                            project_id,
                            memory_type,
                            value.get("subject"),
                            value.get("predicate"),
                            value.get("object"),
                            content,
                            float(value.get("salience", 0) or 0),
                        ),
                    )
            conn.commit()

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
    ) -> str | None:
        if not messages:
            return None

        prompt_messages = self._build_prompt(messages)
        source_text = self._build_source_text(messages)
        payload = await provider.complete(prompt_messages, model, 0.2)
        extracted = self._parse_payload(payload)
        start_message_id = messages[0][0]
        end_message_id = messages[-1][0]
        episode_id = self._insert_episode(
            user_id=user_id,
            tenant_id=tenant_id,
            project_id=project_id,
            session_id=session_id,
            start_message_id=start_message_id,
            end_message_id=end_message_id,
            source_text=source_text,
        )
        self._insert_memory_items(
            episode_id=episode_id,
            user_id=user_id,
            tenant_id=tenant_id,
            project_id=project_id,
            extracted=extracted,
        )
        logger.info(
            "Stored StructMem episode user=%s project=%s episode=%s messages=%d",
            user_id,
            project_id,
            episode_id,
            len(messages),
        )
        return episode_id
