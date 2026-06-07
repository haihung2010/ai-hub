"""Unit tests for AIService StructMem integration."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.core.config import Settings
from app.models.chat import ChatRequest, Message
from app.models.memory import (
    MemoryConsolidationRecord,
    MemoryItemRecord,
    RetrievedMemoryBundle,
)
from app.services.ai_service import AIService
from app.services.history_service import HistoryService
from app.services.user_service import UserService


class _Provider:
    name = "llama_cpp"

    async def complete(
        self,
        messages: list[Message],
        model: str,
        temperature: float,
        options: dict | None = None,
    ) -> str:
        return "ok"


class _MemoryRetrieval:
    def retrieve(self, **_: Any) -> RetrievedMemoryBundle:
        return RetrievedMemoryBundle(
            procedural=[
                MemoryItemRecord(
                    id="p1",
                    episode_id="e1",
                    user_id="u1",
                    tenant_id="default",
                    project_id="vehix",
                    memory_type="procedural",
                    subject=None,
                    predicate=None,
                    object=None,
                    content="Answer briefly.",
                    salience=0.9,
                    valid_from=None,
                    valid_to=None,
                    last_accessed_at=None,
                    created_at="2026-04-25 10:00:00",
                )
            ],
            semantic=[],
            relational=[],
            episodic=[],
            consolidated=[
                MemoryConsolidationRecord(
                    id="c1",
                    user_id="u1",
                    tenant_id="default",
                    project_id="vehix",
                    scope_key="global",
                    source_episode_ids="e1",
                    content="The user manages internal outsourcing projects.",
                    version=1,
                    created_at="2026-04-25 10:00:00",
                    updated_at="2026-04-25 10:00:00",
                )
            ],
        )


class _StructMem:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def process_recent_messages(self, **kwargs: Any) -> str | None:
        self.calls.append(kwargs)
        return "episode-1"


@pytest.fixture
def settings() -> Settings:
    return Settings(
        APP_PORT=8000,
        LOG_LEVEL="WARNING",
        LLAMA_CPP_BASE_URL="http://llama.test",
        LLAMA_CPP_OPENAI_URL="http://llama.test/v1",
        DEFAULT_MODEL="test-model:latest",
        REQUEST_TIMEOUT_SECONDS=5.0,
        MAX_HISTORY_MESSAGES=5,
        API_KEY="test-api-key",
        RATE_LIMIT_PER_MINUTE=5,
        ENABLE_STRUCTMEM=True,
        STRUCTMEM_EXTRACTION_THRESHOLD=2,
        STRUCTMEM_EXTRACTION_MODEL="structmem-test-model",
    )


@pytest.mark.unit
def test_prepare_messages_includes_structmem_blocks(settings: Settings) -> None:
    service = AIService(
        local=_Provider(),
        history=HistoryService(),
        settings=settings,
        users=UserService(),
        memory_retrieval=_MemoryRetrieval(),
    )
    request = ChatRequest(project_id="vehix", user_message="Who am I?")
    bundle = service._load_structmem("u1", "default", "vehix", request.user_message)

    messages, _ = service._prepare_messages_for_request(
        request,
        "Base system prompt",
        [],
        None,
        bundle,
    )

    system_messages = [message.content for message in messages if message.role == "system"]
    assert len(system_messages) == 1
    assert system_messages[0].startswith("Base system prompt")
    assert "### SYSTEM: PROCEDURAL MEMORY ###" in system_messages[0]
    assert "Answer briefly." in system_messages[0]
    assert "### SYSTEM: CONSOLIDATED MEMORY ###" in system_messages[0]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_chat_schedules_structmem_job(settings: Settings) -> None:
    structmem = _StructMem()
    service = AIService(
        local=_Provider(),
        history=HistoryService(),
        settings=settings,
        users=UserService(),
        memory_retrieval=_MemoryRetrieval(),
        structmem=structmem,
    )

    req = ChatRequest(project_id="vehix", tenant_id="default", user_name="Hung", user_message="hello")
    response = await service.chat(req)
    await asyncio.sleep(0)

    assert response.content == "ok"
    assert len(structmem.calls) == 1
    assert structmem.calls[0]["tenant_id"] == "default"
    assert structmem.calls[0]["project_id"] == "vehix"
    assert structmem.calls[0]["model"] == "structmem-test-model"


class _SummaryServiceStub:
    """Captures summarize() invocations so we can assert it is scheduled."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def summarize(self, *args: Any, **kwargs: Any) -> None:
        self.calls.append({"args": args, "kwargs": kwargs})

    def get_latest_summary(self, *args: Any, **kwargs: Any) -> None:
        return None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_chat_schedules_summary_alongside_structmem(settings: Settings) -> None:
    """Regression test for the Rank-4 bug (2026-06-06 health report).

    Prior versions of ``_schedule_memory_jobs`` returned early after scheduling
    StructMem, which silently disabled ``SummaryService`` whenever
    ``ENABLE_STRUCTMEM=true`` and left the ``summaries`` table empty. Both
    services must be scheduled when configured.
    """
    structmem = _StructMem()
    summaries = _SummaryServiceStub()
    service = AIService(
        local=_Provider(),
        history=HistoryService(),
        settings=settings,
        users=UserService(),
        memory_retrieval=_MemoryRetrieval(),
        structmem=structmem,
        summaries=summaries,  # type: ignore[arg-type]
    )

    req = ChatRequest(project_id="vehix", tenant_id="default", user_name="Hung", user_message="hello")
    await service.chat(req)
    # Drain the asyncio task queue so the create_task() calls have a chance to run.
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert len(structmem.calls) == 1, "StructMem should be scheduled"
    assert len(summaries.calls) == 1, (
        "SummaryService must also be scheduled even when StructMem is enabled "
        "— otherwise the `summaries` table never receives rows"
    )
    # summarize() is invoked with positional args: (user_id, project_id, provider,
    # model, threshold, tenant_id, token_threshold)
    args = summaries.calls[0]["args"]
    assert args[0] is not None, "user_id should be passed"
    assert args[1] == "vehix", "project_id should be passed"
    assert args[4] == settings.summary_threshold, "summary_threshold should be passed"
    assert args[5] == "default", "tenant_id should be passed"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_chat_schedules_summary_when_structmem_disabled(settings: Settings) -> None:
    """SummaryService should be scheduled when ``ENABLE_STRUCTMEM=false``."""
    structmem = _StructMem()  # present but should NOT be called
    summaries = _SummaryServiceStub()
    settings_no_structmem = settings.model_copy(update={"enable_structmem": False})
    service = AIService(
        local=_Provider(),
        history=HistoryService(),
        settings=settings_no_structmem,
        users=UserService(),
        memory_retrieval=_MemoryRetrieval(),
        structmem=structmem,
        summaries=summaries,  # type: ignore[arg-type]
    )

    req = ChatRequest(project_id="vehix", tenant_id="default", user_name="Hung", user_message="hello")
    await service.chat(req)
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert structmem.calls == [], "StructMem should NOT be scheduled when ENABLE_STRUCTMEM=false"
    assert len(summaries.calls) == 1, "SummaryService should be scheduled"
