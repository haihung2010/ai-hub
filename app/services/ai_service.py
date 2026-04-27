"""Orchestrates prompt loading, provider selection, and history trimming."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import unicodedata

from app.core.config import Settings
from app.core.errors import OllamaUnavailable
from app.models.chat import ChatRequest, ChatResponse, Message
from app.models.memory import MemoryConsolidationRecord, MemoryItemRecord, RetrievedMemoryBundle
from app.prompts.loader import load_prompt
from app.services.history_service import HistoryService
from app.services.memory_retrieval_service import MemoryRetrievalService
from app.services.providers.base import ChatProvider
from app.services.structmem_service import StructMemService
from app.services.summary_service import SummaryService
from app.services.tools.web_search_service import WebSearchService
from app.services.user_service import UserService

logger = logging.getLogger(__name__)


class AIService:
    def __init__(
        self,
        local: ChatProvider,
        history: HistoryService,
        settings: Settings,
        users: UserService,
        summaries: SummaryService | None = None,
        web_search: WebSearchService | None = None,
        memory_retrieval: MemoryRetrievalService | None = None,
        structmem: StructMemService | None = None,
    ) -> None:
        self._local = local
        self._history = history
        self._settings = settings
        self._users = users
        self._summaries = summaries
        self._web_search = web_search
        self._memory_retrieval = memory_retrieval
        self._structmem = structmem
        self._gpu_lock = asyncio.Semaphore(settings.gpu_concurrency)
        logger.info(
            "AIService initialized with gpu_concurrency=%s", settings.gpu_concurrency
        )

    @staticmethod
    def _format_memory_items(title: str, items: list[MemoryItemRecord]) -> str | None:
        if not items:
            return None
        lines = [f"### SYSTEM: {title} ###"]
        for item in items:
            lines.append(f"- {item.content}")
        return "\n".join(lines)

    @staticmethod
    def _format_consolidated_memories(items: list[MemoryConsolidationRecord]) -> str | None:
        if not items:
            return None
        lines = ["### SYSTEM: CONSOLIDATED MEMORY ###"]
        for item in items:
            lines.append(f"- {item.content}")
        return "\n".join(lines)

    def _load_structmem(
        self,
        user_id: str | None,
        tenant_id: str,
        project_id: str,
        query: str,
    ) -> RetrievedMemoryBundle | None:
        if not self._settings.enable_structmem or not self._memory_retrieval:
            return None
        return self._memory_retrieval.retrieve(
            user_id=user_id,
            tenant_id=tenant_id,
            project_id=project_id,
            query=query,
            max_procedural=self._settings.structmem_max_procedural,
            max_semantic=self._settings.structmem_max_semantic,
            max_relational=self._settings.structmem_max_relational,
            max_episodic=self._settings.structmem_max_episodic,
            max_consolidated=self._settings.structmem_max_consolidated,
        )

    def _build_structmem_blocks(self, bundle: RetrievedMemoryBundle | None) -> list[str]:
        if not bundle:
            return []
        blocks = [
            self._format_memory_items("PROCEDURAL MEMORY", bundle.procedural),
            self._format_memory_items("SEMANTIC MEMORY", bundle.semantic),
            self._format_memory_items("RELATIONAL MEMORY", bundle.relational),
            self._format_memory_items("EPISODIC MEMORY", bundle.episodic),
            self._format_consolidated_memories(bundle.consolidated),
        ]
        return [block for block in blocks if block]

    def _schedule_memory_jobs(
        self,
        user_id: str | None,
        tenant_id: str,
        project_id: str,
        session_id: str,
        provider: ChatProvider,
    ) -> None:
        if self._settings.enable_structmem and user_id and self._structmem:
            asyncio.create_task(
                self._structmem.process_recent_messages(
                    user_id=user_id,
                    tenant_id=tenant_id,
                    project_id=project_id,
                    session_id=session_id,
                    provider=provider,
                    model=self._settings.structmem_extraction_model,
                    threshold=self._settings.structmem_extraction_threshold,
                )
            )
            return
        if user_id and self._summaries:
            asyncio.create_task(
                self._summaries.summarize(
                    user_id,
                    project_id,
                    provider,
                    self._settings.summary_model,
                    self._settings.summary_threshold,
                )
            )

    @staticmethod
    def _effective_history_cap(settings: Settings, model_mode: str) -> int:
        return settings.lite_max_history_messages if model_mode == "lite" else settings.max_history_messages

    @staticmethod
    def _strip_diacritics(text: str) -> str:
        return unicodedata.normalize("NFD", text).encode("ascii", "ignore").decode("ascii").lower()

    def _should_web_search(self, text: str) -> bool:
        # Normalize Vietnamese diacritics so "giá vàng" matches pattern "gia vang"
        normalized = self._strip_diacritics(text)
        patterns = [
            r"\b(tin|news|moi|latest|cap nhat|hot)\b",
            r"\b(hom nay|today|hien tai|current|bay gio|now|ngay may|ngay bao nhieu)\b",
            r"\b(gia|price|ti gia|rate|vang|gold|btc|bitcoin|crypto|chung khoan|stock)\b",
            r"\b(ai|la ai|who|chu tich|tong bi thu|thu tuong|lanh dao)\b",
            r"\b(o dau|dau|where|thoi tiet|weather|nhiet do)\b",
            r"\b(search|tim|tra|google|web|mang|internet)\b",
            r"\b(bao nhieu|may|how many|how much|the nao|how)\b",
        ]
        return any(re.search(pattern, normalized) for pattern in patterns) or "?" in text

    def _build_search_context(self, query: str) -> tuple[str | None, list[str]]:
        if not self._web_search or not self._settings.enable_web_search_tool:
            return None, []

        safe_query = query.strip()[:300]
        try:
            results = self._web_search.search(
                safe_query,
                max_results=self._settings.web_search_max_results,
            )
        except Exception:
            logger.exception("Web search failed query=%s", safe_query)
            return None, []

        if not results:
            return None, []

        payload = json.dumps(results, ensure_ascii=False)
        return (
            "### SYSTEM: CURRENT REAL-TIME KNOWLEDGE FROM WEB ###\n"
            "The following search results contain the most up-to-date information. "
            "You MUST use this data to answer accurately, even if it contradicts your internal knowledge. "
            "Always cite the source URLs in your response.\n\n"
            f"{payload}",
            [item["url"] for item in results],
        )

    def _append_sources_if_missing(self, content: str, source_urls: list[str]) -> str:
        if not source_urls:
            return content
        # Check if model already cited sources (heuristic)
        if "http" in content:
            return content

        lines = ["", "Sources:"]
        for url in source_urls[:3]:
            lines.append(f"- {url}")
        return content.rstrip() + "\n" + "\n".join(lines)

    def _inject_search_context(self, messages: list[Message], context: str | None) -> list[Message]:
        if not context:
            return messages

        # Injection strategy: Insert as a separate system message right before the user's current query
        # This makes it highly likely the model will attend to it.
        new_messages = list(messages)
        user_msg = new_messages.pop()
        new_messages.append(Message(role="system", content=context))
        new_messages.append(user_msg)
        return new_messages

    def _select_temperature(self, req: ChatRequest, prompt_temperature: float) -> float:
        if req.model_mode == "normal":
            return min(prompt_temperature, 0.4)
        return prompt_temperature

    def _select_model(self, req: ChatRequest, prompt_model: str) -> tuple[str, int]:
        if req.model_mode == "lite":
            return self._settings.lite_model, self._settings.lite_num_ctx
        return prompt_model or self._settings.default_model, self._settings.default_num_ctx

    def _prepare_messages_for_request(
        self,
        req: ChatRequest,
        prompt_system: str,
        combined_history: list[Message],
        summary: str | None,
        memory_bundle: RetrievedMemoryBundle | None,
    ) -> tuple[list[Message], list[str]]:
        messages = self._assemble(
            prompt_system,
            combined_history,
            req.user_message,
            req.images if req.model_mode == "lite" else None,
            summary,
            self._build_structmem_blocks(memory_bundle),
            self._effective_history_cap(self._settings, req.model_mode),
        )

        source_urls: list[str] = []
        if req.enable_search and self._settings.enable_web_search_tool:
            logger.info("Triggering web search for: %s", req.user_message)
            search_context, source_urls = self._build_search_context(req.user_message)
            if search_context:
                logger.info("Search context injected with %d results", len(source_urls))
                messages = self._inject_search_context(messages, search_context)
            else:
                logger.info("Search triggered but no results found")

        return messages, source_urls

    def _resolve_user(self, req: ChatRequest) -> str | None:
        if req.user_name is None:
            return None
        user = self._users.get_or_create_user(req.user_name, req.tenant_id)
        return user.id

    def _load_history(self, req: ChatRequest, session_id: str) -> list[Message]:
        limit = self._settings.lite_max_history_messages if req.model_mode == "lite" else self._settings.max_history_messages
        return self._history.get_session_messages(session_id, tenant_id=req.tenant_id, limit=limit) if not req.history else req.history

    def _load_summary(self, user_id: str | None, project_id: str) -> str | None:
        return self._summaries.get_latest_summary(user_id, project_id) if user_id and self._summaries else None

    def _save_chat_messages(self, req: ChatRequest, session_id: str, user_id: str | None, content: str) -> None:
        self._history.save_message(session_id, "user", req.user_message, tenant_id=req.tenant_id, user_id=user_id)
        self._history.save_message(session_id, "assistant", content, tenant_id=req.tenant_id, user_id=user_id)

    def _resolve_session(self, req: ChatRequest, user_id: str | None) -> str:
        return req.session_id if req.session_id else self._history.create_session(req.project_id, user_id=user_id)

    def _assemble(
        self,
        system: str,
        history: list[Message],
        user_message: str,
        images: list[str] | None,
        summary: str | None,
        memory_blocks: list[str],
        history_cap: int,
    ) -> list[Message]:
        trimmed = history[-history_cap:] if history_cap > 0 else []
        parts = [Message(role="system", content=system)]
        for block in memory_blocks:
            parts.append(Message(role="system", content=block))
        if summary and not self._settings.enable_structmem:
            parts.append(Message(role="system", content=f"Conversation summary so far:\n{summary}"))
        parts.extend(trimmed)
        parts.append(Message(role="user", content=user_message, images=images))
        return parts

    async def _complete(self, provider: ChatProvider, messages: list[Message], model: str, temperature: float, options: dict | None = None) -> str:
        async with self._gpu_lock:
            # Full prompt logging for debugging
            logger.info("Prompt Context: %s", json.dumps([m.model_dump() for m in messages], ensure_ascii=False))
            return await provider.complete(messages, model, temperature, options)

    async def chat(self, req: ChatRequest) -> ChatResponse:
        user_id = self._resolve_user(req)
        session_id = self._resolve_session(req, user_id)
        combined_history = self._load_history(req, session_id)
        summary = self._load_summary(user_id, req.project_id)
        memory_bundle = self._load_structmem(user_id, req.tenant_id, req.project_id, req.user_message)
        prompt = load_prompt(req.project_id)
        model, num_ctx = self._select_model(req, prompt.model)
        temperature = self._select_temperature(req, prompt.temperature)

        messages, source_urls = self._prepare_messages_for_request(
            req,
            prompt.system_prompt,
            combined_history,
            summary,
            memory_bundle,
        )
        provider = self._local
        options = {"num_ctx": num_ctx}

        try:
            content = await self._complete(provider, messages, model, temperature, options)
            content = self._append_sources_if_missing(content, source_urls)
            return ChatResponse(project_id=req.project_id, session_id=session_id, model=model, provider=provider.name, content=content, user_id=user_id)
        except OllamaUnavailable:
            logger.error("ollama unavailable")
            raise
        finally:
            self._save_chat_messages(req, session_id, user_id, content if 'content' in locals() else "")
            self._schedule_memory_jobs(user_id, req.tenant_id, req.project_id, session_id, provider)
