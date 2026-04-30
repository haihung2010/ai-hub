from __future__ import annotations

import logging

from app.services.history_service import HistoryService
from app.services.memory_consolidation_service import MemoryConsolidationService
from app.services.memory_extraction_service import MemoryExtractionService

logger = logging.getLogger(__name__)

# Run consolidation every N extractions to limit LLM overhead.
_CONSOLIDATION_EVERY_N_EXTRACTIONS = 3


class StructMemService:
    def __init__(
        self,
        history: HistoryService,
        extraction: MemoryExtractionService,
        consolidation: MemoryConsolidationService | None = None,
    ) -> None:
        self._history = history
        self._extraction = extraction
        self._consolidation = consolidation
        self._extraction_counter: dict[str, int] = {}

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
        consolidation_model: str | None = None,
    ) -> str | None:
        if not user_id:
            return None

        unsummarized = self._history.get_unsummarized_messages(
            user_id,
            project_id,
            tenant_id=tenant_id,
        )
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

        self._history.mark_messages_summarized(
            user_id,
            project_id,
            unsummarized[-1][0],
            tenant_id=tenant_id,
        )
        logger.info(
            "Processed StructMem pipeline user=%s project=%s episode=%s messages=%d",
            user_id,
            project_id,
            episode_id,
            len(unsummarized),
        )

        # Trigger consolidation every N extractions
        if self._consolidation and consolidation_model:
            bucket_key = f"{tenant_id}:{user_id}:{project_id}"
            count = self._extraction_counter.get(bucket_key, 0) + 1
            self._extraction_counter[bucket_key] = count
            if count % _CONSOLIDATION_EVERY_N_EXTRACTIONS == 0:
                await self._consolidation.consolidate(
                    user_id=user_id,
                    tenant_id=tenant_id,
                    project_id=project_id,
                    provider=provider,
                    model=consolidation_model,
                )

        return episode_id
