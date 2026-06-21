#!/usr/bin/env python3
"""Back-fill embeddings for knowledge chunks that were ingested without a vector.

Usage:
    python scripts/reindex_knowledge.py [--tenant TENANT] [--project PROJECT] [--batch N]
    python scripts/reindex_knowledge.py --force --contextualize  # LLM re-contextualize everything
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Make the project root importable
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings
from app.core.database import init_db
from app.services.contextualizer import Contextualizer, LlamaCppContextualizerProvider
from app.services.knowledge_embedding_service import KnowledgeEmbeddingService
from app.services.knowledge_ingestion_service import KnowledgeIngestionService
from app.services.providers.llama_cpp import LlamaCppProvider


def main() -> None:
    parser = argparse.ArgumentParser(description="Re-index knowledge chunk embeddings")
    parser.add_argument("--tenant", default=None, help="Filter by tenant_id")
    parser.add_argument("--project", default=None, help="Filter by project_id")
    parser.add_argument("--batch", type=int, default=50, help="Max chunks to process (default 50)")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-embed every matching chunk (even ones with non-NULL embedding).",
    )
    parser.add_argument(
        "--contextualize",
        action="store_true",
        help="Generate LLM context for each chunk via the Contextualizer. "
        "Only effective when ENABLE_LLM_CONTEXTUALIZER=true and E4B on :8081 is up.",
    )
    args = parser.parse_args()

    settings = get_settings()
    init_db()

    print(f"Loading embedding model: {settings.knowledge_embedding_model}")
    embedding = KnowledgeEmbeddingService(model_name=settings.knowledge_embedding_model)

    # Build the Contextualizer if --contextualize was passed AND the
    # config flag is on. If config is off but --contextualize is set,
    # warn and fall back to the deterministic header.
    contextualizer: Contextualizer | None = None
    if args.contextualize:
        if not settings.enable_llm_contextualizer:
            print(
                "WARNING: --contextualize set but ENABLE_LLM_CONTEXTUALIZER=false. "
                "Falling back to deterministic header. "
                "Set ENABLE_LLM_CONTEXTUALIZER=true and BACKGROUND_LLAMA_CPP_ENABLED=true to use E4B.",
                file=sys.stderr,
            )
        else:
            try:
                import httpx
                client = httpx.AsyncClient(
                    timeout=settings.contextualizer_timeout_seconds
                )
                llama = LlamaCppProvider(
                    client=client,
                    openai_url=settings.contextualizer_url
                    or settings.background_llama_cpp_openai_url,
                )
                adapter = LlamaCppContextualizerProvider(
                    llama_cpp_provider=llama,
                    model=settings.contextualizer_model,
                )
                contextualizer = Contextualizer(
                    provider=adapter,
                    model=settings.contextualizer_model,
                    max_context_tokens=settings.contextualizer_max_context_tokens,
                    timeout_seconds=settings.contextualizer_timeout_seconds,
                )
                print(
                    f"Contextualizer enabled: model={settings.contextualizer_model} "
                    f"max_tokens={settings.contextualizer_max_context_tokens} "
                    f"timeout={settings.contextualizer_timeout_seconds}s"
                )
            except Exception as exc:
                print(f"WARNING: Contextualizer init failed: {exc!r}", file=sys.stderr)

    ingestion = KnowledgeIngestionService(
        chunk_chars=settings.knowledge_chunk_chars,
        max_card_chars=settings.knowledge_max_card_chars,
        embedding_service=embedding,
        contextualizer=contextualizer,
    )

    mode = "force re-embed all" if args.force else "embed only NULL-embedding chunks"
    ctx = "with LLM contextualization" if contextualizer else "with deterministic header"
    print(
        f"Scanning ({mode}, {ctx}) — "
        f"tenant={args.tenant!r}, project={args.project!r}, batch={args.batch}"
    )
    result = ingestion.fill_missing_embeddings(
        tenant_id=args.tenant,
        project_id=args.project,
        batch_size=args.batch,
        force=args.force,
        contextualize=args.contextualize,
    )

    print(
        f"Done — total: {result['total']}, updated: {result['updated']}, "
        f"skipped: {result.get('skipped', 0)}"
    )
    if result.get("error"):
        print(f"Error: {result['error']}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
