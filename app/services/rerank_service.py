from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RerankResult:
    index: int
    score: float


class RerankService:
    def __init__(self, base_url: str, timeout: float = 10.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def rerank(self, query: str, documents: list[str]) -> list[RerankResult]:
        """Score documents against query. Returns sorted by score desc. Fallback: original order."""
        if not documents:
            return []
        try:
            with httpx.Client(timeout=self._timeout) as client:
                resp = client.post(
                    f"{self._base_url}/v1/rerank",
                    json={"model": "bge-reranker-v2-m3", "query": query, "documents": documents},
                )
                resp.raise_for_status()
                results = [
                    RerankResult(index=r["index"], score=r["relevance_score"])
                    for r in resp.json()["results"]
                ]
                results.sort(key=lambda r: r.score, reverse=True)
                return results
        except Exception as exc:
            logger.warning("Reranker unavailable, falling back to hybrid score: %s", exc)
            return [RerankResult(index=i, score=0.0) for i in range(len(documents))]
