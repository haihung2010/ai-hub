from __future__ import annotations

import logging

from app.services.history_service import HistoryService
from app.services.memory_extraction_service import MemoryExtractionService

logger = logging.getLogger(__name__)


class StructMemService:
    def __init__(
        self,
        history: HistoryService,
        extraction: MemoryExtractionService,
    ) -> None:
        self._history = history
        self._extraction = extraction

    async def process_recent_messages(
        self,
        *,
        user_id: str | None,
        tenant_id: str,
        project_id: str,
        session_id: str,
        provider,
        model: str,
        threshold: int,
    ) -> str | None:
        if not user_id:
            return None

        unsummarized = self._history.get_unsummarized_messages(user_id, project_id)
        if len(unsummarized) < threshold:
            return None

        episode_id = await self._extraction.extract_and_store(
            user_id=user_id,
            tenant_id=tenant_id,
            project_id=project_id,
            session_id=session_id,
            messages=unsummarized,
            provider=provider,
            model=model,
        )
        if not episode_id:
            return None

        self._history.mark_messages_summarized(user_id, project_id, unsummarized[-1][0])
        logger.info(
            "Processed StructMem pipeline user=%s project=%s episode=%s messages=%d",
            user_id,
            project_id,
            episode_id,
            len(unsummarized),
        )
        return episode_id
