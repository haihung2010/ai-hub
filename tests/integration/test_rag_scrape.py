"""Integration test: measure retrieval hit-rate on the scraped knowledge base.

These tests run against the *current* PostgreSQL state (i.e., whatever cards
have been ingested by the latest `scrape_rag.py` run). They do not depend on
any specific run — they just measure the live retrieval quality.

Test categories:
- ``test_retrieval_i``: IHI standards domain — 5 queries derived from the
  scrape's source content. Each query must return at least one card from the
  ``ihi-standards`` domain in the top-5.
- ``test_retrieval_vi``: Vietnamese fanpage — 5 Vietnamese queries that must
  return at least one ``vi-fanpage`` card.
- ``test_retrieval_quality``: a more general sanity check that a known
  card appears near the top.

These are *integration* tests: they require DATABASE_URL and an ingested
knowledge base. If the KB is empty, all assertions are skipped (xfail).

Opt-out of the autouse ``isolated_db`` fixture because this test must
exercise the *real* (non-truncated) knowledge base.
"""
from __future__ import annotations

import os
import sys

import pytest

# Make app importable
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from app.core.database import get_db_connection  # noqa: E402
from app.services.knowledge_embedding_service import KnowledgeEmbeddingService  # noqa: E402
from app.services.knowledge_retrieval_service import KnowledgeRetrievalService  # noqa: E402

# Don't truncate the live knowledge base — the whole point is to test what's there.
pytestmark = [pytest.mark.no_isolated_db]


def _kb_size() -> int:
    with get_db_connection() as conn:
        return conn.execute("SELECT COUNT(*) AS n FROM knowledge_cards").fetchone()["n"]


def _make_retrieval() -> KnowledgeRetrievalService:
    embedding = KnowledgeEmbeddingService()
    return KnowledgeRetrievalService(embedding_service=embedding)


@pytest.fixture(scope="module")
def retrieval() -> KnowledgeRetrievalService:
    return _make_retrieval()


# Skip everything if no cards at all (unless RUN_LIVE=1, in which case it's a hard fail)
pytestmark_skip_empty = pytest.mark.skipif(
    os.environ.get("RUN_LIVE") != "1" and _kb_size() == 0,
    reason="Knowledge KB is empty — run scripts/scrape_rag.py first",
)
pytestmark.append(pytestmark_skip_empty)


# ---------------------------------------------------------------------------
# IHI standards queries
# ---------------------------------------------------------------------------

IHI_QUERIES = [
    ("ISO 10816 vibration evaluation", "ihi-standards"),
    ("NEMA MG-1 motor vibration", "ihi-standards"),
    ("IEEE 1159 power quality", "ihi-standards"),
    ("IEC 61000 harmonics compatibility", "ihi-standards"),
    ("rotating machinery condition monitoring", "ihi-standards"),
]


@pytest.mark.parametrize("query,expected_domain", IHI_QUERIES)
def test_ihi_query_returns_relevant_card(query, expected_domain, retrieval):
    """Each IHI query must return at least one card from the expected domain in top-5."""
    results = retrieval.search(
        tenant_id="default",
        project_id="default",
        query=query,
        limit=5,
    )
    domains_hit = {r.knowledge_domain for r in results}
    assert expected_domain in domains_hit, (
        f"Query {query!r} returned no {expected_domain} card in top-5. "
        f"Got: {[(r.title[:50], r.knowledge_domain) for r in results]}"
    )


# ---------------------------------------------------------------------------
# Vietnamese fanpage queries
# ---------------------------------------------------------------------------

VI_QUERIES = [
    ("chính sách đổi trả hàng", "vi-fanpage"),
    ("phí vận chuyển giao hàng", "vi-fanpage"),
    ("bảo hành sản phẩm", "vi-fanpage"),
    ("đặt hàng qua Messenger", "vi-fanpage"),
    ("mã giảm giá freeship", "vi-fanpage"),
]


@pytest.mark.parametrize("query,expected_domain", VI_QUERIES)
def test_vi_query_returns_relevant_card(query, expected_domain, retrieval):
    """Each Vietnamese query must return at least one vi-fanpage card in top-5."""
    results = retrieval.search(
        tenant_id="default",
        project_id="default",
        query=query,
        limit=5,
    )
    domains_hit = {r.knowledge_domain for r in results}
    assert expected_domain in domains_hit, (
        f"Query {query!r} returned no {expected_domain} card in top-5. "
        f"Got: {[(r.title[:50], r.knowledge_domain) for r in results]}"
    )


# ---------------------------------------------------------------------------
# Pre vs post hit-rate comparison
# ---------------------------------------------------------------------------

ALL_QUERIES = [(q, d) for q, d in IHI_QUERIES] + [(q, d) for q, d in VI_QUERIES]


def test_hit_rate_summary(retrieval, kb_size=None):
    """Compute overall hit-rate: % of queries that return at least one card from the expected domain in top-5."""
    if _kb_size() == 0:
        pytest.skip("No cards ingested")

    hits = 0
    for query, expected_domain in ALL_QUERIES:
        results = retrieval.search(
            tenant_id="default",
            project_id="default",
            query=query,
            limit=5,
        )
        if any(r.knowledge_domain == expected_domain for r in results):
            hits += 1

    hit_rate = hits / len(ALL_QUERIES)
    print(f"\n[hit-rate] {hits}/{len(ALL_QUERIES)} = {hit_rate:.0%} on {_kb_size()} cards")
    # Threshold: at least 60% of queries should hit. (Could be higher; this
    # is a smoke threshold — anything less and the scrape needs review.)
    assert hit_rate >= 0.6, f"hit-rate {hit_rate:.0%} below 60% threshold"


def test_kb_has_both_domains():
    """Sanity: the scrape should populate both ihi-standards and vi-fanpage."""
    with get_db_connection() as conn:
        rows = conn.execute(
            "SELECT knowledge_domain, COUNT(*) AS n FROM knowledge_cards GROUP BY 1"
        ).fetchall()
    domains = {r["knowledge_domain"]: r["n"] for r in rows}
    assert "ihi-standards" in domains, f"missing ihi-standards: {domains}"
    assert "vi-fanpage" in domains, f"missing vi-fanpage: {domains}"
    assert domains["ihi-standards"] >= 10, f"too few ihi-standards: {domains['ihi-standards']}"
    assert domains["vi-fanpage"] >= 10, f"too few vi-fanpage: {domains['vi-fanpage']}"
