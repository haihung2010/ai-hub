"""Rolling summary memory: generates and stores recursive conversation summaries."""

from __future__ import annotations

import asyncio
import html
import logging
import re

from app.core.database import DEFAULT_TENANT_ID, get_db_connection
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
    def __init__(self, history: HistoryService | None = None, max_concurrency: int = 1) -> None:
        self._history = history or HistoryService()
        self._summary_lock = asyncio.Semaphore(max(1, max_concurrency))

    @staticmethod
    def _sanitize_text(text: str) -> str:
        for _ in range(2):
            text = re.sub(r"(%sis)^\s*&lt;\|channel(%s:\|&gt;|&gt;)%s[^\n]*", "", text)
            text = re.sub(r"(%sis)^\s*&lt;channel\|&gt;[^\n]*", "", text)
            text = re.sub(r"&lt;\|[^\n&]*(%s:\|&gt;|&gt;)%s", "", text)
            text = re.sub(r"&lt;channel\|&gt;", "", text, flags=re.IGNORECASE)
            text = html.unescape(text)
            text = re.sub(r"(%sis)^\s*<\|channel(%s:\|>|>)%s[^\n]*", "", text)
            text = re.sub(r"(%sis)^\s*<channel\|>[^\n]*", "", text)
            text = re.sub(r"<\|[^\n>]*(%s:\|>|>)%s", "", text)
            text = re.sub(r"<channel\|>", "", text, flags=re.IGNORECASE)
        text = re.sub(r"(%sm)^\s*text(%s:acular)%s[-\w{}.:\"')]*\s*", "", text, flags=re.IGNORECASE)
        return text.strip()

    def get_latest_summary(
        self,
        user_id: str,
        project_id: str,
        tenant_id: str = DEFAULT_TENANT_ID,
    ) -> str | None:
        # Skip the summary if it predates the user's memory boundary for this
        # project — boundary marks "fresh start" for memory recall while
        # keeping the underlying rows for audit.
        with get_db_connection() as conn:
            row = conn.execute(
                "SELECT s.content, s.updated_at, b.boundary_at "
                "FROM summaries s "
                "LEFT JOIN memory_boundaries b ON b.tenant_id = s.tenant_id "
                "  AND b.user_id = s.user_id AND b.project_id = s.project_id "
                "WHERE s.tenant_id = %s AND s.user_id = %s AND s.project_id = %s "
                "ORDER BY s.version DESC LIMIT 1",
                (tenant_id, user_id, project_id),
            ).fetchone()
        if not row:
            return None
        boundary = row.get("boundary_at")
        updated = row.get("updated_at")
        if boundary and updated and updated < boundary:
            return None
        return row["content"]

    def _format_messages(self, messages: list[tuple[int, Message]]) -> str:
        lines: list[str] = []
        for _, msg in messages:
            lines.append(f"{msg.role}: {self._sanitize_text(msg.content)}")
        return "\n".join(lines)

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        return max(1, (len(text) + 3) // 4)

    def upsert_summary(
        self,
        user_id: str,
        project_id: str,
        content: str,
        tenant_id: str = DEFAULT_TENANT_ID,
    ) -> None:
        self._upsert_summary(user_id, project_id, content, tenant_id)

    async def summarize(
        self,
        user_id: str,
        project_id: str,
        provider: ChatProvider,
        model: str,
        threshold: int,
        tenant_id: str = DEFAULT_TENANT_ID,
        token_threshold: int | None = None,
    ) -> None:
        unsummarized = self._history.get_unsummarized_messages(user_id, project_id, tenant_id)
        formatted = self._format_messages(unsummarized)
        over_message_threshold = len(unsummarized) >= threshold
        over_token_threshold = bool(token_threshold and self._estimate_tokens(formatted) >= token_threshold)
        if not over_message_threshold and not over_token_threshold:
            return

        previous = self._sanitize_text(self.get_latest_summary(user_id, project_id, tenant_id) or "No previous summary.")
        prompt_text = SUMMARIZATION_PROMPT.format(
            previous_summary=previous,
            messages=formatted,
        )

        messages = [
            Message(role="system", content="You are a helpful summarizer."),
            Message(role="user", content=prompt_text),
        ]

        async with self._summary_lock:
            try:
                summary_content = self._sanitize_text(await provider.complete(messages, model, 0.3))
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
            self._upsert_summary(user_id, project_id, summary_content, tenant_id)
            self._history.mark_messages_summarized(user_id, project_id, max_id, tenant_id)
        logger.info(
            "Summary generated user=%s project=%s messages_summarized=%d",
            user_id,
            project_id,
            len(unsummarized),
        )

    def _upsert_summary(
        self,
        user_id: str,
        project_id: str,
        content: str,
        tenant_id: str = DEFAULT_TENANT_ID,
    ) -> None:
        with get_db_connection() as conn:
            existing = conn.execute(
                "SELECT version FROM summaries WHERE tenant_id = %s AND user_id = %s AND project_id = %s",
                (tenant_id, user_id, project_id),
            ).fetchone()
            if existing:
                new_version = existing["version"] + 1
                conn.execute(
                    "UPDATE summaries SET content = %s, version = %s, updated_at = CURRENT_TIMESTAMP "
                    "WHERE tenant_id = %s AND user_id = %s AND project_id = %s",
                    (content, new_version, tenant_id, user_id, project_id),
                )
            else:
                conn.execute(
                    "INSERT INTO summaries (tenant_id, user_id, project_id, content, version) "
                    "VALUES (%s, %s, %s, %s, 1)",
                    (tenant_id, user_id, project_id, content),
                )
            conn.commit()
        logger.info(
            "summary.upsert tenant=%s user=%s project=%s version=%d chars=%d",
            tenant_id,
            user_id,
            project_id,
            (existing["version"] + 1) if existing else 1,
            len(content),
        )
