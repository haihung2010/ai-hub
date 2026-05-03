from __future__ import annotations

import struct

import pytest

from app.services.knowledge_embedding_service import KnowledgeEmbeddingService


# ---------------------------------------------------------------------------
# similarity() — pure static, no model needed
# ---------------------------------------------------------------------------

def _pack(*vals: float) -> bytes:
    return struct.pack(f"{len(vals)}f", *vals)


@pytest.mark.unit
def test_similarity_identical_unit_vectors_returns_one() -> None:
    v = _pack(1.0, 0.0, 0.0)
    assert KnowledgeEmbeddingService.similarity(v, v) == pytest.approx(1.0, abs=1e-5)


@pytest.mark.unit
def test_similarity_orthogonal_vectors_returns_zero() -> None:
    a = _pack(1.0, 0.0)
    b = _pack(0.0, 1.0)
    assert KnowledgeEmbeddingService.similarity(a, b) == pytest.approx(0.0, abs=1e-5)


@pytest.mark.unit
def test_similarity_partial_overlap() -> None:
    # Use unit vectors: (0.6, 0.8) and (1.0, 0.0) → dot = 0.6
    a = _pack(0.6, 0.8)
    b = _pack(1.0, 0.0)
    score = KnowledgeEmbeddingService.similarity(a, b)
    assert 0.0 < score < 1.0


@pytest.mark.unit
def test_similarity_negative_dot_product_clamped_to_zero() -> None:
    a = _pack(1.0, 0.0)
    b = _pack(-1.0, 0.0)
    assert KnowledgeEmbeddingService.similarity(a, b) == 0.0


@pytest.mark.unit
def test_similarity_empty_bytes_returns_zero() -> None:
    assert KnowledgeEmbeddingService.similarity(b"", b"") == 0.0
    assert KnowledgeEmbeddingService.similarity(_pack(1.0), b"") == 0.0
    assert KnowledgeEmbeddingService.similarity(b"", _pack(1.0)) == 0.0


@pytest.mark.unit
def test_similarity_dimension_mismatch_returns_zero() -> None:
    a = _pack(1.0, 0.0)
    b = _pack(1.0, 0.0, 0.0)
    assert KnowledgeEmbeddingService.similarity(a, b) == 0.0


# ---------------------------------------------------------------------------
# embed() — requires fastembed, skip if unavailable
# ---------------------------------------------------------------------------

fastembed = pytest.importorskip("fastembed", reason="fastembed not installed")


@pytest.mark.unit
def test_embed_returns_bytes() -> None:
    svc = KnowledgeEmbeddingService()
    result = svc.embed("hello world")
    assert isinstance(result, bytes)
    assert len(result) > 0
    assert len(result) % 4 == 0


@pytest.mark.unit
def test_embed_is_deterministic() -> None:
    svc = KnowledgeEmbeddingService()
    assert svc.embed("test") == svc.embed("test")


@pytest.mark.unit
def test_embed_different_texts_produce_different_vectors() -> None:
    svc = KnowledgeEmbeddingService()
    assert svc.embed("cat") != svc.embed("dog")


@pytest.mark.unit
def test_embed_model_loaded_lazily() -> None:
    svc = KnowledgeEmbeddingService()
    assert svc._model is None
    svc.embed("trigger load")
    assert svc._model is not None


@pytest.mark.unit
def test_embed_similarity_related_texts_higher_than_unrelated() -> None:
    svc = KnowledgeEmbeddingService()
    a = svc.embed("refund policy thirty days")
    b = svc.embed("money back guarantee")
    c = svc.embed("python programming language")
    assert KnowledgeEmbeddingService.similarity(a, b) > KnowledgeEmbeddingService.similarity(a, c)
