"""Custom exceptions map to the documented HTTP status codes."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.errors import (
    AIHubError,
    OllamaUnavailable,
    ProjectNotFound,
    UpstreamError,
    UpstreamTimeout,
    VramExhausted,
)
from app.main import create_app


class _Stub:
    """Minimal AIService stand-in that always raises the configured exception."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    async def chat(self, _req: Any) -> Any:
        raise self._exc


@pytest.mark.unit
@pytest.mark.parametrize(
    ("exc", "expected_status"),
    [
        (ProjectNotFound("x"), 404),
        (OllamaUnavailable("connection refused"), 503),
        (VramExhausted("out of memory"), 503),
        (UpstreamTimeout("read timeout"), 504),
        (UpstreamError("502 upstream"), 502),
    ],
)
def test_exception_maps_to_status(
    settings: Settings, exc: AIHubError, expected_status: int
) -> None:
    app = create_app(settings=settings)
    with TestClient(app) as tc:
        app.state.ai_service = _Stub(exc)
        resp = tc.post(
            "/v1/chat",
            json={"project_id": "iot", "user_message": "hi"},
            headers={"X-API-KEY": settings.api_key},
        )
    assert resp.status_code == expected_status
    assert "detail" in resp.json()
