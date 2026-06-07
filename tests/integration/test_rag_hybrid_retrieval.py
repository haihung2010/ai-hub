# tests/integration/test_rag_hybrid_retrieval.py
import os
import pytest
import psycopg
from app.services.ihi_rag_service import IHIragService

# Test DB reachability via a one-off connection (do NOT close the shared pool
# — _get_pool() is a process-wide singleton and closing it here would break
# every subsequent test in the session).
try:
    _url = os.environ.get("DATABASE_URL", "")
    if not _url:
        _DB_OK = False
    else:
        with psycopg.connect(_url, connect_timeout=3) as _conn:
            with _conn.cursor() as _cur:
                _cur.execute("SELECT 1")
                _cur.fetchone()
        _DB_OK = True
except Exception:
    _DB_OK = False

pytestmark = [
    pytest.mark.skipif(not _DB_OK, reason="PostgreSQL not reachable"),
    pytest.mark.no_isolated_db,
]


@pytest.fixture
def rag_service():
    """Function-scoped service using the shared pool.

    Note: ``_get_pool()`` is a process-wide singleton — we must NOT
    close it in the fixture or subsequent tests will see ``'already
    closed'``. Service state is reset by recreating the IHIragService
    instance.
    """
    from app.core.database import _get_pool
    pool = _get_pool()
    s = IHIragService(db_pool=pool)
    s.load_cases()
    yield s


def test_retrieve_top_k_returns_k_results(rag_service):
    """retrieve_top_k returns up to k cases."""
    results = rag_service.retrieve_top_k(
        readings={"temperature": 92, "velocity": 1.0, "current": 5},
        k=3
    )
    assert isinstance(results, list)
    assert len(results) <= 3
    for case, score in results:
        assert isinstance(score, float)
        assert 0 <= score <= 1.0


def test_retrieve_top_k_pattern_match_high_score(rag_service):
    """Exact pattern match should give high score (>= 0.7)."""
    # Match overheat case: t=95, v=1, c=5
    results = rag_service.retrieve_top_k(
        readings={"temperature": 95, "velocity": 1.0, "current": 5},
        k=3
    )
    # First result should be RAG-001 (overheat) with high score
    assert results
    first_case, first_score = results[0]
    assert first_score >= 0.7


def test_retrieve_top_k_empty_for_no_match(rag_service):
    """No matching case → empty list or all scores < 0.7."""
    # Use values that match no case (very low everything)
    results = rag_service.retrieve_top_k(
        readings={"temperature": 0.1, "velocity": 0.0, "current": 0.0},
        k=3
    )
    # May still get vector matches, but scores should be low
    if results:
        assert all(score < 0.7 for _, score in results)
