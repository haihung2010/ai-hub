"""OpenRouter routing tests for AIService."""

from __future__ import annotations

import asyncio

import pytest

from app.core.config import Settings
from app.core.database import init_db
from app.core.errors import UpstreamError, UpstreamTimeout
from app.models.chat import ChatRequest, Message
from app.services.ai_service import AIService
from app.services.history_service import HistoryService
from app.services.user_service import UserService


class _Provider:
    def __init__(self, name: str, content: str = "ok") -> None:
        self.name = name
        self.content = content
        self.calls = 0
        self.stream_calls = 0
        self.models: list[str] = []
        self.messages: list[list[Message]] = []
        self.options: list[dict | None] = []

    async def complete(
        self,
        messages: list[Message],
        model: str,
        temperature: float,
        options: dict | None = None,
    ) -> str:
        self.calls += 1
        self.models.append(model)
        self.messages.append(messages)
        self.options.append(options)
        return self.content

    async def stream_complete(
        self,
        messages: list[Message],
        model: str,
        temperature: float,
        options: dict | None = None,
    ):
        self.stream_calls += 1
        self.models.append(model)
        self.messages.append(messages)
        self.options.append(options)
        yield self.content


class _SearchService:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def search(self, query: str, max_results: int = 5) -> list[dict[str, str]]:
        self.queries.append(query)
        return [{"title": "Example", "url": "https://example.com/news", "snippet": "fresh result"}]


class _LockedButNonBlockingSemaphore:
    def locked(self) -> bool:
        return True

    async def acquire(self) -> bool:
        return True

    def release(self) -> None:
        return None

    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None


class _NeverAvailableSemaphore:
    def locked(self) -> bool:
        return False

    async def acquire(self) -> bool:
        await asyncio.Event().wait()
        return True

    def release(self) -> None:
        return None


class _SlowProvider(_Provider):
    async def complete(
        self,
        messages: list[Message],
        model: str,
        temperature: float,
        options: dict | None = None,
    ) -> str:
        await asyncio.Event().wait()
        return self.content


@pytest.fixture
def openrouter_settings() -> Settings:
    return Settings(
        APP_PORT=8000,
        LOG_LEVEL="WARNING",
        LLAMA_CPP_BASE_URL="http://llama.test",
        LLAMA_CPP_OPENAI_URL="http://llama.test/v1",
        DEFAULT_MODEL="local-quality",
        LITE_MODEL="local-lite",
        OPENROUTER_ENABLED=True,
        OPENROUTER_API_KEY="test-key",
        OPENROUTER_MODEL="openrouter/free-model",
        OPENROUTER_ALLOWED_PROJECTS=["test", "doden"],
        OPENROUTER_DENIED_PROJECTS=["vehix"],
        EXTERNAL_LLM_DEFAULT_ALLOWED=False,
        REQUEST_TIMEOUT_SECONDS=5.0,
        MAX_HISTORY_MESSAGES=5,
        API_KEY="***",
        RATE_LIMIT_PER_MINUTE=5,
        ALLOWED_HOSTS=["testserver"],
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_local_model_modes_select_lite_by_default_and_normal_model(openrouter_settings: Settings) -> None:
    init_db()
    local = _Provider("llama_cpp", "local")
    service = AIService(
        local=local,
        cloud=_Provider("openrouter", "cloud"),
        history=HistoryService(),
        settings=openrouter_settings,
        users=UserService(),
    )

    default_response = await service.chat(
        ChatRequest(
            project_id="test",
            tenant_id="default",
            user_name="hung-default",
            user_message="hello default",
        )
    )
    normal_response = await service.chat(
        ChatRequest(
            project_id="test",
            tenant_id="default",
            user_name="hung-normal",
            user_message="hello normal",
            model_mode="normal",
        )
    )

    assert default_response.model == "local-lite"
    assert normal_response.model == "local-quality"
    assert local.models == ["local-lite", "local-quality"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_memory_check_uses_full_session_history(openrouter_settings: Settings) -> None:
    init_db()
    history = HistoryService()
    users = UserService()
    service = AIService(
        local=_Provider("llama_cpp", "local"),
        cloud=_Provider("openrouter", "cloud"),
        history=history,
        settings=openrouter_settings,
        users=users,
    )
    user_id = users.get_or_create_user("hung-memory", "default").id
    session_id = history.create_session("test", user_id=user_id, tenant_id="default")
    for index in range(12):
        history.save_message(session_id, "user", f"question {index}", tenant_id="default", user_id=user_id)
        history.save_message(session_id, "assistant", f"answer {index}", tenant_id="default", user_id=user_id)

    response = await service.chat(
        ChatRequest(
            project_id="test",
            tenant_id="default",
            user_name="hung-memory",
            session_id=session_id,
            user_message="Đây là bài kiểm tra bộ nhớ, tóm tắt lại toàn bộ cuộc trò chuyện",
        )
    )

    assert "question 0" in response.content
    assert "question 11" in response.content
    assert response.provider == "memory"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_external_mode_routes_to_openrouter_when_explicitly_allowed(openrouter_settings: Settings) -> None:
    init_db()
    local = _Provider("llama_cpp", "local")
    cloud = _Provider("openrouter", "cloud")
    service = AIService(
        local=local,
        cloud=cloud,
        history=HistoryService(),
        settings=openrouter_settings,
        users=UserService(),
    )

    response = await service.chat(
        ChatRequest(
            project_id="test",
            tenant_id="default",
            user_name="hung",
            user_message="hello external",
            model_mode="external",
            allow_external=True,
        )
    )

    assert response.provider == "openrouter"
    assert response.model == "openrouter/free-model"
    assert response.content == "cloud"
    assert cloud.calls == 1
    assert local.calls == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_openrouter_token_cap_is_passed_to_cloud_provider(openrouter_settings: Settings) -> None:
    init_db()
    cloud = _Provider("openrouter", "cloud")
    settings = openrouter_settings.model_copy(update={"openrouter_max_tokens": 128, "ai_top_p": 0.9})
    service = AIService(
        local=_Provider("llama_cpp"),
        cloud=cloud,
        history=HistoryService(),
        settings=settings,
        users=UserService(),
    )

    await service.chat(
        ChatRequest(
            project_id="test",
            tenant_id="default",
            user_name="hung",
            user_message="hello external",
            model_mode="external",
            allow_external=True,
        )
    )

    assert cloud.options == [{"max_tokens": 128, "top_p": 0.9}]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_llama_cpp_local_provider_passes_num_ctx(openrouter_settings: Settings) -> None:
    init_db()
    local = _Provider("llama_cpp", "local")
    settings = openrouter_settings.model_copy(update={"local_max_tokens": 128, "ai_top_p": 0.9})
    service = AIService(
        local=local,
        cloud=_Provider("openrouter"),
        history=HistoryService(),
        settings=settings,
        users=UserService(),
    )

    await service.chat(
        ChatRequest(
            project_id="test",
            tenant_id="default",
            user_name="hung",
            user_message="hello local",
        )
    )

    assert local.options == [{"max_tokens": 128, "num_ctx": 8192, "top_p": 0.9}]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_llama_cpp_local_provider_includes_num_ctx(openrouter_settings: Settings) -> None:
    init_db()
    local = _Provider("llama_cpp", "local")
    settings = openrouter_settings.model_copy(update={"local_max_tokens": 128, "ai_top_p": 0.9})
    service = AIService(
        local=local,
        cloud=_Provider("openrouter"),
        history=HistoryService(),
        settings=settings,
        users=UserService(),
    )

    await service.chat(
        ChatRequest(
            project_id="test",
            tenant_id="default",
            user_name="hung",
            user_message="hello local",
        )
    )

    assert local.options == [{"max_tokens": 128, "num_ctx": 8192, "top_p": 0.9}]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_provider_call_timeout_raises_upstream_timeout(openrouter_settings: Settings) -> None:
    init_db()
    settings = openrouter_settings.model_copy(update={"provider_call_timeout_seconds": 0.01})
    service = AIService(
        local=_Provider("llama_cpp"),
        cloud=_SlowProvider("openrouter"),
        history=HistoryService(),
        settings=settings,
        users=UserService(),
    )

    with pytest.raises(UpstreamTimeout, match="after retry"):
        await service.chat(
            ChatRequest(
                project_id="test",
                tenant_id="default",
                user_name="hung",
                user_message="slow external",
                model_mode="external",
                allow_external=True,
            )
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_external_mode_requires_explicit_allow_by_default(openrouter_settings: Settings) -> None:
    init_db()
    service = AIService(
        local=_Provider("llama_cpp"),
        cloud=_Provider("openrouter"),
        history=HistoryService(),
        settings=openrouter_settings,
        users=UserService(),
    )

    with pytest.raises(UpstreamError, match="not allowed"):
        await service.chat(
            ChatRequest(
                project_id="test",
                tenant_id="default",
                user_name="hung",
                user_message="hello external",
                model_mode="external",
            )
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_external_mode_denies_private_vehix_even_if_allowed(openrouter_settings: Settings) -> None:
    init_db()
    service = AIService(
        local=_Provider("llama_cpp"),
        cloud=_Provider("openrouter"),
        history=HistoryService(),
        settings=openrouter_settings,
        users=UserService(),
    )

    with pytest.raises(UpstreamError, match="not allowed"):
        await service.chat(
            ChatRequest(
                project_id="vehix",
                tenant_id="default",
                user_name="hung",
                user_message="private vehix data",
                model_mode="external",
                allow_external=True,
            )
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("/search: latest AI news", "latest AI news"),
        ("/search latest AI news", "latest AI news"),
        ("  /search: latest AI news  ", "latest AI news"),
        ("/SEARCH: latest AI news", "latest AI news"),
        ("/search", None),
        ("/search:", None),
        ("please /search latest AI news", None),
        ("what is the weather today?", None),
    ],
)
def test_explicit_search_parser(openrouter_settings: Settings, text: str, expected: str | None) -> None:
    service = AIService(
        local=_Provider("llama_cpp"),
        cloud=_Provider("openrouter"),
        history=HistoryService(),
        settings=openrouter_settings,
        users=UserService(),
    )

    assert service._extract_explicit_search_query(text) == expected


@pytest.mark.unit
@pytest.mark.asyncio
async def test_explicit_search_routes_to_openrouter_and_uses_stripped_query(openrouter_settings: Settings) -> None:
    init_db()
    local = _Provider("llama_cpp", "local")
    cloud = _Provider("openrouter", "cloud")
    search = _SearchService()
    service = AIService(
        local=local,
        cloud=cloud,
        history=HistoryService(),
        settings=openrouter_settings,
        users=UserService(),
        web_search=search,
    )

    response = await service.chat(
        ChatRequest(
            project_id="test",
            tenant_id="default",
            user_name="hung-search-cloud",
            user_message="/search: latest AI news",
            enable_search=True,
            allow_external=True,
        )
    )

    assert response.provider == "openrouter"
    assert response.model == "openrouter/free-model"
    assert response.route == "cloud"
    assert response.route_reason == "explicit_search_cloud"
    # Search now relies on the cloud :online plugin server-side, so we no
    # longer inject a local SearXNG context block or surface URLs from it.
    assert response.sources == []
    assert search.queries == []
    assert cloud.calls == 1
    assert local.calls == 0
    assert cloud.messages[0][-1].content == "latest AI news"
    # Web plugin should be enabled in the OpenRouter request options
    assert cloud.options[0].get("web") is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_enable_search_without_command_stays_local(openrouter_settings: Settings) -> None:
    init_db()
    local = _Provider("llama_cpp", "local")
    cloud = _Provider("openrouter", "cloud")
    search = _SearchService()
    service = AIService(
        local=local,
        cloud=cloud,
        history=HistoryService(),
        settings=openrouter_settings,
        users=UserService(),
        web_search=search,
    )

    response = await service.chat(
        ChatRequest(
            project_id="test",
            tenant_id="default",
            user_name="hung-normal-search-toggle",
            user_message="gia vang hom nay the nao",
            enable_search=True,
            allow_external=True,
        )
    )

    assert response.provider == "llama_cpp"
    assert response.route == "local"
    assert response.sources == []
    assert search.queries == []
    assert local.calls == 1
    assert cloud.calls == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_search_command_without_toggle_routes_to_cloud(openrouter_settings: Settings) -> None:
    # /search: is always detected regardless of enable_search toggle
    init_db()
    local = _Provider("llama_cpp", "local")
    cloud = _Provider("openrouter", "cloud")
    search = _SearchService()
    service = AIService(
        local=local,
        cloud=cloud,
        history=HistoryService(),
        settings=openrouter_settings,
        users=UserService(),
        web_search=search,
    )

    response = await service.chat(
        ChatRequest(
            project_id="test",
            tenant_id="default",
            user_name="hung-search-toggle-off",
            user_message="/search: latest AI news",
            enable_search=False,
            allow_external=True,
        )
    )

    assert response.provider == "openrouter"
    assert response.route == "cloud"
    assert local.calls == 0
    assert cloud.calls == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_explicit_search_respects_external_policy(openrouter_settings: Settings) -> None:
    init_db()
    local = _Provider("llama_cpp", "local")
    cloud = _Provider("openrouter", "cloud")
    service = AIService(
        local=local,
        cloud=cloud,
        history=HistoryService(),
        settings=openrouter_settings,
        users=UserService(),
        web_search=_SearchService(),
    )

    # /search: bypasses external policy — routes to cloud unconditionally
    response = await service.chat(
        ChatRequest(
            project_id="test",
            tenant_id="default",
            user_name="hung-search-denied",
            user_message="/search: latest AI news",
            enable_search=True,
            allow_external=False,
        )
    )
    assert response.provider == "openrouter"
    assert response.route == "cloud"
    assert local.calls == 0
    assert cloud.calls == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_explicit_search_stream_routes_to_openrouter(openrouter_settings: Settings) -> None:
    init_db()
    local = _Provider("llama_cpp", "local")
    cloud = _Provider("openrouter", "cloud")
    search = _SearchService()
    service = AIService(
        local=local,
        cloud=cloud,
        history=HistoryService(),
        settings=openrouter_settings,
        users=UserService(),
        web_search=search,
    )

    events = [
        event
        async for event in service.chat_stream(
            ChatRequest(
                project_id="test",
                tenant_id="default",
                user_name="hung-search-stream",
                user_message="/search latest AI news",
                enable_search=True,
                allow_external=True,
            )
        )
    ]

    assert events[0]["type"] == "start"
    assert events[0]["provider"] == "openrouter"
    assert events[0]["model"] == "openrouter/free-model"
    assert events[0]["route"] == "cloud"
    # Search now relies on the cloud :online plugin, no client-side URLs
    assert events[-1]["sources"] == []
    assert search.queries == []
    assert cloud.stream_calls == 1
    assert local.stream_calls == 0
    # Web plugin enabled in stream request options
    assert cloud.options[-1].get("web") is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_throughput_mode_routes_allowed_requests_to_openrouter(openrouter_settings: Settings) -> None:
    init_db()
    local = _Provider("llama_cpp", "local")
    cloud = _Provider("openrouter", "cloud")
    settings = openrouter_settings.model_copy(update={"hybrid_force_cloud_for_allowed": True})
    service = AIService(
        local=local,
        cloud=cloud,
        history=HistoryService(),
        settings=settings,
        users=UserService(),
    )

    response = await service.chat(
        ChatRequest(
            project_id="doden",
            tenant_id="default",
            user_name="hung",
            user_message="route direct cloud for throughput",
            allow_external=True,
        )
    )

    assert response.provider == "openrouter"
    assert response.route == "cloud"
    assert response.fallback_used is False
    assert response.route_reason == "throughput_cloud"
    assert response.queue_wait_ms is None
    assert cloud.calls == 1
    assert local.calls == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_throughput_mode_respects_denied_projects(openrouter_settings: Settings) -> None:
    init_db()
    local = _Provider("llama_cpp", "local")
    cloud = _Provider("openrouter", "cloud")
    settings = openrouter_settings.model_copy(update={"hybrid_force_cloud_for_allowed": True})
    service = AIService(
        local=local,
        cloud=cloud,
        history=HistoryService(),
        settings=settings,
        users=UserService(),
    )

    response = await service.chat(
        ChatRequest(
            project_id="vehix",
            tenant_id="default",
            user_name="hung",
            user_message="private project must stay local",
            allow_external=True,
        )
    )

    assert response.provider == "llama_cpp"
    assert response.route == "local"
    assert response.fallback_used is False
    assert response.route_reason == "local_available"
    assert local.calls == 1
    assert cloud.calls == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_local_overload_routes_to_openrouter_fallback(openrouter_settings: Settings) -> None:
    init_db()
    local = _Provider("llama_cpp", "local")
    cloud = _Provider("openrouter", "cloud")
    service = AIService(
        local=local,
        cloud=cloud,
        history=HistoryService(),
        settings=openrouter_settings,
        users=UserService(),
    )
    service._gpu_lock = _LockedButNonBlockingSemaphore()

    response = await service.chat(
        ChatRequest(
            project_id="doden",
            tenant_id="default",
            user_name="hung",
            user_message="auto fallback only when overloaded",
            allow_external=True,
        )
    )

    assert response.provider == "openrouter"
    assert response.route == "cloud"
    assert response.fallback_used is True
    assert response.model == "openrouter/free-model"
    assert response.route_reason == "local_locked_fallback"
    assert response.queue_wait_ms is None
    assert cloud.calls == 1
    assert local.calls == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_local_queue_timeout_routes_to_openrouter_fallback(openrouter_settings: Settings) -> None:
    init_db()
    local = _Provider("llama_cpp", "local")
    cloud = _Provider("openrouter", "cloud")
    settings = openrouter_settings.model_copy(update={"hybrid_force_cloud_when_locked": False, "hybrid_local_queue_timeout_seconds": 0.01})
    service = AIService(
        local=local,
        cloud=cloud,
        history=HistoryService(),
        settings=settings,
        users=UserService(),
    )
    service._gpu_lock = _NeverAvailableSemaphore()

    response = await service.chat(
        ChatRequest(
            project_id="doden",
            tenant_id="default",
            user_name="hung",
            user_message="fallback after waiting briefly",
            allow_external=True,
        )
    )

    assert response.provider == "openrouter"
    assert response.route == "cloud"
    assert response.fallback_used is True
    assert response.route_reason == "local_queue_timeout_fallback"
    assert cloud.calls == 1
    assert cloud.options == [{}]
    assert local.calls == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_local_queue_timeout_fallback_uses_cloud_token_cap(openrouter_settings: Settings) -> None:
    init_db()
    local = _Provider("llama_cpp", "local")
    cloud = _Provider("openrouter", "cloud")
    settings = openrouter_settings.model_copy(update={
        "hybrid_force_cloud_when_locked": False,
        "hybrid_local_queue_timeout_seconds": 0.01,
        "openrouter_max_tokens": 64,
    })
    service = AIService(
        local=local,
        cloud=cloud,
        history=HistoryService(),
        settings=settings,
        users=UserService(),
    )
    service._gpu_lock = _NeverAvailableSemaphore()

    await service.chat(
        ChatRequest(
            project_id="doden",
            tenant_id="default",
            user_name="hung",
            user_message="fallback after waiting briefly",
            allow_external=True,
        )
    )

    assert cloud.options == [{"max_tokens": 64}]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_local_queue_timeout_raises_when_external_disallowed(openrouter_settings: Settings) -> None:
    init_db()
    local = _Provider("llama_cpp", "local")
    cloud = _Provider("openrouter", "cloud")
    settings = openrouter_settings.model_copy(update={"hybrid_force_cloud_when_locked": False, "hybrid_local_queue_timeout_seconds": 0.01})
    service = AIService(
        local=local,
        cloud=cloud,
        history=HistoryService(),
        settings=settings,
        users=UserService(),
    )
    service._gpu_lock = _NeverAvailableSemaphore()

    with pytest.raises(UpstreamTimeout):
        await service.chat(
            ChatRequest(
                project_id="doden",
                tenant_id="default",
                user_name="hung",
                user_message="must not fallback",
                allow_external=False,
            )
        )

    assert cloud.calls == 0
    assert local.calls == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_local_overload_respects_openrouter_denied_projects(openrouter_settings: Settings) -> None:
    init_db()
    local = _Provider("llama_cpp", "local")
    cloud = _Provider("openrouter", "cloud")
    service = AIService(
        local=local,
        cloud=cloud,
        history=HistoryService(),
        settings=openrouter_settings,
        users=UserService(),
    )
    service._gpu_lock = _LockedButNonBlockingSemaphore()

    response = await service.chat(
        ChatRequest(
            project_id="vehix",
            tenant_id="default",
            user_name="hung",
            user_message="must stay local despite saturation",
        )
    )

    assert response.provider == "llama_cpp"
    assert response.route == "local"
    assert response.fallback_used is False
    assert local.calls == 1
    assert cloud.calls == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_local_overload_does_not_fallback_when_external_disallowed(openrouter_settings: Settings) -> None:
    init_db()
    local = _Provider("llama_cpp", "local")
    cloud = _Provider("openrouter", "cloud")
    service = AIService(
        local=local,
        cloud=cloud,
        history=HistoryService(),
        settings=openrouter_settings,
        users=UserService(),
    )
    service._gpu_lock = _LockedButNonBlockingSemaphore()

    response = await service.chat(
        ChatRequest(
            project_id="doden",
            tenant_id="default",
            user_name="hung",
            user_message="local-only key must stay local despite saturation",
            allow_external=False,
        )
    )

    assert response.provider == "llama_cpp"
    assert response.route == "local"
    assert response.fallback_used is False
    assert local.calls == 1
    assert cloud.calls == 0
