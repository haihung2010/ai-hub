"""Verify StructMem and SummaryService never run simultaneously for the same message."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.config import Settings
from app.services.ai_service import AIService


def _capture_create_task(coroutines: list):
    def _fake_create_task(coro):
        coroutines.append(coro)
        coro.close()
        return MagicMock()

    return _fake_create_task


def _make_ai_service(enable_structmem: bool) -> tuple[AIService, MagicMock, MagicMock]:
    settings = Settings(
        API_KEY="***",
        LLAMA_CPP_BASE_URL="http://llama.test",
        LLAMA_CPP_OPENAI_URL="http://llama.test/v1",
        ENABLE_STRUCTMEM=enable_structmem,
    )
    structmem = MagicMock()
    structmem.process_recent_messages = AsyncMock()
    summaries = MagicMock()
    summaries.summarize = AsyncMock()

    local = MagicMock()
    local.name = "llama_cpp"

    service = AIService(
        local=local,
        history=MagicMock(),
        users=MagicMock(),
        settings=settings,
        structmem=structmem,
        summaries=summaries,
    )
    return service, structmem, summaries


@pytest.mark.asyncio
async def test_structmem_runs_and_summary_skipped_when_structmem_enabled() -> None:
    service, structmem, summaries = _make_ai_service(enable_structmem=True)

    coroutines = []
    with patch("asyncio.create_task", side_effect=_capture_create_task(coroutines)) as mock_create_task:
        service._schedule_memory_jobs(
            user_id="user-1",
            tenant_id="default",
            project_id="iot",
            session_id="sess-1",
            provider=service._local,
        )

    assert mock_create_task.call_count == 1
    assert len(coroutines) == 1
    structmem.process_recent_messages.assert_called_once()
    summaries.summarize.assert_not_called()


@pytest.mark.asyncio
async def test_summary_runs_and_structmem_skipped_when_structmem_disabled() -> None:
    service, structmem, summaries = _make_ai_service(enable_structmem=False)

    coroutines = []
    with patch("asyncio.create_task", side_effect=_capture_create_task(coroutines)) as mock_create_task:
        service._schedule_memory_jobs(
            user_id="user-1",
            tenant_id="default",
            project_id="iot",
            session_id="sess-1",
            provider=service._local,
        )

    assert mock_create_task.call_count == 1
    assert len(coroutines) == 1
    summaries.summarize.assert_called_once()
    structmem.process_recent_messages.assert_not_called()


@pytest.mark.asyncio
async def test_no_task_created_without_user_id() -> None:
    service, structmem, summaries = _make_ai_service(enable_structmem=True)

    with patch("asyncio.create_task") as mock_create_task:
        service._schedule_memory_jobs(
            user_id=None,
            tenant_id="default",
            project_id="iot",
            session_id="sess-1",
            provider=service._local,
        )

    mock_create_task.assert_not_called()


@pytest.mark.asyncio
async def test_no_task_created_without_structmem_service() -> None:
    settings = Settings(
        API_KEY="***",
        LLAMA_CPP_BASE_URL="http://llama.test",
        LLAMA_CPP_OPENAI_URL="http://llama.test/v1",
        ENABLE_STRUCTMEM=True,
    )
    local = MagicMock()
    local.name = "llama_cpp"
    # structmem not provided → falls to summary branch, but summaries also None
    service = AIService(local=local, history=MagicMock(), users=MagicMock(), settings=settings, structmem=None, summaries=None)

    with patch("asyncio.create_task") as mock_create_task:
        service._schedule_memory_jobs(
            user_id="user-1",
            tenant_id="default",
            project_id="iot",
            session_id="sess-1",
            provider=local,
        )

    mock_create_task.assert_not_called()
