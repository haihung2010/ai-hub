"""Tests for PeriodicSummarizer — APScheduler cron for IHI rollups."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.scheduler import PeriodicSummarizer


@pytest.mark.asyncio
async def test_rollup_skips_when_too_few_tokens():
    """If accumulated windows have < min_tokens, skip rollup."""
    ai_service = MagicMock()
    ai_service.summarize = AsyncMock()
    db = MagicMock()
    db.fetch_all = AsyncMock(return_value=[
        {"data": "short", "created_at": "2026-06-07T00:00:00"},
        {"data": "tiny", "created_at": "2026-06-07T00:01:00"},
    ])
    summarizer = PeriodicSummarizer(
        ai_service=ai_service,
        db=db,
        min_tokens=5000,
    )
    await summarizer.rollup_once()
    ai_service.summarize.assert_not_called()
    db.execute.assert_not_called()


@pytest.mark.asyncio
async def test_rollup_calls_12b_with_accumulated_windows():
    """When enough tokens, summarize via 12B and insert into ihi_rollups."""
    ai_service = MagicMock()
    ai_service.summarize = AsyncMock(return_value="Test summary text")
    db = MagicMock()
    db.fetch_all = AsyncMock(return_value=[
        {"data": "x" * 3000, "created_at": "2026-06-07T00:00:00"},
        {"data": "x" * 3000, "created_at": "2026-06-07T00:01:00"},
    ])
    summarizer = PeriodicSummarizer(
        ai_service=ai_service,
        db=db,
        min_tokens=5000,
    )
    await summarizer.rollup_once()

    ai_service.summarize.assert_called_once()
    call_args = ai_service.summarize.call_args
    assert call_args.kwargs["model_override"] == "gemma4-12b"
    assert call_args.kwargs["user_id"] == "_ihi_rollup"
    assert "x" * 6000 in call_args.kwargs["text"]

    db.execute.assert_called_once()
    insert_args = db.execute.call_args
    sql = insert_args.args[0]
    assert "INSERT INTO ihi_rollups" in sql
    assert "Test summary text" in insert_args.args[1]


@pytest.mark.asyncio
async def test_rollup_handles_empty_windows():
    """No windows in last 6h → skip."""
    ai_service = MagicMock()
    ai_service.summarize = AsyncMock()
    db = MagicMock()
    db.fetch_all = AsyncMock(return_value=[])
    summarizer = PeriodicSummarizer(ai_service=ai_service, db=db, min_tokens=5000)
    await summarizer.rollup_once()
    ai_service.summarize.assert_not_called()


@pytest.mark.asyncio
async def test_rollup_logs_failure_does_not_propagate():
    """If AI service fails, rollup must not raise (best-effort)."""
    ai_service = MagicMock()
    ai_service.summarize = AsyncMock(side_effect=RuntimeError("boom"))
    db = MagicMock()
    db.fetch_all = AsyncMock(return_value=[
        {"data": "x" * 6000, "created_at": "2026-06-07T00:00:00"},
    ])
    summarizer = PeriodicSummarizer(ai_service=ai_service, db=db, min_tokens=5000)
    # Should not raise
    await summarizer.rollup_once()
