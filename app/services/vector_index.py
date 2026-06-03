"""FastEmbed-based vector index for IHI RAG case descriptions.

Model: sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
(same as knowledge retrieval)
Output dimension: 384
"""
from __future__ import annotations

import math

# Lazy load FastEmbed to avoid slow import on app startup
_embedder = None


def _get_embedder():
    global _embedder
    if _embedder is None:
        from fastembed import TextEmbedding
        _embedder = TextEmbedding(model_name="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
    return _embedder


class IHIVectorIndex:
    """Vector index for IHI RAG case descriptions.

    Wraps FastEmbed for embedding + cosine similarity for ranking.
    Storage in PG `ihi_case_embeddings` (vector(384)) is done separately
    by IHIRagService.
    """

    def embed(self, text: str) -> list[float]:
        """Embed a single text -> 384-dim vector."""
        embedder = _get_embedder()
        result = list(embedder.embed([text]))[0]
        return [float(x) for x in result]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed a list of texts (more efficient than one-by-one)."""
        embedder = _get_embedder()
        return [[float(x) for x in v] for v in embedder.embed(texts)]

    @staticmethod
    def cosine_similarity(a: list[float], b: list[float]) -> float:
        """Compute cosine similarity between two vectors."""
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(y * y for y in b))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)
