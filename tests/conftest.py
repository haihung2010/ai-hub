"""Shared fixtures: settings isolation, app/client factory, httpx mock."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

import app.core.database as _db_module
from app.core.config import Settings
from app.core.database import init_db
from app.main import create_app
from app.middleware.security import AuthFailureTracker, InMemoryRateLimiter


@pytest.fixture(autouse=True)
def isolated_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect DB_PATH to a per-test temp dir so tests never share SQLite state."""
    monkeypatch.setattr(_db_module, "DB_PATH", tmp_path / "test.db")
    init_db()


@pytest.fixture
def settings() -> Settings:
    return Settings(
        APP_PORT=8000,
        LOG_LEVEL="WARNING",
        LLAMA_CPP_BASE_URL="http://llama.test",
        LLAMA_CPP_OPENAI_URL="http://llama.test/v1",
        DEFAULT_MODEL="test-model:latest",
        LITE_MODEL="test-lite:latest",
        REQUEST_TIMEOUT_SECONDS=5.0,
        MAX_HISTORY_MESSAGES=5,
        LITE_MAX_HISTORY_MESSAGES=5,
        API_KEY="test-api-key",
        RATE_LIMIT_PER_MINUTE=5,
        ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1", "api-aiserver.htechlabsvn.com"],
    )


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    limiter = InMemoryRateLimiter(limit=settings.rate_limit_per_minute)
    tracker = AuthFailureTracker(limit=settings.auth_failure_limit, block_seconds=settings.auth_failure_block_seconds)
    app = create_app(settings=settings, limiter=limiter, failure_tracker=tracker)
    with TestClient(app) as tc:
        tc.headers.update({"X-API-KEY": settings.api_key})
        yield tc


@pytest.fixture
def mock_api() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False, assert_all_mocked=True) as router:
        yield router


def make_ollama_chat_response(content: str = "pong") -> dict[str, Any]:
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "model": "test-model:latest",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
    }


def make_ollama_tags_response(names: list[str]) -> dict[str, Any]:
    return {"models": [{"name": n} for n in names]}
