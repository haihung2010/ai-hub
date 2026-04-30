"""Unit tests for SummaryService: CRUD, threshold logic, and upsert."""

from __future__ import annotations
import asyncio
from unittest.mock import AsyncMock

import pytest

from app.core.database import init_db
from app.models.chat import Message
from app.services.history_service import HistoryService
from app.services.summary_service import SummaryService


@pytest.fixture(autouse=True)
def _init_db() -> None:
    init_db()


@pytest.fixture
def history() -> HistoryService:
    return HistoryService()


@pytest.fixture
def summary(history: HistoryService) -> SummaryService:
    return SummaryService(history=history)


@pytest.fixture
def uid() -> str:
    import uuid
    return f"test-{uuid.uuid4().hex[:8]}"


@pytest.mark.unit
def test_get_latest_summary_returns_none_when_empty(
    summary: SummaryService, uid: str
) -> None:
    assert summary.get_latest_summary(uid, "test") is None


@pytest.mark.unit
def test_upsert_creates_and_updates_summary(
    summary: SummaryService, uid: str
) -> None:
    summary._upsert_summary(uid, "test", "First summary")
    assert summary.get_latest_summary(uid, "test") == "First summary"

    summary._upsert_summary(uid, "test", "Updated summary")
    assert summary.get_latest_summary(uid, "test") == "Updated summary"


@pytest.mark.unit
def test_summaries_are_isolated_by_tenant(summary: SummaryService, uid: str) -> None:
    summary._upsert_summary(uid, "stock_prediction", "Stock summary", tenant_id="stock")

    assert summary.get_latest_summary(uid, "stock_prediction", tenant_id="stock") == "Stock summary"
    assert summary.get_latest_summary(uid, "stock_prediction", tenant_id="other") is None


@pytest.mark.unit
def test_count_and_get_unsummarized(
    history: HistoryService, summary: SummaryService, uid: str
) -> None:
    session_id = history.create_session("test", user_id=uid)
    for i in range(5):
        history.save_message(session_id, "user", f"msg {i}", user_id=uid)
        history.save_message(session_id, "assistant", f"reply {i}", user_id=uid)

    count = history.count_unsummarized_messages(uid, "test")
    assert count == 10

    unsummarized = history.get_unsummarized_messages(uid, "test")
    assert len(unsummarized) == 10
    assert unsummarized[0][1].content == "msg 0"


@pytest.mark.unit
def test_mark_messages_summarized(
    history: HistoryService, summary: SummaryService, uid: str
) -> None:
    session_id = history.create_session("test", user_id=uid)
    for i in range(4):
        history.save_message(session_id, "user", f"msg {i}", user_id=uid)

    unsummarized = history.get_unsummarized_messages(uid, "test")
    assert len(unsummarized) == 4
    mid_id = unsummarized[1][0]

    history.mark_messages_summarized(uid, "test", mid_id)

    remaining = history.get_unsummarized_messages(uid, "test")
    assert len(remaining) == 2


@pytest.mark.unit
def test_format_messages(summary: SummaryService) -> None:
    pairs = [
        (1, Message(role="user", content="Hello")),
        (2, Message(role="assistant", content="Hi")),
    ]
    result = summary._format_messages(pairs)
    assert "user: Hello" in result
    assert "assistant: Hi" in result


@pytest.mark.unit
@pytest.mark.asyncio
async def test_summarize_skips_below_threshold(
    history: HistoryService, summary: SummaryService, uid: str
) -> None:
    provider = AsyncMock()
    session_id = history.create_session("proj", user_id=uid)
    history.save_message(session_id, "user", "hi", user_id=uid)
    await summary.summarize(uid, "proj", provider, "model", threshold=10)
    provider.complete.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_summarize_runs_when_token_threshold_exceeded(
    history: HistoryService, summary: SummaryService, uid: str
) -> None:
    provider = AsyncMock()
    provider.complete.return_value = "Token threshold summary."
    session_id = history.create_session("token-proj", user_id=uid)
    history.save_message(session_id, "user", "x" * 200, user_id=uid)
    await summary.summarize(uid, "token-proj", provider, "model", threshold=20, token_threshold=10)
    provider.complete.assert_awaited_once()
    assert summary.get_latest_summary(uid, "token-proj") == "Token threshold summary."


@pytest.mark.unit
@pytest.mark.asyncio
async def test_summarize_calls_provider_and_stores(
    history: HistoryService, summary: SummaryService, uid: str
) -> None:
    provider = AsyncMock()
    provider.complete.return_value = "Conversation summary text."
    session_id = history.create_session("proj2", user_id=uid)
    for i in range(3):
        history.save_message(session_id, "user", f"msg {i}", user_id=uid)
    await summary.summarize(uid, "proj2", provider, "model", threshold=2)
    provider.complete.assert_awaited_once()
    stored = summary.get_latest_summary(uid, "proj2")
    assert stored == "Conversation summary text."
    assert len(history.get_unsummarized_messages(uid, "proj2")) == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_summarize_handles_empty_provider_response(
    history: HistoryService, summary: SummaryService, uid: str
) -> None:
    provider = AsyncMock()
    provider.complete.return_value = "   "
    session_id = history.create_session("proj3", user_id=uid)
    for i in range(3):
        history.save_message(session_id, "user", f"msg {i}", user_id=uid)
    await summary.summarize(uid, "proj3", provider, "model", threshold=2)
    assert summary.get_latest_summary(uid, "proj3") is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_summarize_handles_provider_exception(
    history: HistoryService, summary: SummaryService, uid: str
) -> None:
    provider = AsyncMock()
    provider.complete.side_effect = RuntimeError("LLM down")
    session_id = history.create_session("proj4", user_id=uid)
    for i in range(3):
        history.save_message(session_id, "user", f"msg {i}", user_id=uid)
    await summary.summarize(uid, "proj4", provider, "model", threshold=2)
    assert summary.get_latest_summary(uid, "proj4") is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_summarize_limits_concurrent_provider_calls(history: HistoryService) -> None:
    summary = SummaryService(history=history, max_concurrency=1)
    active = 0
    max_active = 0

    async def complete(*_: object) -> str:
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.01)
        active -= 1
        return "Queued summary."

    provider = AsyncMock()
    provider.complete.side_effect = complete
    user_ids = ["summary-queue-a", "summary-queue-b"]
    for user_id in user_ids:
        session_id = history.create_session("queue-proj", user_id=user_id)
        for i in range(2):
            history.save_message(session_id, "user", f"msg {i}", user_id=user_id)

    await asyncio.gather(
        *(summary.summarize(user_id, "queue-proj", provider, "model", threshold=2) for user_id in user_ids)
    )

    assert provider.complete.await_count == 2
    assert max_active == 1
