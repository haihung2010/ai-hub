"""Settings load defaults and honor env overrides."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings, get_settings


@pytest.mark.unit
def test_defaults_available(monkeypatch: pytest.MonkeyPatch, tmp_path: pytest.TempPathFactory) -> None:
    monkeypatch.chdir(tmp_path)  # no .env file in tmp dir
    for key in ["APP_PORT", "RATE_LIMIT_PER_MINUTE", "API_KEY", "DEFAULT_MODEL", "MAX_HISTORY_MESSAGES"]:
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(ValidationError):
        Settings()

    monkeypatch.setenv("API_KEY", "test-api-key")
    s = Settings()
    assert s.app_port == 8000
    assert s.default_model.startswith("VladimirGav/")
    assert s.max_history_messages == 20
    assert s.rate_limit_per_minute == 5
    assert "https://htechlabsvn.com" in s.allowed_origins


@pytest.mark.unit
def test_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_PORT", "9000")
    monkeypatch.setenv("MAX_HISTORY_MESSAGES", "3")
    get_settings.cache_clear()
    s = get_settings()
    assert s.app_port == 9000
    assert s.max_history_messages == 3
    get_settings.cache_clear()
