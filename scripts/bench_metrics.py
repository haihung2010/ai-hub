"""Pure functions for computing benchmark metrics.

No I/O, no subprocess, no network. Importable for unit testing.
"""
from __future__ import annotations

import statistics
from typing import Any


def _percentile(values: list[float], p: float) -> float:
    """Return the p-th percentile (0-100) of values. Returns 0 if empty.

    Uses statistics.median for p=50 (handles 2-value median as average),
    nearest-rank indexing for other percentiles.
    """
    if not values:
        return 0
    if len(values) == 1:
        return values[0]
    if p == 50:
        # statistics.median of 2 values returns the mean; indexing would return the lower
        return float(statistics.median(values))
    sorted_vals = sorted(values)
    idx = min(int(round(p / 100 * (len(sorted_vals) - 1))), len(sorted_vals) - 1)
    return sorted_vals[idx]


def compute_phase_metrics(timings: list[dict], wall_time_s: float) -> dict:
    """Compute aggregate metrics from a list of request timings.

    Each timing: {ttft_ms, e2e_ms, prompt_tokens, completion_tokens, status}
    status="ok" counts toward percentiles; "TIMEOUT"/"ERROR" counted as errors only.
    """
    successful = [t for t in timings if t.get("status") == "ok"]
    ttft = [t["ttft_ms"] for t in successful]
    e2e = [t["e2e_ms"] for t in successful]
    completion_tokens = sum(t["completion_tokens"] for t in successful)

    return {
        "requests": len(successful),
        "errors": len(timings) - len(successful),
        "ttft_p50_ms": int(_percentile(ttft, 50)),
        "ttft_p95_ms": int(_percentile(ttft, 95)),
        "e2e_p50_ms": int(_percentile(e2e, 50)),
        "e2e_p95_ms": int(_percentile(e2e, 95)),
        "tok_s_aggregate": round(completion_tokens / max(wall_time_s, 0.001), 1),
        "rps": round(len(successful) / max(wall_time_s, 0.001), 2),
    }


def aggregate_phases(phases: dict[str, dict]) -> dict:
    """Aggregate multiple phase metrics into config-level summary."""
    peak_tok_s = max((p.get("tok_s_aggregate", 0) for p in phases.values()), default=0)
    latency_phases = {k: v for k, v in phases.items() if "concurrency" in k}
    p95_latency_at_20 = latency_phases.get("concurrency_20", {}).get("ttft_p95_ms", 0)
    return {
        "peak_tok_s": peak_tok_s,
        "p95_latency_at_20": p95_latency_at_20,
    }


def compute_composite_score(peak_tok_s: float, p95_latency_ms: float, quality: float,
                            max_tok_s: float, max_latency_ms: float, max_quality: float) -> float:
    """Compute composite score [0, 1] using balanced weighting.

    Normalizes each metric to [0, 1] using max-value scaling, then weights:
    - 0.40 x normalized_tok_s (higher is better)
    - 0.30 x normalized_inv_latency (1 - normalized, higher is better)
    - 0.30 x normalized_quality (higher is better)
    """
    norm_tok = peak_tok_s / max(max_tok_s, 1)
    norm_lat = 1 - (p95_latency_ms / max(max_latency_ms, 1))
    norm_qual = quality / max(max_quality, 1)
    return round(0.40 * norm_tok + 0.30 * max(norm_lat, 0) + 0.30 * norm_qual, 4)


def rank_configs(configs: list[dict]) -> list[dict]:
    """Rank configs by composite score. Returns list sorted desc by composite_score.

    Each config: {name, peak_tok_s, p95_latency_at_20, quality}.
    Adds 'composite_score' field to each.
    """
    if not configs:
        return []
    max_tok = max(c.get("peak_tok_s", 0) for c in configs)
    max_lat = max(c.get("p95_latency_at_20", 0) for c in configs) or 1
    max_qual = max(c.get("quality", 0) for c in configs) or 1
    for c in configs:
        c["composite_score"] = compute_composite_score(
            c.get("peak_tok_s", 0), c.get("p95_latency_at_20", 0), c.get("quality", 0),
            max_tok, max_lat, max_qual,
        )
    return sorted(configs, key=lambda c: c["composite_score"], reverse=True)
