"""Verify memory extraction runs in parallel with the streaming reply.

Task 1 (2026-06-14): the rolling summary + structmem extraction was
previously kicked off AFTER the SSE stream finished, so the user paid
the extraction overhead on top of the model reply. We now save the
user message and schedule extraction BEFORE the stream starts, so the
LLM call for extraction runs concurrently with the model stream.

These tests pin that behaviour:
  * user message is persisted before the first stream chunk is yielded
  * _schedule_memory_jobs runs before the first stream chunk
  * extraction is fire-and-forget — a failure in the extraction task
    does NOT prevent the stream from completing
  * extraction is NOT run twice for the same turn (idempotency
    preserved by mark_messages_summarized inside the services)
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.config import Settings
from app.models.chat import ChatRequest


# ── Helpers ──────────────────────────────────────────────────────────────


def _make_settings(**overrides) -> Settings:
    """Build a Settings instance with the bare minimum to construct
    AIService. We never reach a real provider — all providers are
    mocked in the test bodies."""
    base = dict(
        APP_PORT=8000,
        LOG_LEVEL="WARNING",
        LLAMA_CPP_BASE_URL="http://llama.test",
        LLAMA_CPP_OPENAI_URL="http://llama.test/v1",
        DEFAULT_MODEL="test-model",
        LITE_MODEL="test-lite",
        REQUEST_TIMEOUT_SECONDS=5.0,
        MAX_HISTORY_MESSAGES=5,
        LITE_MAX_HISTORY_MESSAGES=5,
        API_KEY="test-key-aaaaaaaaaa",
        RATE_LIMIT_PER_MINUTE=5,
        ALLOWED_HOSTS=["testserver"],
        BACKGROUND_LLAMA_CPP_ENABLED="false",
        ENABLE_KNOWLEDGE_RAG=False,
    )
    base.update(overrides)
    return Settings(**base)


def _make_request(**overrides) -> ChatRequest:
    base = {
        "user_name": "parallel-mem-user",
        "project_id": "parallel_mem_test",
        "user_message": "I like red shoes size 42",
        "model_mode": "lite",
        "stream": True,
    }
    base.update(overrides)
    return ChatRequest(**base)


class _FakeProvider:
    """Minimal stand-in for ChatProvider. The stream_complete coroutine
    yields ``chunks`` one at a time with a small sleep between them so
    tests can interleave a check between chunks."""

    name = "fake"

    def __init__(self, chunks: list[str], chunk_delay: float = 0.01) -> None:
        self._chunks = chunks
        self._delay = chunk_delay
        self.stream_calls = 0

    async def stream_complete(self, messages, model, temperature, options):
        self.stream_calls += 1
        for c in self._chunks:
            await asyncio.sleep(self._delay)
            yield c

    async def complete(self, messages, model, temperature, options):
        return "ok"

    def healthy(self) -> bool:
        return True


class _StubSummaryService:
    """Stand-in for SummaryService with controllable async behavior."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.started = asyncio.Event()
        self.finished = asyncio.Event()
        self.raise_on_call: BaseException | None = None

    async def summarize(self, *args, **kwargs) -> None:
        self.calls.append({"args": args, "kwargs": kwargs})
        self.started.set()
        try:
            if self.raise_on_call is not None:
                raise self.raise_on_call
            await asyncio.sleep(0)
        finally:
            self.finished.set()

    def get_latest_summary(self, *args, **kwargs):
        return None


class _StubStructMemService:
    """Stand-in for StructMemService with controllable async behavior."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.started = asyncio.Event()
        self.finished = asyncio.Event()
        self.raise_on_call: BaseException | None = None

    async def process_recent_messages(self, **kwargs) -> str | None:
        self.calls.append(kwargs)
        self.started.set()
        try:
            if self.raise_on_call is not None:
                raise self.raise_on_call
            await asyncio.sleep(0)
        finally:
            self.finished.set()
        return "episode-1"


def _build_service(provider, settings, summaries=None, structmem=None):
    """Construct an AIService with everything that chat_stream touches
    stubbed out, except the provider and memory services under test."""
    from app.services.ai_service import AIService

    history = MagicMock()
    history.get_session_messages = MagicMock(return_value=[])
    history.get_unsummarized_messages = MagicMock(return_value=[])

    pinned = MagicMock()
    pinned.format_for_prompt = MagicMock(return_value=None)
    pinned.remember_from_message = MagicMock()

    user_service = MagicMock()
    user_service.get_or_create_user = MagicMock(
        return_value=SimpleNamespace(id="u-1", tenant_id="default", name="parallel-mem-user")
    )

    usage = MagicMock()
    usage.record = MagicMock()

    if summaries is None:
        summaries = _StubSummaryService()
    if structmem is None:
        structmem = _StubStructMemService()

    return AIService(
        local=provider,
        history=history,
        settings=settings,
        users=user_service,
        summaries=summaries,
        structmem=structmem,
        pinned_memory=pinned,
        usage=usage,
    )


_PROMPT = SimpleNamespace(system_prompt="", model="m", temperature=0.7, enable_search=False)


# ── Tests ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_user_message_saved_before_stream_yields_first_chunk() -> None:
    """The user message must be in the DB before the first chunk is
    yielded, so the background extraction task (scheduled immediately
    after) sees the new turn."""
    settings = _make_settings()
    provider = _FakeProvider(["Hello", " world"], chunk_delay=0.02)
    service = _build_service(provider, settings)

    save_user_message_calls: list[float] = []
    original_save_user = service._save_user_message

    def _record_save_user(req, session_id, user_id):
        save_user_message_calls.append(time.perf_counter())
        return original_save_user(req, session_id, user_id)

    chunks_yielded_at: list[float] = []
    with patch("app.prompts.loader.load_prompt", return_value=_PROMPT), \
         patch.object(service, "_save_user_message", side_effect=_record_save_user):
        async for ev in service.chat_stream(_make_request()):
            if ev.get("type") == "chunk":
                chunks_yielded_at.append(time.perf_counter())

    assert chunks_yielded_at, "stream produced no chunks"
    assert save_user_message_calls, "user message was never saved"
    # save_user_message runs BEFORE any "chunk" event is yielded. Even
    # with the test's small sleep before each chunk, save must be
    # recorded first.
    assert save_user_message_calls[0] <= chunks_yielded_at[0]


@pytest.mark.asyncio
async def test_schedule_memory_jobs_runs_before_first_chunk_yielded() -> None:
    """The extraction scheduling must happen before the first stream
    chunk is yielded, so the LLM call for extraction runs in parallel
    with the model stream."""
    settings = _make_settings()
    provider = _FakeProvider(["a", "b", "c"], chunk_delay=0.02)
    service = _build_service(provider, settings)

    schedule_called_at: list[float] = []
    original_schedule = service._schedule_memory_jobs

    def _record_schedule(*args, **kwargs):
        schedule_called_at.append(time.perf_counter())
        return original_schedule(*args, **kwargs)

    chunks_yielded_at: list[float] = []
    with patch("app.prompts.loader.load_prompt", return_value=_PROMPT), \
         patch.object(service, "_schedule_memory_jobs", side_effect=_record_schedule):
        async for ev in service.chat_stream(_make_request()):
            if ev.get("type") == "chunk":
                chunks_yielded_at.append(time.perf_counter())

    assert schedule_called_at, "_schedule_memory_jobs was never called"
    assert chunks_yielded_at, "no chunks were yielded"
    assert schedule_called_at[0] <= chunks_yielded_at[0]


@pytest.mark.asyncio
async def test_structmem_task_is_kicked_off_before_first_chunk() -> None:
    """The structmem coroutine begins executing BEFORE the first chunk
    is yielded. Proves extraction runs in parallel with the stream —
    not sequentially after."""
    settings = _make_settings(ENABLE_STRUCTMEM=True)
    provider = _FakeProvider(["x", "y", "z", "w"], chunk_delay=0.05)
    structmem = _StubStructMemService()
    # Make the extraction long enough to clearly outlast the first chunk.
    original_extract = structmem.process_recent_messages

    async def slow_extract(**kwargs):
        # Mark started synchronously, then sleep to ensure stream
        # outpaces us.
        structmem.calls.append(kwargs)
        structmem.started.set()
        await asyncio.sleep(0.3)
        structmem.finished.set()
        return "episode-1"

    structmem.process_recent_messages = slow_extract  # type: ignore[method-assign]
    service = _build_service(provider, settings, structmem=structmem)

    seen_started_before_first_chunk = False
    first_chunk_seen = False
    with patch("app.prompts.loader.load_prompt", return_value=_PROMPT):
        async for ev in service.chat_stream(_make_request()):
            if ev.get("type") == "chunk" and not first_chunk_seen:
                first_chunk_seen = True
                # The structmem task was scheduled just before the
                # stream started; by the time the first chunk arrives
                # the coroutine is mid-await on asyncio.sleep(0.3) and
                # has set its started event.
                seen_started_before_first_chunk = structmem.started.is_set()
                # Don't break — keep consuming so the stream finishes.

    # Drain the rest of the stream
    try:
        await asyncio.wait_for(structmem.finished.wait(), timeout=1.0)
    except asyncio.TimeoutError:
        pytest.fail("structmem extraction did not complete")

    assert first_chunk_seen, "no first chunk was seen"
    assert seen_started_before_first_chunk, (
        "structmem extraction did not start before first stream chunk — "
        "it is running sequentially after the stream, not in parallel"
    )


@pytest.mark.asyncio
async def test_summary_task_is_kicked_off_before_first_chunk() -> None:
    """Same as the structmem test, but for SummaryService."""
    settings = _make_settings()
    provider = _FakeProvider(["x", "y", "z", "w"], chunk_delay=0.05)
    summaries = _StubSummaryService()

    original_summarize = summaries.summarize

    async def slow_summarize(*args, **kwargs):
        summaries.calls.append({"args": args, "kwargs": kwargs})
        summaries.started.set()
        await asyncio.sleep(0.3)
        summaries.finished.set()

    summaries.summarize = slow_summarize  # type: ignore[method-assign]
    service = _build_service(provider, settings, summaries=summaries)

    seen_started_before_first_chunk = False
    first_chunk_seen = False
    with patch("app.prompts.loader.load_prompt", return_value=_PROMPT):
        async for ev in service.chat_stream(_make_request()):
            if ev.get("type") == "chunk" and not first_chunk_seen:
                first_chunk_seen = True
                seen_started_before_first_chunk = summaries.started.is_set()

    try:
        await asyncio.wait_for(summaries.finished.wait(), timeout=1.0)
    except asyncio.TimeoutError:
        pytest.fail("summary extraction did not complete")

    assert first_chunk_seen, "no first chunk was seen"
    assert seen_started_before_first_chunk, (
        "summary extraction did not start before first stream chunk"
    )


@pytest.mark.asyncio
async def test_extraction_failure_does_not_break_stream() -> None:
    """Fire-and-forget contract: if the background extraction task
    raises, the stream must still complete and the error must be
    swallowed (logged but not raised to the caller)."""
    settings = _make_settings(ENABLE_STRUCTMEM=True)
    provider = _FakeProvider(["hello"], chunk_delay=0.0)
    structmem = _StubStructMemService()
    structmem.raise_on_call = RuntimeError("extraction kaboom")
    service = _build_service(provider, settings, structmem=structmem)

    events = []
    with patch("app.prompts.loader.load_prompt", return_value=_PROMPT):
        async for ev in service.chat_stream(_make_request()):
            events.append(ev)

    types = [e.get("type") for e in events]
    assert "start" in types
    assert "chunk" in types
    assert "done" in types
    # Stream completed normally despite the extraction error.
    try:
        await asyncio.wait_for(structmem.finished.wait(), timeout=1.0)
    except asyncio.TimeoutError:
        pytest.fail("structmem extraction did not complete (failing test)")


@pytest.mark.asyncio
async def test_assistant_message_saved_after_stream() -> None:
    """The assistant message must be persisted AFTER the stream
    finishes (it doesn't exist until the model has produced it). User
    message is saved BEFORE — that was the whole point of the split."""
    settings = _make_settings()
    provider = _FakeProvider(["a", "b"], chunk_delay=0.0)
    service = _build_service(provider, settings)

    saved_user_at: list[float] = []
    saved_assistant_at: list[float] = []
    done_yielded_at: list[float] = []
    original_save_user = service._save_user_message
    original_save_assistant = service._save_assistant_message

    def _record_user(req, session_id, user_id):
        saved_user_at.append(time.perf_counter())
        return original_save_user(req, session_id, user_id)

    def _record_assistant(req, session_id, user_id, content):
        saved_assistant_at.append(time.perf_counter())
        return original_save_assistant(req, session_id, user_id, content)

    with patch("app.prompts.loader.load_prompt", return_value=_PROMPT), \
         patch.object(service, "_save_user_message", side_effect=_record_user), \
         patch.object(service, "_save_assistant_message", side_effect=_record_assistant):
        async for ev in service.chat_stream(_make_request()):
            if ev.get("type") == "done":
                done_yielded_at.append(time.perf_counter())

    assert saved_user_at, "user message was never saved"
    assert saved_assistant_at, "assistant message was never saved"
    assert done_yielded_at, "no done event yielded"
    # User save happens before "done" (before the stream even starts);
    # assistant save happens after "done" (after the stream finishes).
    assert saved_user_at[0] < done_yielded_at[0]
    assert saved_assistant_at[0] > done_yielded_at[0]


@pytest.mark.asyncio
async def test_no_double_extraction_for_same_turn() -> None:
    """Idempotency: each extraction service is called at most once per
    chat_stream invocation. Repeated turns rely on
    mark_messages_summarized inside the services for safety."""
    settings = _make_settings(ENABLE_STRUCTMEM=True)
    provider = _FakeProvider(["x"], chunk_delay=0.0)
    structmem = _StubStructMemService()
    summaries = _StubSummaryService()
    service = _build_service(provider, settings, summaries=summaries, structmem=structmem)

    with patch("app.prompts.loader.load_prompt", return_value=_PROMPT):
        async for _ in service.chat_stream(_make_request()):
            pass

    # Give the tasks a moment to actually run.
    try:
        await asyncio.wait_for(structmem.finished.wait(), timeout=1.0)
    except asyncio.TimeoutError:
        pass
    try:
        await asyncio.wait_for(summaries.finished.wait(), timeout=1.0)
    except asyncio.TimeoutError:
        pass

    # Each extraction service should be called at most once per turn.
    assert len(structmem.calls) <= 1
    assert len(summaries.calls) <= 1


@pytest.mark.asyncio
async def test_stream_completes_faster_when_extraction_runs_in_parallel() -> None:
    """Wall-clock latency to the user should be lower than the sum of
    (model stream time + extraction time) when extraction runs in
    parallel. We measure: with a 100ms extraction and 50ms stream,
    total wall-clock should be ~100ms (max) rather than 150ms (sum)."""
    settings = _make_settings()
    # Stream that takes ~50ms total to produce (5 chunks × 10ms).
    provider = _FakeProvider(["a", "b", "c", "d", "e"], chunk_delay=0.01)
    # Summarizer that takes 100ms (in parallel with the stream).
    summaries = _StubSummaryService()
    original_summarize = summaries.summarize

    async def long_summarize(*args, **kwargs):
        summaries.calls.append({"args": args, "kwargs": kwargs})
        summaries.started.set()
        await asyncio.sleep(0.1)
        summaries.finished.set()

    summaries.summarize = long_summarize  # type: ignore[method-assign]
    service = _build_service(provider, settings, summaries=summaries)

    started = time.perf_counter()
    with patch("app.prompts.loader.load_prompt", return_value=_PROMPT):
        async for _ in service.chat_stream(_make_request()):
            pass
    stream_wallclock = time.perf_counter() - started

    # If extraction ran serially AFTER the stream, total would be
    # ~150ms (50ms stream + 100ms extraction). Parallel means
    # ~100ms (max of the two). Allow some slack for scheduling.
    assert stream_wallclock < 0.14, (
        f"stream took {stream_wallclock*1000:.0f}ms — extraction "
        "is not running in parallel (expected <140ms with 50ms stream "
        "+ 100ms extraction in parallel)"
    )
