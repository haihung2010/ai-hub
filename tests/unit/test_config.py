"""Settings load defaults and honor env overrides."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings, get_settings


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
    ]:
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(ValidationError):
        Settings()

    monkeypatch.setenv("API_KEY", "test-api-key")
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
    assert s.hybrid_force_cloud_for_allowed is False
    assert s.ai_max_tokens == 0
    assert s.local_max_tokens == 0
    assert s.openrouter_max_tokens == 0
    assert s.ai_top_p == 0.0
    assert s.provider_call_timeout_seconds == 0.0
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
