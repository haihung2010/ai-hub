"""Integration coverage for API key auth, rate limiting, CORS, and security logging."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.middleware.security import AuthFailureTracker, InMemoryRateLimiter


@pytest.fixture
def security_settings(tmp_path: Path) -> Settings:
    return Settings(
        APP_PORT=8000,
        LOG_LEVEL="WARNING",
        LLAMA_CPP_BASE_URL="http://llama.test",
        LLAMA_CPP_OPENAI_URL="http://llama.test/v1",
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
        ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1", "api-aiserver.htechlabsvn.com"],
    )


@pytest.fixture
def secure_client(security_settings: Settings) -> TestClient:
    limiter = InMemoryRateLimiter(limit=security_settings.rate_limit_per_minute)
    tracker = AuthFailureTracker(limit=security_settings.auth_failure_limit, block_seconds=security_settings.auth_failure_block_seconds)
    app = create_app(settings=security_settings, limiter=limiter, failure_tracker=tracker)
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


@pytest.mark.integration
def test_unknown_host_is_rejected(security_settings: Settings) -> None:
    app = create_app(settings=security_settings)
    with TestClient(app) as client:
        response = client.get("/health", headers={"Host": "evil.example.com"})

    assert response.status_code == 421
    assert response.json()["detail"] == "host not allowed"


@pytest.mark.integration
def test_health_can_require_api_key(security_settings: Settings) -> None:
    protected = security_settings.model_copy(update={"public_health_enabled": False})
    app = create_app(settings=protected)
    with TestClient(app) as client:
        missing = client.get("/health")
        allowed = client.get("/health", headers={"X-API-KEY": protected.api_key})

    assert missing.status_code == 401
    assert allowed.status_code in {200, 503}


@pytest.mark.integration
def test_docs_can_be_disabled_on_public_domain(security_settings: Settings) -> None:
    protected = security_settings.model_copy(update={"public_docs_enabled": False})
    app = create_app(settings=protected)
    with TestClient(app) as client:
        response = client.get("/docs")

    assert response.status_code == 401


@pytest.mark.integration
def test_repeated_invalid_api_keys_block_client_ip(security_settings: Settings) -> None:
    protected = security_settings.model_copy(
        update={"auth_failure_limit": 2, "auth_failure_block_seconds": 300}
    )
    limiter = InMemoryRateLimiter(limit=protected.rate_limit_per_minute)
    tracker = AuthFailureTracker(limit=protected.auth_failure_limit, block_seconds=protected.auth_failure_block_seconds)
    app = create_app(settings=protected, limiter=limiter, failure_tracker=tracker)
    with TestClient(app) as client:
        for _ in range(2):
            response = client.post(
                "/v1/chat",
                headers={"X-API-KEY": "wrong"},
                json={"project_id": "iot", "user_message": "hi"},
            )
            assert response.status_code == 401

        blocked = client.post(
            "/v1/chat",
            headers={"X-API-KEY": protected.api_key},
            json={"project_id": "iot", "user_message": "hi"},
        )

    assert blocked.status_code == 403
    assert blocked.json()["detail"] == "client temporarily blocked"
