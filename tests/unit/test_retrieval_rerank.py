"""Tests for retrieval service rerank behavior.

Anthropic Contextual Retrieval (2026-06-19) recommends reranking the
top candidates ALWAYS, not just when the vector-search top score is low.
The pre-existing threshold check (`top_score < 0.85` → skip rerank) is a
fast path that trades a small amount of accuracy for ~1 saved RTT to
the reranker. With Contextual Retrieval we accept the extra RTT in
exchange for the documented −67% retrieval failure rate.

These tests focus on the rerank decision logic in isolation, not the
hybrid SQL. The DB-touching parts of the retrieval service are covered
by integration tests (test_rag_hybrid_retrieval.py).
"""

from __future__ import annotations

import pytest

from app.services.knowledge_retrieval_service import KnowledgeRetrievalService


# Pure unit tests — no DB. Skip the autouse isolated_db fixture.
pytestmark = pytest.mark.no_isolated_db


# ── Stubs ──────────────────────────────────────────────────────────


class _StubRerankService:
    """Records every rerank() call and returns a configurable reordering.

    The real RerankService.rerank() returns a list of objects with an
    `index` attribute pointing back into the input docs list. We mirror
    that contract here so the retrieval service can use `[rr.index]`
    indexing unchanged.
    """

    def __init__(self, reorder_to: list[int] | None = None) -> None:
        # reorder_to[i] = original index that the i-th reranked slot points to.
        # Default: keep original order.
        self._reorder = reorder_to
        self.calls: list[tuple[str, list[str]]] = []

    def rerank(self, query: str, docs: list[str]):
        self.calls.append((query, docs))
        if self._reorder is None:
            return [_Idx(i) for i in range(len(docs))]
        return [_Idx(i) for i in self._reorder]


class _Idx:
    def __init__(self, index: int) -> None:
        self.index = index


class _StubResult:
    """Stand-in for KnowledgeSearchResult — only the attributes the
    rerank path actually uses."""

    def __init__(self, content: str, score: float) -> None:
        self.content = content
        self.score = score


# ── _apply_rerank decision logic ──────────────────────────────────


class TestApplyRerank:
    @pytest.mark.unit
    def test_rerank_always_runs_even_when_top_score_is_high(self) -> None:
        """Anthropic: rerank the top 20 always, regardless of vector
        top score. The pre-existing < 0.85 threshold is removed."""
        svc = KnowledgeRetrievalService()
        rerank = _StubRerankService()
        svc._rerank = rerank  # type: ignore[assignment]

        # Top result has a very high score (0.95) — under the OLD
        # threshold check, this would have skipped rerank. Under the
        # new behavior, rerank MUST still run.
        results = [
            _StubResult("doc A", 0.95),
            _StubResult("doc B", 0.80),
            _StubResult("doc C", 0.60),
        ]

        out = svc._apply_rerank(results, "test query")  # type: ignore[arg-type]

        assert len(rerank.calls) == 1, "rerank must be called even when top score is high"
        # Default reorder is identity, so order is preserved
        assert [r.content for r in out] == ["doc A", "doc B", "doc C"]

    @pytest.mark.unit
    def test_rerank_uses_top_20_candidates(self) -> None:
        """Only the top 20 candidates go to the reranker (Anthropic's
        recommendation). Larger candidate sets slow the reranker with
        diminishing returns on accuracy."""
        svc = KnowledgeRetrievalService()
        rerank = _StubRerankService()
        svc._rerank = rerank  # type: ignore[assignment]

        # 30 fake results
        results = [
            _StubResult(f"doc {i}", 0.9 - i * 0.01) for i in range(30)
        ]

        svc._apply_rerank(results, "test query")  # type: ignore[arg-type]

        # The rerank service should have received exactly 20 docs
        assert len(rerank.calls[0][1]) == 20
        # And those should be the top 20 by score
        assert rerank.calls[0][1][0] == "doc 0"
        assert rerank.calls[0][1][-1] == "doc 19"

    @pytest.mark.unit
    def test_rerank_reorders_results(self) -> None:
        """When the reranker returns a different ordering, retrieval
        must reflect that order in the returned results."""
        svc = KnowledgeRetrievalService()
        # Force reorder: rerank says doc 2 is most relevant, then 0, then 1
        rerank = _StubRerankService(reorder_to=[2, 0, 1])
        svc._rerank = rerank  # type: ignore[assignment]

        results = [
            _StubResult("doc A", 0.95),
            _StubResult("doc B", 0.80),
            _StubResult("doc C", 0.60),
        ]

        out = svc._apply_rerank(results, "test query")  # type: ignore[arg-type]

        assert [r.content for r in out] == ["doc C", "doc A", "doc B"]

    @pytest.mark.unit
    def test_no_rerank_service_returns_results_unchanged(self) -> None:
        """When no rerank service is configured, results pass through."""
        svc = KnowledgeRetrievalService()
        svc._rerank = None  # explicit off

        results = [
            _StubResult("doc A", 0.95),
            _StubResult("doc B", 0.80),
        ]

        out = svc._apply_rerank(results, "test query")  # type: ignore[arg-type]

        assert [r.content for r in out] == ["doc A", "doc B"]

    @pytest.mark.unit
    def test_empty_results_returns_empty(self) -> None:
        """No candidates → no rerank call → empty result."""
        svc = KnowledgeRetrievalService()
        rerank = _StubRerankService()
        svc._rerank = rerank  # type: ignore[assignment]

        out = svc._apply_rerank([], "test query")  # type: ignore[arg-type]

        assert out == []
        assert len(rerank.calls) == 0

    @pytest.mark.unit
    def test_rerank_exception_falls_back_to_hybrid_results(self) -> None:
        """If the reranker 5xx's or times out, retrieval falls back to
        the hybrid vector+FTS results rather than failing the whole
        request. The Anthropic recipe says the cost of a bad rerank
        call is a slightly worse top-20 — much better than a 5xx."""
        class _BoomRerank:
            def rerank(self, _query, _docs):
                raise RuntimeError("reranker connection refused")

        svc = KnowledgeRetrievalService()
        svc._rerank = _BoomRerank()  # type: ignore[assignment]

        results = [
            _StubResult("doc A", 0.95),
            _StubResult("doc B", 0.80),
        ]

        out = svc._apply_rerank(results, "test query")  # type: ignore[arg-type]

        # Fallback returns the original order
        assert [r.content for r in out] == ["doc A", "doc B"]
