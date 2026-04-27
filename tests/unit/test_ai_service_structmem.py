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
    name = "ollama"

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
        OLLAMA_BASE_URL="http://ollama.test",
        OLLAMA_OPENAI_URL="http://ollama.test/v1",
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
    assert system_messages[0] == "Base system prompt"
    assert any("### SYSTEM: PROCEDURAL MEMORY ###" in content for content in system_messages)
    assert any("Answer briefly." in content for content in system_messages)
    assert any("### SYSTEM: CONSOLIDATED MEMORY ###" in content for content in system_messages)


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
