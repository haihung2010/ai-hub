"""Stub tracing service for the RAG-scrape worktree.

The real implementation lives on ``feat/ihi-rag-optimization`` and is
written by another agent. This stub lets the existing
``tests/conftest.py`` import ``app.main`` without crashing. It is NOT
loaded by the RAG-scrape ingest path (which uses
``knowledge_ingestion_service`` and ``knowledge_embedding_service``
directly). When the IHI branch merges into main, this stub can be
removed.

To activate the real implementation: copy
``feat/ihi-rag-optimization:app/services/tracing_service.py`` over this
file. Until then, ``is_enabled()`` always returns False, which is the
safe default.
"""
from __future__ import annotations


def is_enabled() -> bool:
    """Stub: tracing is off in this worktree."""
    return False


def init_tracer(*args, **kwargs):  # pragma: no cover - stub
    return None


def shutdown(*args, **kwargs):  # pragma: no cover - stub
    return None
