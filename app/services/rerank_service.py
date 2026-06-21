from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from app.services.observability import ObservabilityService

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RerankResult:
    index: int
    score: float


class RerankService:
    def __init__(
        self,
        base_url: str,
        timeout: float = 10.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._client = client
        self._owns_client = client is None

    @ObservabilityService.instance().observe("retrieval.rerank")
    async def rerank(self, query: str, documents: list[str]) -> list[RerankResult]:
        """Score documents against query. Returns sorted by score desc. Fallback: original order."""
        if not documents:
            return []
        client = self._client or httpx.AsyncClient(timeout=self._timeout)
        try:
            try:
                resp = await client.post(
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
            finally:
                if self._owns_client:
                    await client.aclose()
        except Exception as exc:
            logger.warning("Reranker unavailable, falling back to hybrid score: %s", exc)
            return [RerankResult(index=i, score=0.0) for i in range(len(documents))]
