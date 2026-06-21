#!/usr/bin/env python3
"""A/B benchmark for Anthropic Contextual Retrieval (2026-06-19).

Runs the same set of Vietnamese queries against the AI Hub knowledge
search API twice — once with Contextual Retrieval disabled (baseline)
and once with it enabled (after reindex) — and computes retrieval
quality metrics:

  - hit_rate@5  : fraction of queries where the expected card appears
                   in the top 5 results.
  - hit_rate@10 : same, top 10.
  - MRR         : mean reciprocal rank of the first relevant result.
  - nDCG@10     : normalized discounted cumulative gain at rank 10.

The Anthropic blog reports baseline 5.7% failure rate → 1.9% with
Contextual Retrieval + Reranking (a 67% reduction). On our Vietnamese
eval we expect smaller gains because E4B Q4 is weaker than Claude
Sonnet, but the directional improvement should be visible.

Output:
  reports/contextual_<label>.json — raw per-query results + summary
  reports/contextual_diff.json     — side-by-side comparison (with --diff)

Usage:
  # 1. Baseline (Contextual Retrieval off, no LLM reindex)
  ./venv/bin/python evals/bench_contextual_retrieval.py \\
      --label before \\
      --output reports/contextual_before.json

  # 2. Enable Contextual Retrieval in .env, then reindex
  echo "ENABLE_LLM_CONTEXTUALIZER=true" >> .env
  ./venv/bin/python scripts/reindex_knowledge.py --force --contextualize

  # 3. Post (Contextual Retrieval on)
  ./venv/bin/python evals/bench_contextual_retrieval.py \\
      --label after \\
      --output reports/contextual_after.json

  # 4. Diff
  ./venv/bin/python evals/bench_contextual_retrieval.py \\
      --diff reports/contextual_before.json reports/contextual_after.json
"""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# Make the project root importable
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


API_BASE = os.environ.get("AIHUB_API_BASE", "http://localhost:8000")
API_KEY = os.environ.get("AIHUB_API_KEY", "")


def _post_search(query: str, *, limit: int = 10) -> list[dict]:
    """POST /v1/knowledge/search. Returns the `results` list."""
    body = json.dumps(
        {
            "query": query,
            "limit": limit,
            # Tenant defaults to "default" if the eval needs wider scope
            # the operator can override via the env var AIHUB_EVAL_TENANT.
            "tenant_id": os.environ.get("AIHUB_EVAL_TENANT", "default"),
            "project_id": os.environ.get("AIHUB_EVAL_PROJECT", "default"),
        }
    ).encode()
    req = urllib.request.Request(
        f"{API_BASE}/v1/knowledge/search",
        data=body,
        method="POST",
    )
    req.add_header("Content-Type", "application/json")
    req.add_header("X-API-KEY", API_KEY)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        # The endpoint may be /v1/knowledge/cards with a query param on
        # older builds. Fall back gracefully.
        if exc.code in (404, 405):
            return []
        raise
    return data.get("results", [])


def _hit_at_k(expected_card_id: str, results: list[dict], k: int) -> bool:
    return any(r.get("card_id") == expected_card_id for r in results[:k])


def _mrr(expected_card_id: str, results: list[dict]) -> float:
    for i, r in enumerate(results, start=1):
        if r.get("card_id") == expected_card_id:
            return 1.0 / i
    return 0.0


def _ndcg_at_k(expected_card_id: str, results: list[dict], k: int) -> float:
    """Binary relevance: 1 if the chunk is from the expected card, else 0."""
    gains = [1.0 if r.get("card_id") == expected_card_id else 0.0 for r in results[:k]]
    discounts = [1.0 / math.log2(i + 2) for i in range(len(gains))]
    dcg = sum(g * d for g, d in zip(gains, discounts))
    # Ideal: relevant result at position 1
    idcg = 1.0 / math.log2(2) if any(gains) else 0.0
    return (dcg / idcg) if idcg > 0 else 0.0


def run_benchmark(eval_path: Path, *, label: str, judge: bool = False) -> dict:
    rows = [json.loads(line) for line in eval_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not rows:
        print(f"ERROR: eval file is empty: {eval_path}", file=sys.stderr)
        sys.exit(1)

    per_query: list[dict] = []
    hit_at_5: list[float] = []
    hit_at_10: list[float] = []
    mrrs: list[float] = []
    ndcgs: list[float] = []
    latencies_ms: list[float] = []

    for row in rows:
        t0 = time.perf_counter()
        results = _post_search(row["input"], limit=10)
        latency_ms = (time.perf_counter() - t0) * 1000.0
        latencies_ms.append(latency_ms)

        h5 = _hit_at_k(row["expected_card_id"], results, 5)
        h10 = _hit_at_k(row["expected_card_id"], results, 10)
        mrr = _mrr(row["expected_card_id"], results)
        ndcg = _ndcg_at_k(row["expected_card_id"], results, 10)
        top_card_id = results[0].get("card_id", "") if results else ""

        hit_at_5.append(1.0 if h5 else 0.0)
        hit_at_10.append(1.0 if h10 else 0.0)
        mrrs.append(mrr)
        ndcgs.append(ndcg)

        per_query.append(
            {
                "id": row["id"],
                "query": row["input"],
                "expected_card_id": row["expected_card_id"],
                "top_card_id": top_card_id,
                "rank": next(
                    (i for i, r in enumerate(results, start=1) if r.get("card_id") == row["expected_card_id"]),
                    None,
                ),
                "hit_at_5": h5,
                "hit_at_10": h10,
                "mrr": mrr,
                "ndcg_at_10": ndcg,
                "latency_ms": round(latency_ms, 1),
            }
        )

    summary = {
        "label": label,
        "n_queries": len(rows),
        "hit_rate_at_5": round(statistics.mean(hit_at_5), 4),
        "hit_rate_at_10": round(statistics.mean(hit_at_10), 4),
        "mrr": round(statistics.mean(mrrs), 4),
        "ndcg_at_10": round(statistics.mean(ndcgs), 4),
        "latency_p50_ms": round(statistics.median(latencies_ms), 1),
        "latency_p95_ms": round(
            sorted(latencies_ms)[max(0, int(len(latencies_ms) * 0.95) - 1)], 1
        ),
    }

    if judge:
        try:
            from evals.judges.llm_judge_vi import judge as _judge  # type: ignore
        except ImportError:
            print(
                "Warning: evals.judges.llm_judge_vi not available; skipping judge",
                file=sys.stderr,
            )
        except Exception as exc:  # pragma: no cover - defensive
            print(
                f"Warning: failed to import judge module: {exc}",
                file=sys.stderr,
            )
        else:
            for r in per_query:
                if "judge" in r:
                    continue
                try:
                    r["judge"] = _judge(
                        query=r["query"],
                        retrieved_card_id=r.get("top_card_id", ""),
                        expected_card_id=r.get("expected_card_id", ""),
                    )
                except Exception as exc:
                    print(
                        f"Warning: judge failed for query id={r.get('id')}: {exc}",
                        file=sys.stderr,
                    )
                    r["judge"] = {"error": str(exc)}

    return {"summary": summary, "per_query": per_query}


def print_summary(name: str, summary: dict) -> None:
    print(
        f"\n=== {name} ===\n"
        f"  n_queries:        {summary['n_queries']}\n"
        f"  hit_rate@5:       {summary['hit_rate_at_5']:.4f}\n"
        f"  hit_rate@10:      {summary['hit_rate_at_10']:.4f}\n"
        f"  MRR:              {summary['mrr']:.4f}\n"
        f"  nDCG@10:          {summary['ndcg_at_10']:.4f}\n"
        f"  latency_p50_ms:   {summary['latency_p50_ms']:.1f}\n"
        f"  latency_p95_ms:   {summary['latency_p95_ms']:.1f}\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="A/B benchmark for Anthropic Contextual Retrieval",
    )
    parser.add_argument(
        "--eval",
        type=Path,
        default=Path(__file__).parent / "data" / "knowledge_rag_eval.jsonl",
        help="Eval JSONL file",
    )
    parser.add_argument("--label", default="run", help="Label for this run")
    parser.add_argument("--output", type=Path, help="Output JSON file")
    parser.add_argument(
        "--diff",
        nargs=2,
        type=Path,
        metavar=("BEFORE", "AFTER"),
        help="Compare two prior benchmark JSON files",
    )
    parser.add_argument(
        "--judge",
        action="store_true",
        help="Run LLM-as-judge (requires E4B Q4 on port 8081)",
    )
    parser.add_argument(
        "--langfuse-dataset",
        default=None,
        help="If set, also run as Langfuse dataset experiment",
    )
    args = parser.parse_args()

    if args.diff:
        before = json.loads(args.diff[0].read_text(encoding="utf-8"))
        after = json.loads(args.diff[1].read_text(encoding="utf-8"))
        print_summary(args.diff[0].stem, before["summary"])
        print_summary(args.diff[1].stem, after["summary"])
        print("=== Delta (after − before) ===")
        for key in ("hit_rate_at_5", "hit_rate_at_10", "mrr", "ndcg_at_10"):
            delta = after["summary"][key] - before["summary"][key]
            print(f"  {key:18s} {delta:+.4f}")
        delta_p95 = after["summary"]["latency_p95_ms"] - before["summary"]["latency_p95_ms"]
        print(f"  {'latency_p95_ms':18s} {delta_p95:+.1f}")
        return

    if not API_KEY:
        print(
            "ERROR: AIHUB_API_KEY env var not set. Set it to a valid API key.",
            file=sys.stderr,
        )
        sys.exit(1)

    result = run_benchmark(args.eval, label=args.label, judge=args.judge)
    print_summary(args.label, result["summary"])

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nWrote {args.output}")

    if args.langfuse_dataset:
        try:
            from langfuse import Langfuse  # type: ignore

            lf = Langfuse(
                public_key=os.environ["LANGFUSE_PUBLIC_KEY"],
                secret_key=os.environ["LANGFUSE_SECRET_KEY"],
                host=os.environ.get("LANGFUSE_HOST", "http://localhost:3000"),
            )
            with lf.start_as_current_span(
                name=f"contextual_retrieval_{args.label}"
            ) as span:
                span.set_attribute("eval.label", args.label)
                for r in result["per_query"]:
                    expected = r.get("expected_card_id")
                    top = r.get("top_card_id")
                    if expected and top:
                        lf.score_current_span(
                            name="hit_rate_top1",
                            value=1.0 if top == expected else 0.0,
                        )
            lf.flush()
        except ImportError:
            print(
                "Warning: langfuse not installed; skipping dataset push",
                file=sys.stderr,
            )
        except Exception as exc:
            print(
                f"Warning: langfuse dataset push failed: {exc}",
                file=sys.stderr,
            )


if __name__ == "__main__":
    main()
