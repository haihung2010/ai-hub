"""Shared fixtures: settings isolation, app/client factory, httpx mock."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


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
    )


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    app = create_app(settings=settings)
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
