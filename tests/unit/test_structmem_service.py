"""Unit tests for StructMemService.process_recent_messages."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.database import init_db
from app.services.history_service import HistoryService
from app.services.memory_extraction_service import MemoryExtractionService
from app.services.structmem_service import StructMemService


@pytest.fixture(autouse=True)
def _init_db() -> None:
    init_db()


@pytest.fixture
def uid() -> str:
    import uuid
    return f"test-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def history() -> HistoryService:
    return HistoryService()


@pytest.fixture
def extraction() -> MemoryExtractionService:
    return MemoryExtractionService()


@pytest.fixture
def svc(history: HistoryService, extraction: MemoryExtractionService) -> StructMemService:
    return StructMemService(history=history, extraction=extraction)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_returns_none_when_no_user_id(
    svc: StructMemService,
) -> None:
    result = await svc.process_recent_messages(
        user_id=None,
        tenant_id="t",
        project_id="p",
        session_id="s",
        provider=AsyncMock(),
        model="m",
        threshold=5,
    )
    assert result is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_returns_none_below_threshold(
    svc: StructMemService, history: HistoryService, uid: str,
) -> None:
    session_id = history.create_session("proj", user_id=uid)
    history.save_message(session_id, "user", "hi", user_id=uid)

    result = await svc.process_recent_messages(
        user_id=uid,
        tenant_id="t",
        project_id="proj",
        session_id=session_id,
        provider=AsyncMock(),
        model="m",
        threshold=10,
    )
    assert result is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_calls_extraction_above_threshold(
    svc: StructMemService, history: HistoryService, extraction: MemoryExtractionService, uid: str,
) -> None:
    session_id = history.create_session("proj2", user_id=uid)
    for i in range(3):
        history.save_message(session_id, "user", f"msg {i}", user_id=uid)

    extraction.extract_and_store = AsyncMock(return_value="ep-123")  # type: ignore[method-assign]

    result = await svc.process_recent_messages(
        user_id=uid,
        tenant_id="t",
        project_id="proj2",
        session_id=session_id,
        provider=AsyncMock(),
        model="m",
        threshold=2,
    )
    assert result == "ep-123"
    extraction.extract_and_store.assert_awaited_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_marks_messages_summarized_on_success(
    svc: StructMemService, history: HistoryService, extraction: MemoryExtractionService, uid: str,
) -> None:
    session_id = history.create_session("proj3", user_id=uid)
    for i in range(3):
        history.save_message(session_id, "user", f"msg {i}", user_id=uid)

    extraction.extract_and_store = AsyncMock(return_value="ep-456")  # type: ignore[method-assign]

    await svc.process_recent_messages(
        user_id=uid,
        tenant_id="t",
        project_id="proj3",
        session_id=session_id,
        provider=AsyncMock(),
        model="m",
        threshold=2,
    )
    remaining = history.get_unsummarized_messages(uid, "proj3")
    assert len(remaining) == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_returns_none_when_extraction_returns_none(
    svc: StructMemService, history: HistoryService, extraction: MemoryExtractionService, uid: str,
) -> None:
    session_id = history.create_session("proj4", user_id=uid)
    for i in range(3):
        history.save_message(session_id, "user", f"msg {i}", user_id=uid)

    extraction.extract_and_store = AsyncMock(return_value=None)  # type: ignore[method-assign]

    result = await svc.process_recent_messages(
        user_id=uid,
        tenant_id="t",
        project_id="proj4",
        session_id=session_id,
        provider=AsyncMock(),
        model="m",
        threshold=2,
    )
    assert result is None
