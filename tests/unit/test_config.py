"""Settings load defaults and honor env overrides."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings, get_settings


# Pure unit tests — no DB access. Skip the autouse isolated_db fixture.
pytestmark = pytest.mark.no_isolated_db


@pytest.mark.unit
def test_defaults_available(monkeypatch: pytest.MonkeyPatch, tmp_path: pytest.TempPathFactory) -> None:
    monkeypatch.chdir(tmp_path)  # no .env file in tmp dir
    for key in [
        "APP_PORT",
        "RATE_LIMIT_PER_MINUTE",
        "API_KEY",
        "DEFAULT_MODEL",
        "MAX_HISTORY_MESSAGES",
        "OPENROUTER_MODEL",
        "OPENROUTER_FALLBACK_MODELS",
        "LOCAL_PROVIDER",
        "LLAMA_CPP_BASE_URL",
        "LLAMA_CPP_OPENAI_URL",
        "SUMMARY_CONCURRENCY",
        "SUMMARY_CONTEXT_TOKEN_THRESHOLD",
        "LITE_MAX_HISTORY_MESSAGES",
    ]:
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(ValidationError):
        Settings()

    monkeypatch.setenv("API_KEY", "test-api-key-aaaaaaaaaa")
    s = Settings()
    assert s.app_port == 8000
    assert s.default_model == "local-gemma4-e4b-q8"
    assert s.openrouter_model == "openai/gpt-oss-20b:free"
    assert s.openrouter_fallback_models == []
    assert s.local_provider == "llama_cpp"
    assert s.llama_cpp_base_url == "http://localhost:8080"
    assert s.llama_cpp_openai_url == "http://localhost:8080/v1"
    assert s.max_history_messages == 20
    assert s.lite_max_history_messages == 20
    assert s.summary_context_token_threshold == 4000
    assert s.summary_concurrency == 1
    assert s.rate_limit_per_minute == 5
    assert s.hybrid_force_cloud_for_allowed is True
    assert s.ai_max_tokens == 1000
    assert s.local_max_tokens == 1000
    assert s.openrouter_max_tokens == 1000
    assert s.ai_top_p == 0.0
    assert s.provider_call_timeout_seconds == 20.0
    assert s.enable_failure_risk is True
    assert s.failure_risk_log_only is True
    assert s.failure_risk_enable_actions is False
    assert s.failure_risk_high_threshold == 0.6
    assert s.failure_risk_medium_threshold == 0.3
    assert "https://htechlabsvn.com" in s.allowed_origins


@pytest.mark.unit
def test_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_PORT", "9000")
    monkeypatch.setenv("MAX_HISTORY_MESSAGES", "3")
    monkeypatch.setenv("AI_MAX_TOKENS", "256")
    monkeypatch.setenv("LOCAL_MAX_TOKENS", "128")
    monkeypatch.setenv("OPENROUTER_MAX_TOKENS", "64")
    monkeypatch.setenv("OPENROUTER_FALLBACK_MODELS", '["openrouter/auto"]')
    monkeypatch.setenv("HYBRID_FORCE_CLOUD_FOR_ALLOWED", "true")
    monkeypatch.setenv("AI_TOP_P", "0.9")
    monkeypatch.setenv("PROVIDER_CALL_TIMEOUT_SECONDS", "45")
    monkeypatch.setenv("SUMMARY_CONCURRENCY", "2")
    monkeypatch.setenv("LOCAL_PROVIDER", "llama_cpp")
    monkeypatch.setenv("LLAMA_CPP_BASE_URL", "http://llama.test")
    monkeypatch.setenv("LLAMA_CPP_OPENAI_URL", "http://llama.test/v1")
    get_settings.cache_clear()
    s = get_settings()
    assert s.app_port == 9000
    assert s.max_history_messages == 3
    assert s.ai_max_tokens == 256
    assert s.local_max_tokens == 128
    assert s.openrouter_max_tokens == 64
    assert s.openrouter_fallback_models == ["openrouter/auto"]
    assert s.hybrid_force_cloud_for_allowed is True
    assert s.ai_top_p == 0.9
    assert s.provider_call_timeout_seconds == 45
    assert s.summary_concurrency == 2
    assert s.local_provider == "llama_cpp"
    assert s.llama_cpp_base_url == "http://llama.test"
    assert s.llama_cpp_openai_url == "http://llama.test/v1"
    get_settings.cache_clear()


# ── Anthropic Contextual Retrieval (2026-06-19) ──────────────────


@pytest.mark.unit
def test_contextualizer_defaults_are_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default off so the LLM path never runs unless an operator turns it on.

    Why off by default: the E4B Q4 background model shares port 8081 with
    SummaryService and StructMem. We don't want context generation to start
    competing for those slots in a deployment that wasn't sized for it.
    """
    monkeypatch.setenv("API_KEY", "test-api-key-aaaaaaaaaa")
    get_settings.cache_clear()
    s = Settings()
    assert s.enable_llm_contextualizer is False
    assert s.contextualizer_model == "local-gemma4-e4b-q4-text"
    assert s.contextualizer_url == ""  # caller fills from background url
    assert s.contextualizer_max_concurrency == 4
    assert s.contextualizer_timeout_seconds == 30.0
    # 50-100 tokens is the Anthropic-recommended range; we pick 100 as
    # the upper bound so the worst-case response is still embed-friendly.
    assert s.contextualizer_max_context_tokens == 100
    get_settings.cache_clear()


@pytest.mark.unit
def test_contextualizer_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_KEY", "test-api-key-aaaaaaaaaa")
    monkeypatch.setenv("ENABLE_LLM_CONTEXTUALIZER", "true")
    monkeypatch.setenv("CONTEXTUALIZER_URL", "http://custom-host:8081/v1")
    monkeypatch.setenv("CONTEXTUALIZER_MODEL", "local-gemma3-4b-q4")
    monkeypatch.setenv("CONTEXTUALIZER_MAX_CONCURRENCY", "8")
    monkeypatch.setenv("CONTEXTUALIZER_TIMEOUT_SECONDS", "45.0")
    monkeypatch.setenv("CONTEXTUALIZER_MAX_CONTEXT_TOKENS", "150")
    get_settings.cache_clear()
    s = get_settings()
    assert s.enable_llm_contextualizer is True
    assert s.contextualizer_url == "http://custom-host:8081/v1"
    assert s.contextualizer_model == "local-gemma3-4b-q4"
    assert s.contextualizer_max_concurrency == 8
    assert s.contextualizer_timeout_seconds == 45.0
    assert s.contextualizer_max_context_tokens == 150
    get_settings.cache_clear()


@pytest.mark.unit
def test_contextualizer_max_tokens_bounds(monkeypatch: pytest.MonkeyPatch) -> None:
    """max_context_tokens is bounded to 20-400. Outside that range
    the model is either too short to be useful or too long to be
    'context' rather than 'summary'.
    """
    monkeypatch.setenv("API_KEY", "test-api-key-aaaaaaaaaa")
    monkeypatch.setenv("CONTEXTUALIZER_MAX_CONTEXT_TOKENS", "10")
    get_settings.cache_clear()
    with pytest.raises(ValidationError):
        Settings()
    monkeypatch.setenv("CONTEXTUALIZER_MAX_CONTEXT_TOKENS", "500")
    with pytest.raises(ValidationError):
        Settings()
    get_settings.cache_clear()


# ── Langfuse observability (2026-06-21) ─────────────────────────


@pytest.mark.unit
def test_langfuse_settings_default_to_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """LANGFUSE_ENABLED must default to False to keep tests hermetic."""
    # pydantic-settings reads shell env even when _env_file=None, so
    # clear the Langfuse keys to verify the field-level defaults.
    for key in (
        "LANGFUSE_ENABLED",
        "LANGFUSE_HOST",
        "LANGFUSE_PUBLIC_KEY",
        "LANGFUSE_SECRET_KEY",
        "LANGFUSE_OTLP_ENDPOINT",
        "LANGFUSE_FLUSH_INTERVAL_SECONDS",
        "LANGFUSE_SAMPLE_RATE",
    ):
        monkeypatch.delenv(key, raising=False)
    from app.core.config import Settings
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.langfuse_enabled is False
    assert s.langfuse_host == "http://localhost:3000"
    assert s.langfuse_public_key == ""
    assert s.langfuse_secret_key == ""
    assert s.langfuse_otlp_endpoint == "http://localhost:3000/api/public/otel"
    assert s.langfuse_flush_interval_seconds == 5.0
    assert s.langfuse_sample_rate == 1.0
