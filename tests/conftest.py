"""Shared fixtures: settings isolation, app/client factory, httpx mock."""

from __future__ import annotations

from collections.abc import Iterator
import os
from typing import Any

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

import app.core.database as _db_module
from app.core.config import Settings
from app.core.database import get_db_connection, init_db
from app.main import create_app
from app.middleware.security import AuthFailureTracker, InMemoryRateLimiter


def ensure_user(user_id: str, tenant_id: str = "default", name: str | None = None) -> None:
    """Insert a user row — needed for FK-constrained tables in PostgreSQL.
    Uses user_id as name by default to avoid UNIQUE(tenant_id, name) conflicts."""
    with get_db_connection() as conn:
        conn.execute(
            "INSERT INTO users (id, tenant_id, name) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
            (user_id, tenant_id, name or user_id),
        )
        conn.commit()

_TEST_TABLES = [
    "usage_events", "failure_risk_events",
    "knowledge_card_chunks", "knowledge_cards",
    "memory_items", "memory_episodes", "memory_consolidations",
    "pinned_memories", "prediction_records",
    "messages", "sessions", "summaries", "users",
    "api_keys",
]

# Production DSN matchers. Each entry is a function: DSN -> True if the
# DSN targets the production database (database name == "ai_hub"). This
# is the correct way to detect prod: by DB name, NOT by substring
# (which incorrectly matches "ai_hub_test", "ai_hub_anything", etc.).
#
# Background: pytest run at 2026-06-07 00:14:29 wiped 14 production
# tables because both AI_HUB_ALLOW_DB_TRUNCATE_FOR_TESTS=1 and the
# production DATABASE_URL were set. This guard prevents that.
def _is_prod_dsn(dsn: str) -> bool:
    """Return True if the DSN targets the production database.

    Detection is by the LAST path component (the database name), not by
    substring: a DSN like ``postgresql://aihub:aihub_pass@localhost:5432/ai_hub_test``
    must NOT match (test DB), only ``.../ai_hub`` matches.
    """
    if not dsn:
        return False
    # Find the DB name (last path component after the final '/')
    last_slash = dsn.rfind("/")
    if last_slash < 0:
        return False
    db_part = dsn[last_slash + 1:].split("?")[0]  # strip query params
    return db_part == "ai_hub"


def _should_refuse_truncate(env: dict[str, str] | None = None) -> str | None:
    """Return an error message if the current env is configured to
    TRUNCATE the production database. Return None if safe to proceed.

    Pure function — no env access, no DB access, no I/O. Easy to unit-test.
    """
    e = env if env is not None else os.environ
    if e.get("AI_HUB_ALLOW_DB_TRUNCATE_FOR_TESTS") != "1":
        return (
            "Refusing to truncate PostgreSQL without "
            "AI_HUB_ALLOW_DB_TRUNCATE_FOR_TESTS=1. Point DATABASE_URL at a test DB."
        )
    dsn = e.get("DATABASE_URL", "")
    if dsn and _is_prod_dsn(dsn):
        return (
            "Refusing to TRUNCATE production database. "
            "DATABASE_URL matches a known production DSN. "
            "Set DATABASE_URL to a test database (e.g. ai_hub_test) "
            "or unset AI_HUB_ALLOW_DB_TRUNCATE_FOR_TESTS."
        )
    return None


@pytest.fixture(autouse=True)
def isolated_db(request) -> None:
    """Truncate all tables before each test for isolation (PostgreSQL).

    Guards against accidental production-DB wipe: refuses to truncate if
    DATABASE_URL matches a known production DSN (set AI_HUB_ALLOW_DB_TRUNCATE_FOR_TESTS=1).
    Tests can opt-out of DB isolation by applying the ``no_isolated_db``
    marker — e.g. when exercising the guard logic itself.
    """
    if "no_isolated_db" in request.keywords:
        return
    err = _should_refuse_truncate()
    if err is not None:
        if err.startswith("Refusing to TRUNCATE production"):
            raise RuntimeError(err)
        pytest.fail(err)
    init_db()
    with get_db_connection() as conn:
        conn.execute(f"TRUNCATE TABLE {', '.join(_TEST_TABLES)} CASCADE")
        conn.commit()


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
        BACKGROUND_LLAMA_CPP_ENABLED="false",
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
