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
    """Load all *.json files from reports dir as list of dicts."""
    results = []
    for path in sorted(reports_dir.glob("*.json")):
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
    if not results:
        output_path.write_text("# No results found\n")
        return

    table = format_comparison_table(results)
    winner = max(results, key=lambda r: r.get("composite_score", 0))

    report = f"""# Gemma 4 12B Optimization — Final Report

**Generated:** {datetime.now().isoformat(timespec='seconds')}
**Hardware:** RTX 5060 Ti 16GB VRAM
**Methodology:** Multi-user Vietnamese chat (20-40 concurrent)

## Configurations tested

| Config | 12B variant | Strategy | VRAM | Status |
|--------|-------------|----------|------|--------|
| A: Q4 + E2B | Q4_K_M (7.4GB) | Split multimodal | ~10.3 GB | {'✅' if any('Q4' in r['config'] for r in results) else '❌'} |
| B: Q6 + E2B | Q6_K (9.8GB) | Split multimodal | ~13.3 GB | {'✅' if any('Q6' in r['config'] for r in results) else '❌'} |
| C: Q8 standalone | Q8_0 (12.7GB) | Standalone | ~13.0 GB | {'✅' if any('Q8' in r['config'] for r in results) else '❌'} |

## Results (Stage A — basic benchmark)

{table}

## Stage B (max load)

{_format_stage_b(stage_b_data) if stage_b_data else "_Stage B not run yet._"}

## Recomendación

**Best config:** `{winner.get('config', 'N/A')}`
- Aggregate score: {winner.get('composite_score', 0):.2f}
- Peak tok/s: {winner.get('aggregate', {}).get('peak_tok_s', 0)}
- p95 latency @20 users: {winner.get('aggregate', {}).get('p95_latency_at_20', 0)}ms
- Vietnamese quality: {winner.get('quality', 0)}/10

See `reports/bench_12b/` for full per-config details.
"""
    output_path.write_text(report)


def _format_stage_b(stage_b_data: list[dict]) -> str:
    if not stage_b_data:
        return ""
    lines = ["| Config | Sustained tok/s | Spike tok/s | p95 @60 users |", "|---|---|---|---|"]
    for d in stage_b_data:
        lines.append(
            f"| {d.get('config', '?')} | {d.get('sustained_tok_s', 0)} | "
            f"{d.get('spike_tok_s', 0)} | {d.get('p95_at_60', 0)}ms |"
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
