"""Unit tests for the /v1/crew/research route."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.middleware.security import AuthFailureTracker, InMemoryRateLimiter


@pytest.mark.unit
def test_crew_research_503_when_not_enabled(settings: Settings) -> None:
    limiter = InMemoryRateLimiter(limit=settings.rate_limit_per_minute)
    tracker = AuthFailureTracker(limit=settings.auth_failure_limit, block_seconds=settings.auth_failure_block_seconds)
    app = create_app(settings=settings, limiter=limiter, failure_tracker=tracker)
    with TestClient(app) as tc:
        # crew_service not set on app.state => should 503
        resp = tc.post(
            "/v1/crew/research",
            json={"query": "latest AI news"},
            headers={"X-API-KEY": settings.api_key},
        )
    assert resp.status_code == 503
    assert "crew agents not enabled" in resp.json()["detail"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_crew_research_returns_result(settings: Settings) -> None:
    limiter = InMemoryRateLimiter(limit=settings.rate_limit_per_minute)
    tracker = AuthFailureTracker(limit=settings.auth_failure_limit, block_seconds=settings.auth_failure_block_seconds)
    app = create_app(settings=settings, limiter=limiter, failure_tracker=tracker)
    mock_crew = AsyncMock()
    mock_crew.research.return_value = "Lots of AI news today."

    with TestClient(app) as tc:
        app.state.crew_service = mock_crew
        resp = tc.post(
            "/v1/crew/research",
            json={"query": "latest AI news"},
            headers={"X-API-KEY": settings.api_key},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["query"] == "latest AI news"
    assert body["result"] == "Lots of AI news today."


@pytest.mark.unit
@pytest.mark.asyncio
async def test_crew_research_502_when_empty_result(settings: Settings) -> None:
    limiter = InMemoryRateLimiter(limit=settings.rate_limit_per_minute)
    tracker = AuthFailureTracker(limit=settings.auth_failure_limit, block_seconds=settings.auth_failure_block_seconds)
    app = create_app(settings=settings, limiter=limiter, failure_tracker=tracker)
    mock_crew = AsyncMock()
    mock_crew.research.return_value = ""

    with TestClient(app) as tc:
        app.state.crew_service = mock_crew
        resp = tc.post(
            "/v1/crew/research",
            json={"query": "test query"},
            headers={"X-API-KEY": settings.api_key},
        )
    assert resp.status_code == 502
    assert "empty result" in resp.json()["detail"]


@pytest.mark.unit
def test_crew_research_422_on_empty_query(settings: Settings) -> None:
    limiter = InMemoryRateLimiter(limit=settings.rate_limit_per_minute)
    tracker = AuthFailureTracker(limit=settings.auth_failure_limit, block_seconds=settings.auth_failure_block_seconds)
    app = create_app(settings=settings, limiter=limiter, failure_tracker=tracker)
    with TestClient(app) as tc:
        resp = tc.post(
            "/v1/crew/research",
            json={"query": ""},
            headers={"X-API-KEY": settings.api_key},
        )
    assert resp.status_code == 422
