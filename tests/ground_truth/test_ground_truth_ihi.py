"""Ground truth test for IHI analyze pipeline.

Runs full 3-layer pipeline (rule → RAG → LLM) against labeled cases.
Pass criteria: ≥85% verdict accuracy, ≤5% false negative rate.
"""
import os
import json
import pytest
from pathlib import Path
from fastapi.testclient import TestClient

# Configure test-friendly env BEFORE importing app modules
os.environ["API_KEY"] = "1XteCCQ_s_UbrqOGEIYybmDBnokWhxYap90D6_Jojx8"
os.environ["ALLOWED_HOSTS"] = '["testserver","localhost","127.0.0.1","api-aiserver.htechlabsvn.com"]'
os.environ.setdefault("LLAMA_CPP_BASE_URL", "http://llama.test")
os.environ.setdefault("LLAMA_CPP_OPENAI_URL", "http://llama.test/v1")
os.environ.setdefault("DEFAULT_MODEL", "test-model:latest")
os.environ.setdefault("LITE_MODEL", "test-lite:latest")
os.environ.setdefault("BACKGROUND_LLAMA_CPP_ENABLED", "false")
os.environ.setdefault("RATE_LIMIT_PER_MINUTE", "1000")
os.environ.setdefault("LOG_LEVEL", "WARNING")

from app.core.config import Settings
from app.main import create_app
from app.middleware.security import AuthFailureTracker, InMemoryRateLimiter

API_KEY = os.environ["API_KEY"]
HEADERS = {"X-API-KEY": API_KEY, "Content-Type": "application/json"}

GROUND_TRUTH_FILE = Path(__file__).parent / "ground_truth_v2.jsonl"
MIN_ACCURACY = 0.85
MAX_FALSE_NEGATIVE_RATE = 0.05

# Skip if PG unavailable
try:
    import psycopg
    conn_test = psycopg.connect(os.environ.get("DATABASE_URL", "postgresql://aihub:aihub_pass@localhost:5432/ai_hub"))
    conn_test.close()
    _DB_OK = True
except Exception:
    _DB_OK = False

pytestmark = [
    pytest.mark.skipif(not _DB_OK, reason="PostgreSQL not reachable"),
    pytest.mark.no_isolated_db,
]


def load_cases():
    if not GROUND_TRUTH_FILE.exists():
        return []
    with GROUND_TRUTH_FILE.open() as f:
        return [json.loads(line) for line in f if line.strip()]


@pytest.fixture(scope="module")
def client():
    # Build settings with explicit allowed_hosts to bypass any
    # plugin-loaded .env values that might omit 'testserver'.
    settings = Settings(
        allowed_hosts=["testserver", "localhost", "127.0.0.1", "api-aiserver.htechlabsvn.com"],
    )
    limiter = InMemoryRateLimiter(limit=settings.rate_limit_per_minute)
    tracker = AuthFailureTracker(
        limit=settings.auth_failure_limit,
        block_seconds=settings.auth_failure_block_seconds,
    )
    app = create_app(settings=settings, limiter=limiter, failure_tracker=tracker)
    with TestClient(app) as c:
        c.headers.update({"X-API-KEY": settings.api_key})
        yield c


@pytest.fixture(scope="module")
def cases():
    return load_cases()


def test_ground_truth_accuracy(client, cases):
    """Verify ≥85% verdict match across all labeled cases."""
    if not cases:
        pytest.skip("No ground truth cases found — run scripts/generate_ground_truth.py")
    correct = 0
    false_negatives = 0
    false_positives = 0
    for c in cases:
        # Build request with correct field name routing.
        # Raw alert.db readings use axis-component names (velocity_x/y/z) and
        # 'battery' (raw 0-100 from sensor). The IHI threshold system expects
        # 'velocity_rms' (computed) and 'battery_pct' (= soc %). Convert here.
        device_id = "Sensor-001"  # default; could infer from readings
        r_dict = c["readings"]

        # Compute velocity_rms from x/y/z if not already present
        v_rms = r_dict.get("velocity_rms")
        if v_rms is None and all(r_dict.get(f"velocity_{ax}") is not None
                                 for ax in ("x", "y", "z")):
            vx, vy, vz = r_dict["velocity_x"], r_dict["velocity_y"], r_dict["velocity_z"]
            v_rms = ((vx**2 + vy**2 + vz**2) / 3) ** 0.5

        # battery_pct from soc (or battery if not present)
        battery_pct = r_dict.get("battery_pct")
        if battery_pct is None:
            battery_pct = r_dict.get("soc", r_dict.get("battery"))

        payload = {
            "ts": "03/06 12:00",
            "data": [{"id": device_id,
                      "t": r_dict.get("temperature", 0),
                      "v": v_rms or 0,
                      "c": r_dict.get("current", 0)}],
            "extra": {device_id: {
                k: v for k, v in r_dict.items()
                if k not in ("temperature", "velocity_rms", "current",
                             "velocity_x", "velocity_y", "velocity_z",
                             "battery", "soc")
            } | {
                "velocity_rms": v_rms,
                "battery_pct": battery_pct,
            } if v_rms is not None or battery_pct is not None else {}},
        }
        r = client.post("/v1/ihi/analyze", headers=HEADERS, json=payload)
        assert r.status_code == 200, f"Case {c['id']}: HTTP {r.status_code}"
        got = r.json()["alert"]
        expected = c["expected_alert"]
        if got == expected:
            correct += 1
        elif expected == "DANGER" and got in ("NORMAL", "WARNING"):
            false_negatives += 1
        elif expected == "NORMAL" and got in ("WARNING", "DANGER"):
            false_positives += 1
    accuracy = correct / len(cases)
    fn_rate = false_negatives / max(1, sum(1 for c in cases if c["expected_alert"] == "DANGER"))
    print(f"\n[ground truth] {correct}/{len(cases)} correct ({accuracy:.1%})")
    print(f"[ground truth] false negatives: {false_negatives}, false positives: {false_positives}")
    print(f"[ground truth] FN rate (of DANGER cases): {fn_rate:.1%}")
    assert accuracy >= MIN_ACCURACY, f"Accuracy {accuracy:.1%} < {MIN_ACCURACY:.0%}"
    assert fn_rate <= MAX_FALSE_NEGATIVE_RATE, f"FN rate {fn_rate:.1%} > {MAX_FALSE_NEGATIVE_RATE:.0%}"
