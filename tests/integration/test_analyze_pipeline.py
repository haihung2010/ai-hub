"""Integration tests for /v1/ihi/analyze new 3-layer pipeline (Layer 1 only for now)."""
import os
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.middleware.security import AuthFailureTracker, InMemoryRateLimiter

API_KEY = os.environ.get("API_KEY", "1XteCCQ_s_UbrqOGEIYybmDBnokWhxYap90D6_Jojx8")
HEADERS = {"X-API-KEY": API_KEY, "Content-Type": "application/json"}

# Skip if PG not available
try:
    from app.core.database import _get_pool as _try_pool
    from psycopg_pool import PoolClosed
    try:
        _test_pool = _try_pool()
        with _test_pool.connection() as _conn:
            with _conn.cursor() as _cur:
                _cur.execute("SELECT 1")
                _cur.fetchone()
        _DB_OK = True
    except PoolClosed:
        _DB_OK = False
    except Exception:
        _DB_OK = False
except Exception:
    _DB_OK = False

pytestmark = [
    pytest.mark.skipif(not _DB_OK, reason="PostgreSQL not reachable"),
    pytest.mark.no_isolated_db,
]


@pytest.fixture(scope="module")
def client():
    """Build a TestClient that allows the `testserver` host header."""
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
    with TestClient(app) as c:
        yield c


def test_analyze_with_clear_danger_battery(client):
    """Battery 4% should trigger Layer 1 rule and return DANGER from rule layer."""
    r = client.post("/v1/ihi/analyze", headers=HEADERS, json={
        "ts": "03/06 14:30",
        "data": [{"id": "Sensor-001", "t": 50, "v": 1.0, "c": 5}],
        "extra": {"Sensor-001": {"battery_pct": 4.0, "humidity": 50}},
    })
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["alert"] == "DANGER"
    assert data["source_layer"].startswith("rule_")  # rule caught it
    assert any(v["measurement"] == "battery_pct" for v in data["violations"])


def test_analyze_with_normal_readings(client):
    """All readings OK → NORMAL from rule layer."""
    r = client.post("/v1/ihi/analyze", headers=HEADERS, json={
        "ts": "03/06 14:30",
        "data": [{"id": "Sensor-001", "t": 50, "v": 1.0, "c": 5}],
        "extra": {"Sensor-001": {"battery_pct": 80, "humidity": 50}},
    })
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["alert"] == "NORMAL"
    assert data["source_layer"] == "rule"


def test_analyze_with_voltage_imbalance_nema(client):
    """v_imbalance=6% (>5% NEMA) → DANGER from rule layer."""
    r = client.post("/v1/ihi/analyze", headers=HEADERS, json={
        "ts": "03/06 14:30",
        "data": [{"id": "Meter-001", "t": 30, "v": 0, "c": 5}],
        "extra": {"Meter-001": {"v_imbalance_pct": 6.0, "f_hz": 50.0}},
    })
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["alert"] == "DANGER"
    assert any(v["measurement"] == "v_imbalance_pct" for v in data["violations"])
