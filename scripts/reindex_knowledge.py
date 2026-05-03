#!/usr/bin/env python3
"""Back-fill embeddings for knowledge chunks that were ingested without a vector.

Usage:
    python scripts/reindex_knowledge.py [--tenant TENANT] [--project PROJECT] [--batch N]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Make the project root importable
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings
from app.core.database import init_db
from app.services.knowledge_embedding_service import KnowledgeEmbeddingService
from app.services.knowledge_ingestion_service import KnowledgeIngestionService


def main() -> None:
    parser = argparse.ArgumentParser(description="Re-index knowledge chunk embeddings")
    parser.add_argument("--tenant", default=None, help="Filter by tenant_id")
    parser.add_argument("--project", default=None, help="Filter by project_id")
    parser.add_argument("--batch", type=int, default=50, help="Max chunks to process (default 50)")
    args = parser.parse_args()

    settings = get_settings()
    init_db()

    print(f"Loading embedding model: {settings.knowledge_embedding_model}")
    embedding = KnowledgeEmbeddingService(model_name=settings.knowledge_embedding_model)
    ingestion = KnowledgeIngestionService(
        chunk_chars=settings.knowledge_chunk_chars,
        max_card_chars=settings.knowledge_max_card_chars,
        embedding_service=embedding,
    )

    print(f"Scanning for chunks without embeddings (tenant={args.tenant!r}, project={args.project!r}, batch={args.batch})")
    result = ingestion.fill_missing_embeddings(
        tenant_id=args.tenant,
        project_id=args.project,
        batch_size=args.batch,
    )

    print(f"Done — total: {result['total']}, updated: {result['updated']}, skipped: {result.get('skipped', 0)}")
    if result.get("error"):
        print(f"Error: {result['error']}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
