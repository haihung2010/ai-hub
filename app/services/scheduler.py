"""PeriodicSummarizer — APScheduler cron for IHI rollups.

Every N hours (configurable via PERIODIC_SUMMARY_CRON, default every 6h),
aggregates ihi_windows from the last 6h, sends to 12B for summary,
stores in ihi_rollups. Skips if accumulated tokens < min_tokens threshold
(FrugalGPT lesson — don't rollup during quiet periods).
"""

from __future__ import annotations

import logging
from typing import Any, Protocol
from uuid import uuid4

logger = logging.getLogger(__name__)


class AIServiceLike(Protocol):
    """Minimal interface PeriodicSummarizer needs from AIService."""

    async def summarize(self, *, text: str, model_override: str, user_id: str, session_id: str) -> str:
        ...


class DBLike(Protocol):
    async def fetch_all(self, sql: str) -> list[dict[str, Any]]: ...
    async def execute(self, sql: str, *params: Any) -> None: ...


class PeriodicSummarizer:
    def __init__(
        self,
        *,
        ai_service: AIServiceLike,
        db: DBLike,
        min_tokens: int = 5000,
        window_hours: int = 6,
    ) -> None:
        self._ai = ai_service
        self._db = db
        self._min_tokens = min_tokens
        self._window_hours = window_hours

    async def rollup_once(self) -> str | None:
        """Run a single rollup pass. Returns rollup_id or None if skipped.

        Idempotent in the sense that each invocation creates one row.
        Safe to call from cron OR from the lifespan shutdown.
        """
        try:
            windows = await self._db.fetch_all(
                f"SELECT * FROM ihi_windows "
                f"WHERE created_at > NOW() - INTERVAL '{self._window_hours} hours' "
                f"ORDER BY created_at"
            )
            if not windows:
                logger.info("rollup: no windows in last %dh — skipping", self._window_hours)
                return None

            total_tokens = sum(len(str(w.get("data", ""))) for w in windows)
            if total_tokens < self._min_tokens:
                logger.info(
                    "rollup: only %d tokens accumulated (< %d) — skipping",
                    total_tokens, self._min_tokens,
                )
                return None

            # Format windows as a table for the 12B prompt
            summary_input = self._format_windows(windows)
            summary = await self._ai.summarize(
                text=summary_input,
                model_override="gemma4-12b",
                user_id="_ihi_rollup",
                session_id="_rollup",
            )

            rollup_id = f"rollup_{uuid4().hex}"
            window_start = windows[0]["created_at"]
            window_end = windows[-1]["created_at"]
            # Note: summary is the second positional arg so callers can
            # locate the produced text without re-querying the table.
            await self._db.execute(
                "INSERT INTO ihi_rollups "
                "(summary, id, window_start, window_end, model, source_window_count, source_token_count) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                summary, rollup_id, window_start, window_end, "gemma4-12b", len(windows), total_tokens,
            )
            logger.info(
                "rollup %s: %d windows, %d tokens → 12B summary stored",
                rollup_id, len(windows), total_tokens,
            )
            return rollup_id
        except Exception as e:
            logger.error("rollup failed (will retry next cron): %s", e)
            return None

    def _format_windows(self, windows: list[dict]) -> str:
        """Format ihi_windows as a CSV-ish table for the 12B prompt."""
        timestamps = [str(w.get("created_at", "?")) for w in windows]
        data_values = [str(w.get("data", "")).replace("\n", " ") for w in windows]
        header = "timestamps: " + " | ".join(timestamps)
        body = "data: " + "".join(data_values)
        return f"{header}\n{body}"
