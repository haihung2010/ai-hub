"""Integration coverage for API key auth, rate limiting, CORS, and security logging."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


@pytest.fixture
def security_settings(tmp_path: Path) -> Settings:
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
        SECURITY_LOG_FILE=str(tmp_path / "security.log"),
        ALLOWED_ORIGINS=[
            "http://localhost",
            "http://localhost:3000",
            "http://localhost:5173",
            "http://127.0.0.1:8000",
            "https://htechlabsvn.com",
        ],
    )


@pytest.fixture
def secure_client(security_settings: Settings) -> TestClient:
    app = create_app(settings=security_settings)
    with TestClient(app) as client:
        yield client


@pytest.mark.integration
def test_missing_api_key_returns_401(secure_client: TestClient, security_settings: Settings) -> None:
    response = secure_client.post(
        "/v1/chat",
        json={"project_id": "iot", "user_message": "hi"},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "invalid api key"
    assert Path(security_settings.security_log_file).read_text(encoding="utf-8")


@pytest.mark.integration
def test_invalid_api_key_returns_401(secure_client: TestClient) -> None:
    response = secure_client.post(
        "/v1/chat",
        headers={"X-API-KEY": "wrong"},
        json={"project_id": "iot", "user_message": "hi"},
    )

    assert response.status_code == 401


@pytest.mark.integration
def test_rate_limit_returns_429(secure_client: TestClient) -> None:
    headers = {"X-API-KEY": "test-api-key"}

    for _ in range(5):
        response = secure_client.post(
            "/v1/chat",
            headers=headers,
            json={"project_id": "iot", "user_message": "hi"},
        )
        assert response.status_code != 429

    response = secure_client.post(
        "/v1/chat",
        headers=headers,
        json={"project_id": "iot", "user_message": "hi"},
    )

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "60"


@pytest.mark.integration
def test_cors_allows_configured_origin(security_settings: Settings) -> None:
    app = create_app(settings=security_settings)
    with TestClient(app) as client:
        response = client.options(
            "/v1/chat",
            headers={
                "Origin": "https://htechlabsvn.com",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "X-API-KEY, Content-Type",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://htechlabsvn.com"


@pytest.mark.integration
def test_cors_rejects_unknown_origin(security_settings: Settings) -> None:
    app = create_app(settings=security_settings)
    with TestClient(app) as client:
        response = client.options(
            "/v1/chat",
            headers={
                "Origin": "https://evil.example.com",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "X-API-KEY, Content-Type",
            },
        )

    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers
