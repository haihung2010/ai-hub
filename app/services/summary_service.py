"""Rolling summary memory: generates and stores recursive conversation summaries."""

from __future__ import annotations

import logging

from app.core.database import get_db_connection
from app.models.chat import Message
from app.services.history_service import HistoryService
from app.services.providers.base import ChatProvider

logger = logging.getLogger(__name__)

SUMMARIZATION_PROMPT = (
    "You are a conversation summarizer. Given the previous summary and recent messages, "
    "create an updated comprehensive summary.\n\n"
    "Previous summary:\n{previous_summary}\n\n"
    "Recent messages:\n{messages}\n\n"
    "Write a concise summary capturing: key topics discussed, user preferences, "
    "important decisions, and any ongoing context needed for future conversations. "
    "Keep it under 500 words. Respond with the summary only, no preamble."
)


class SummaryService:
    def __init__(self, history: HistoryService) -> None:
        self._history = history

    def get_latest_summary(self, user_id: str, project_id: str) -> str | None:
        with get_db_connection() as conn:
            row = conn.execute(
                "SELECT content FROM summaries WHERE user_id = ? AND project_id = ? "
                "ORDER BY version DESC LIMIT 1",
                (user_id, project_id),
            ).fetchone()
        return row["content"] if row else None

    def _format_messages(self, messages: list[tuple[int, Message]]) -> str:
        lines: list[str] = []
        for _, msg in messages:
            lines.append(f"{msg.role}: {msg.content}")
        return "\n".join(lines)

    async def summarize(
        self,
        user_id: str,
        project_id: str,
        provider: ChatProvider,
        model: str,
        threshold: int,
    ) -> None:
        unsummarized = self._history.get_unsummarized_messages(user_id, project_id)
        if len(unsummarized) < threshold:
            return

        previous = self.get_latest_summary(user_id, project_id) or "No previous summary."
        formatted = self._format_messages(unsummarized)
        prompt_text = SUMMARIZATION_PROMPT.format(
            previous_summary=previous,
            messages=formatted,
        )

        messages = [
            Message(role="system", content="You are a helpful summarizer."),
            Message(role="user", content=prompt_text),
        ]

        try:
            summary_content = await provider.complete(messages, model, 0.3)
            if not summary_content or not summary_content.strip():
                logger.warning(
                    "LLM returned empty summary user=%s project=%s", user_id, project_id
                )
                return
        except Exception:
            logger.exception(
                "Summary generation failed user=%s project=%s", user_id, project_id
            )
            return

        max_id = unsummarized[-1][0]
        self._upsert_summary(user_id, project_id, summary_content)
        self._history.mark_messages_summarized(user_id, project_id, max_id)
        logger.info(
            "Summary generated user=%s project=%s messages_summarized=%d",
            user_id,
            project_id,
            len(unsummarized),
        )

    def _upsert_summary(self, user_id: str, project_id: str, content: str) -> None:
        with get_db_connection() as conn:
            existing = conn.execute(
                "SELECT version FROM summaries WHERE user_id = ? AND project_id = ?",
                (user_id, project_id),
            ).fetchone()
            if existing:
                new_version = existing["version"] + 1
                conn.execute(
                    "UPDATE summaries SET content = ?, version = ?, updated_at = CURRENT_TIMESTAMP "
                    "WHERE user_id = ? AND project_id = ?",
                    (content, new_version, user_id, project_id),
                )
            else:
                conn.execute(
                    "INSERT INTO summaries (user_id, project_id, content, version) "
                    "VALUES (?, ?, ?, 1)",
                    (user_id, project_id, content),
                )
            conn.commit()
