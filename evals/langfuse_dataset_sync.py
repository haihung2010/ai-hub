#!/usr/bin/env python3
"""Sync local JSONL eval datasets to Langfuse Datasets API.

Usage:
  LANGFUSE_ENABLED=true ./venv/bin/python evals/langfuse_dataset_sync.py \\
      --dataset contextual_retrieval_vi \\
      --file evals/datasets/contextual_retrieval_vi.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True, help="Langfuse dataset name")
    parser.add_argument("--file", required=True, type=Path, help="Local JSONL file")
    args = parser.parse_args()

    if os.environ.get("LANGFUSE_ENABLED", "false").lower() != "true":
        print("LANGFUSE_ENABLED must be true to sync datasets", file=sys.stderr)
        return 1

    from langfuse import Langfuse
    langfuse = Langfuse(
        public_key=os.environ["LANGFUSE_PUBLIC_KEY"],
        secret_key=os.environ["LANGFUSE_SECRET_KEY"],
        host=os.environ.get("LANGFUSE_HOST", "http://localhost:3000"),
    )

    items = []
    with args.file.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            items.append(json.loads(line))

    # Upsert dataset
    langfuse.create_dataset(name=args.dataset)
    for item in items:
        langfuse.create_dataset_item(
            dataset_name=args.dataset,
            input={"query": item["query"]},
            expected_output={"card_id": item["expected_card_id"]},
            metadata={"domain": item.get("domain", "unknown")},
        )

    print(f"Synced {len(items)} items to dataset '{args.dataset}'")
    langfuse.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())