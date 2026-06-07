import os
import pytest
from app.core.database import _get_pool
from app.services.ihi_case_saver import IHICaseSaver
from app.models.ihi import AlertLevel, AnalyzeResponse

try:
    from app.core.database import _get_pool as _try_pool
    # Use psycopg directly to test reachability without closing the singleton pool
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


@pytest.fixture(scope="module")
def db_pool():
    pool = _get_pool()
    yield pool
    # Don't close the singleton pool - other tests use it


def test_save_danger_verdict_creates_case(db_pool):
    """Saving a DANGER verdict creates a new ihi_rag_cases row."""
    saver = IHICaseSaver(db_pool)
    result = AnalyzeResponse(
        alert=AlertLevel.DANGER,
        devices=["Sensor-001"],
        case_id=None, confidence=0.8,
        symptom="battery_critical",
        narrative="Battery 4% critical, charging needed",
    )
    case_id = saver.save_verdict(
        scrape_id=99999, phase=1, sample_time="2026-06-03T12:00:00Z",
        readings={"battery_pct": 4.0, "temperature": 50.0},
        llm_result=result,
    )
    assert case_id is not None
    # Verify
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT device_id, severity, confirmed_by FROM ihi_rag_cases WHERE id = %s",
                (case_id,)
            )
            row = cur.fetchone()
            assert row["device_id"].startswith("scrape_99999")
            assert row["severity"] == "danger"
            assert row["confirmed_by"] == "auto_learned"
            # Cleanup
            cur.execute("DELETE FROM ihi_rag_cases WHERE id = %s", (case_id,))
        conn.commit()


def test_save_normal_verdict_returns_none(db_pool):
    """Saving a NORMAL verdict is skipped (avoid noise)."""
    saver = IHICaseSaver(db_pool)
    result = AnalyzeResponse(
        alert=AlertLevel.NORMAL, devices=[], case_id=None,
        confidence=0.9, symptom=None, narrative="All good",
    )
    case_id = saver.save_verdict(
        scrape_id=99999, phase=1, sample_time="2026-06-03T12:00:00Z",
        readings={}, llm_result=result,
    )
    assert case_id is None
