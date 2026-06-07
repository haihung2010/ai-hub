"""Generate final_comparison.md from JSON results."""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# Allow import of bench_metrics
sys.path.insert(0, str(Path(__file__).parent))
from bench_metrics import rank_configs


def load_results(reports_dir: Path) -> list[dict]:
    """Load all *_basic.json files (Stage A results) from reports dir."""
    results = []
    for path in sorted(reports_dir.glob("*_basic.json")):
        try:
            data = json.loads(path.read_text())
            results.append(data)
        except (json.JSONDecodeError, OSError) as e:
            print(f"WARN: skipping {path}: {e}")
    return results


def load_stage_b_results(reports_dir: Path) -> list[dict]:
    """Load all *_max_load.json files (Stage B results) from reports dir."""
    results = []
    for path in sorted(reports_dir.glob("*_max_load.json")):
        try:
            data = json.loads(path.read_text())
            results.append(data)
        except (json.JSONDecodeError, OSError) as e:
            print(f"WARN: skipping {path}: {e}")
    return results


def format_comparison_table(results: list[dict]) -> str:
    """Format the markdown comparison table."""
    ranked = rank_configs([{
        "name": r["config"],
        "peak_tok_s": r.get("aggregate", {}).get("peak_tok_s", 0),
        "p95_latency_at_20": r.get("aggregate", {}).get("p95_latency_at_20", 0),
        "quality": r.get("quality", 0),
    } for r in results])

    lines = [
        "| Rank | Config | Peak tok/s | p95 Latency @20 users (ms) | Quality (1-10) | Composite |",
        "|------|--------|-----------|--------------------------|----------------|-----------|",
    ]
    for i, r in enumerate(ranked, 1):
        name = r["name"]
        tok = r.get("peak_tok_s", 0)
        lat = r.get("p95_latency_at_20", 0)
        qual = r.get("quality", 0)
        score = r.get("composite_score", 0)
        marker = " 🏆" if i == 1 else ""
        lines.append(f"| {i}{marker} | {name} | {tok} | {lat} | {qual} | {score} |")

    winner = ranked[0] if ranked else None
    return "\n".join(lines) + (
        f"\n\n**Winner:** `{winner['name']}` (composite score: {winner['composite_score']})"
        if winner else "\n\n**Winner:** N/A (no results)"
    )


def write_final_report(reports_dir: Path, output_path: Path, stage_b_data: list[dict] = None) -> None:
    """Generate the final markdown report from JSON results."""
    results = load_results(reports_dir)
    stage_b_data = stage_b_data or load_stage_b_results(reports_dir)
    if not results:
        output_path.write_text("# No results found\n")
        return

    table = format_comparison_table(results)
    ranked_for_winner = rank_configs([{
        "name": r["config"],
        "peak_tok_s": r.get("aggregate", {}).get("peak_tok_s", 0),
        "p95_latency_at_20": r.get("aggregate", {}).get("p95_latency_at_20", 0),
        "quality": r.get("quality", 0),
    } for r in results])
    winner = ranked_for_winner[0] if ranked_for_winner else None
    winner_name = winner["name"] if winner else "N/A"

    has_q4 = any('Q4' in r['config'] for r in results)
    has_q6 = any('Q6' in r['config'] for r in results)
    has_q8_text = any('Q8-textonly' in r['config'] for r in results)
    has_q8_standalone = any('Q8-standalone' in r['config'] for r in results)

    report = f"""# Gemma 4 12B Optimization — Final Report

**Generated:** {datetime.now().isoformat(timespec='seconds')}
**Hardware:** RTX 5060 Ti 16GB VRAM
**Methodology:** Multi-user Vietnamese chat (20-40 concurrent)

## Configurations tested

| Config | 12B variant | Strategy | VRAM | Status |
|--------|-------------|----------|------|--------|
| A: Q4 + E2B | Q4_K_M (7.4GB) | Split multimodal (12B text + E2B vision) | ~10.3 GB | {'✅' if has_q4 else '❌'} |
| B: Q6 + E2B | Q6_K (9.8GB) | Split multimodal (12B text + E2B vision) | ~13.3 GB | {'✅' if has_q6 else '❌'} |
| C: Q8 standalone (multimodal) | Q8_0 (12.7GB) | Standalone w/ mmproj | ~13.0 GB | ⚠️ Skipped (gemma4uv projector not yet supported by llama.cpp 8981) |
| D: Q8 text-only | Q8_0 (12.7GB) | Standalone text-only | ~13.0 GB | {'✅' if has_q8_text else '❌'} |

## Results (Stage A — basic benchmark)

{table}

## Stage B (max load)

{_format_stage_b(stage_b_data) if stage_b_data else "_Stage B not run yet._"}

## Recommendation

**Best config:** `{winner_name}`
- Composite score: {winner.get('composite_score', 0):.4f}
- Peak tok/s: {winner.get('peak_tok_s', 0)}
- p95 latency @20 users: {winner.get('p95_latency_at_20', 0)}ms
- Vietnamese quality: {winner.get('quality', 0)}/10

See `reports/bench_12b/` for full per-config details.
"""
    output_path.write_text(report)


def _format_stage_b(stage_b_data: list[dict]) -> str:
    if not stage_b_data:
        return ""
    lines = [
        "Sustained load test (10 minutes at concurrency 20, 120 prompts):",
        "",
        "| Config | Sustained tok/s | TTFT p50 (ms) | TTFT p95 (ms) | E2E p95 (ms) | Errors |",
        "|---|---|---|---|---|---|",
    ]
    for d in stage_b_data:
        cfg = d.get("config", "?")
        stage = d.get("stages", {}).get("concurrency_20", {})
        tok_s = stage.get("tok_s_aggregate", 0)
        ttft_p50 = stage.get("ttft_p50_ms", 0)
        ttft_p95 = stage.get("ttft_p95_ms", 0)
        e2e_p95 = stage.get("e2e_p95_ms", 0)
        errors = stage.get("errors", 0)
        lines.append(
            f"| {cfg} | {tok_s} | {ttft_p50} | {ttft_p95} | {e2e_p95} | {errors} |"
        )
    return "\n".join(lines)


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--reports-dir", default="reports/bench_12b", type=Path)
    p.add_argument("--output", default="reports/bench_12b/final_comparison.md", type=Path)
    p.add_argument("--stage-b", nargs="*", default=[], help="Stage B JSON files")
    args = p.parse_args()

    stage_b = []
    for path in args.stage_b:
        try:
            with open(path) as f:
                stage_b.append(json.load(f))
        except (OSError, json.JSONDecodeError):
            pass

    write_final_report(args.reports_dir, args.output, stage_b)
    print(f"Report written to {args.output}")
