# tests/integration/test_override_endpoints.py
import os
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.middleware.security import AuthFailureTracker, InMemoryRateLimiter

API_KEY = os.environ.get("API_KEY", "1XteCCQ_s_UbrqOGEIYybmDBnokWhxYap90D6_Jojx8")
HEADERS = {"X-API-KEY": API_KEY, "Content-Type": "application/json"}

# Requires PG. Will skip if not available.
try:
    from app.core.database import _get_pool as _try_pool
    from psycopg_pool import PoolClosed
    try:
        _test_pool = _try_pool()
        # Test connection can be obtained without raising
        with _test_pool.connection() as _conn:
            with _conn.cursor() as _cur:
                _cur.execute("SELECT 1")
                _cur.fetchone()
        _DB_AVAILABLE = True
    except PoolClosed:
        _DB_AVAILABLE = False
    except Exception:
        _DB_AVAILABLE = False
except Exception:
    _DB_AVAILABLE = False

pytestmark = [
    pytest.mark.skipif(not _DB_AVAILABLE, reason="PostgreSQL not reachable"),
    pytest.mark.no_isolated_db,  # We use real DB, not the truncation fixture
]


@pytest.fixture(scope="module")
def client():
    """Build a TestClient with a Settings that includes `testserver` host.

    We can't reuse the `client` fixture from conftest because the global
    `app` object is imported from app.main and may not include testserver
    in ALLOWED_HOSTS depending on the .env of the dev box.
    """
    settings = Settings(
        APP_PORT=8000,
        LOG_LEVEL="WARNING",
        LLAMA_CPP_BASE_URL="http://llama.test",
        LLAMA_CPP_OPENAI_URL="http://llama.test/v1",
        DEFAULT_MODEL="test-model:latest",
        LITE_MODEL="test-lite:latest",
        REQUEST_TIMEOUT_SECONDS=5.0,
        MAX_HISTORY_MESSAGES=5,
        LITE_MAX_HISTORY_MESSAGES=5,
        API_KEY=API_KEY,
        RATE_LIMIT_PER_MINUTE=5,
        ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1", "api-aiserver.htechlabsvn.com"],
        BACKGROUND_LLAMA_CPP_ENABLED="false",
    )
    limiter = InMemoryRateLimiter(limit=settings.rate_limit_per_minute)
    tracker = AuthFailureTracker(limit=settings.auth_failure_limit, block_seconds=settings.auth_failure_block_seconds)
    app = create_app(settings=settings, limiter=limiter, failure_tracker=tracker)
    with TestClient(app) as tc:
        tc.headers.update({"X-API-KEY": API_KEY})
        yield tc


def test_list_device_thresholds(client):
    """GET /v1/ihi/devices/{device_id}/thresholds returns merged view."""
    r = client.get("/v1/ihi/devices/Sensor-001/thresholds", headers=HEADERS)
    assert r.status_code == 200
    data = r.json()
    assert "thresholds" in data
    assert isinstance(data["thresholds"], dict)
    # Should include default thresholds for Sensor-001
    assert "battery_pct" in data["thresholds"]
    assert "temperature" in data["thresholds"]


def test_set_then_get_override(client):
    """POST override, then GET shows it."""
    # Clean first
    client.delete("/v1/ihi/devices/TEST-DEV-EP/thresholds/battery_pct", headers=HEADERS)
    # Set
    r = client.post(
        "/v1/ihi/devices/TEST-DEV-EP/thresholds",
        headers=HEADERS,
        json={"measurement": "battery_pct", "min_value": 50.0, "severity": "DANGER", "note": "pytest"},
    )
    assert r.status_code == 200, r.text
    # Get
    r2 = client.get("/v1/ihi/devices/TEST-DEV-EP/thresholds", headers=HEADERS)
    assert r2.status_code == 200
    data = r2.json()
    # Override should be present
    override_meas = [t for t in data["thresholds"].values() if t.get("source") == "manual"]
    assert any(t["measurement"] == "battery_pct" for t in override_meas)
    # Cleanup
    client.delete("/v1/ihi/devices/TEST-DEV-EP/thresholds/battery_pct", headers=HEADERS)
