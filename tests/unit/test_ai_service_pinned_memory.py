"""Pinned memory integration tests for AIService."""

from __future__ import annotations

from typing import Any

import pytest

from app.core.config import Settings
from app.core.database import init_db
from app.models.chat import ChatRequest, Message
from app.services.ai_service import AIService
from app.models.knowledge import KnowledgeCardCreate
from app.services.history_service import HistoryService
from app.services.knowledge_ingestion_service import KnowledgeIngestionService
from app.services.knowledge_retrieval_service import KnowledgeRetrievalService
from app.services.pinned_memory_service import PinnedMemoryService
from app.services.user_service import UserService


class _CaptureProvider:
    name = "llama_cpp"

    def __init__(self) -> None:
        self.messages: list[Message] = []

    async def complete(
        self,
        messages: list[Message],
        model: str,
        temperature: float,
        options: dict | None = None,
    ) -> str:
        self.messages = messages
        return "ok"


@pytest.fixture
def service_settings() -> Settings:
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
        ALLOWED_HOSTS=["testserver"],
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_explicit_remember_request_creates_pinned_memory(service_settings: Settings) -> None:
    init_db()
    provider = _CaptureProvider()
    pinned = PinnedMemoryService()
    service = AIService(
        local=provider,
        history=HistoryService(),
        settings=service_settings,
        users=UserService(),
        pinned_memory=pinned,
    )

    await service.chat(
        ChatRequest(
            project_id="vehix",
            tenant_id="default",
            user_name="hung",
            user_message="hãy nhớ project Vehix dùng MQTT cho telemetry",
        )
    )

    user_id = UserService().find_by_name("hung", "default").id
    prompt = pinned.format_for_prompt("default", "vehix", user_id)
    assert "project Vehix dùng MQTT cho telemetry" in prompt


@pytest.mark.unit
def test_search_context_stays_before_chat_history(service_settings: Settings) -> None:
    service = AIService(
        local=_CaptureProvider(),
        history=HistoryService(),
        settings=service_settings,
        users=UserService(),
    )
    messages = [
        Message(role="system", content="base system"),
        Message(role="user", content="hello"),
        Message(role="assistant", content="hi"),
        Message(role="user", content="hom nay la ngay bao nhieu"),
    ]

    result = service._inject_search_context(messages, "web context")

    assert result[0].role == "system"
    assert "web context" in result[0].content
    assert "base system" in result[0].content
    assert [message.role for message in result] == ["system", "user", "assistant", "user"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_pinned_memory_is_injected_into_prompt(service_settings: Settings) -> None:
    init_db()
    users = UserService()
    user = users.get_or_create_user("hung", "default")
    pinned = PinnedMemoryService()
    pinned.upsert_memory("default", "vehix", user.id, "mqtt", "Vehix devices use MQTT")
    provider = _CaptureProvider()
    service = AIService(
        local=provider,
        history=HistoryService(),
        settings=service_settings,
        users=users,
        pinned_memory=pinned,
    )

    await service.chat(
        ChatRequest(
            project_id="vehix",
            tenant_id="default",
            user_name="hung",
            user_message="Vehix dùng giao thức gì?",
        )
    )

    system_text = "\n".join(message.content for message in provider.messages if message.role == "system")
    assert "### SYSTEM: PINNED MEMORY ###" in system_text
    assert "Vehix devices use MQTT" in system_text


@pytest.mark.unit
@pytest.mark.asyncio
async def test_project_knowledge_is_injected_into_prompt(service_settings: Settings) -> None:
    init_db()
    ingestion = KnowledgeIngestionService()
    ingestion.create_card(
        KnowledgeCardCreate(
            project_id="test",
            knowledge_domain="customer_faq",
            title="Refund FAQ",
            summary="Refund rules",
            content="Customers can request refund within thirty days with order code.",
            tags=["refund"],
        )
    )
    provider = _CaptureProvider()
    service = AIService(
        local=provider,
        history=HistoryService(),
        settings=service_settings,
        users=UserService(),
        knowledge_retrieval=KnowledgeRetrievalService(),
    )

    await service.chat(
        ChatRequest(
            project_id="test",
            tenant_id="default",
            user_message="tra cuu chinh sach refund",  # matches RAG patterns
        )
    )

    system_text = "\n".join(message.content for message in provider.messages if message.role == "system")
    assert "### SYSTEM: PROJECT KNOWLEDGE CONTEXT ###" in system_text
    assert "Refund FAQ" in system_text
    assert "thirty days" in system_text


@pytest.mark.unit
@pytest.mark.asyncio
async def test_project_knowledge_does_not_cross_project(service_settings: Settings) -> None:
    init_db()
    ingestion = KnowledgeIngestionService()
    ingestion.create_card(
        KnowledgeCardCreate(
            tenant_id="tenant-a",
            project_id="test",
            knowledge_domain="customer_faq",
            title="Refund FAQ",
            content="Refund requires tenant A approval.",
        )
    )
    provider = _CaptureProvider()
    service = AIService(
        local=provider,
        history=HistoryService(),
        settings=service_settings,
        users=UserService(),
        knowledge_retrieval=KnowledgeRetrievalService(),
    )

    await service.chat(
        ChatRequest(
            project_id="test",
            tenant_id="tenant-b",
            user_message="How does refund work?",
        )
    )

    system_text = "\n".join(message.content for message in provider.messages if message.role == "system")
    assert "### SYSTEM: PROJECT KNOWLEDGE CONTEXT ###" not in system_text
