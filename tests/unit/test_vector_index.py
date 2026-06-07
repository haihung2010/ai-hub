"""Tests for the IHI vector index (FastEmbed + cosine similarity).

These are pure CPU/inference tests — opt out of the ``isolated_db`` autouse
fixture via the ``no_isolated_db`` marker. The first embed call will trigger
a model download + load (30-60s on a cold cache); the test must run with
a generous timeout (we use ``--timeout=120`` in CI/local).
"""
import pytest

from app.services.vector_index import IHIVectorIndex

pytestmark = pytest.mark.no_isolated_db


def test_embed_returns_384_dim_vector():
    """FastEmbed paraphrase-multilingual-MiniLM-L12-v2 returns 384-dim embeddings."""
    idx = IHIVectorIndex()
    emb = idx.embed("test text")
    assert len(emb) == 384
    assert isinstance(emb[0], float)


def test_cosine_similarity_identical():
    """Identical vectors -> similarity 1.0."""
    idx = IHIVectorIndex()
    v1 = idx.embed("vibration too high")
    v2 = idx.embed("vibration too high")
    sim = idx.cosine_similarity(v1, v2)
    assert 0.99 < sim <= 1.0


def test_cosine_similarity_unrelated():
    """Unrelated texts -> low similarity."""
    idx = IHIVectorIndex()
    v1 = idx.embed("vibration too high")
    v2 = idx.embed("pho bo tai")
    sim = idx.cosine_similarity(v1, v2)
    assert sim < 0.5
