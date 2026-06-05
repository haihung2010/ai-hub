import pytest
from scripts.bench_metrics import (
    compute_phase_metrics, aggregate_phases, rank_configs, compute_composite_score,
)

pytestmark = pytest.mark.no_isolated_db


def test_compute_phase_metrics_single_request():
    """Compute metrics from a list of request timings."""
    timings = [
        {"ttft_ms": 100, "e2e_ms": 500, "prompt_tokens": 20, "completion_tokens": 100, "status": "ok"},
        {"ttft_ms": 200, "e2e_ms": 600, "prompt_tokens": 20, "completion_tokens": 100, "status": "ok"},
    ]
    m = compute_phase_metrics(timings, wall_time_s=2.0)
    assert m["requests"] == 2
    assert m["ttft_p50_ms"] == 150
    assert m["ttft_p95_ms"] >= 200
    assert m["tok_s_aggregate"] == 100.0
    assert m["rps"] == 1.0
    assert m["errors"] == 0


def test_compute_phase_metrics_with_errors():
    """Errors counted separately, don't pollute percentiles."""
    timings = [
        {"ttft_ms": 100, "e2e_ms": 500, "prompt_tokens": 20, "completion_tokens": 100, "status": "ok"},
        {"ttft_ms": 0, "e2e_ms": 0, "prompt_tokens": 0, "completion_tokens": 0, "status": "TIMEOUT"},
    ]
    m = compute_phase_metrics(timings, wall_time_s=1.0)
    assert m["requests"] == 1
    assert m["errors"] == 1
    assert m["tok_s_aggregate"] == 100.0


def test_aggregate_phases_computes_weighted_score():
    """Aggregate 3 phases into a single config score."""
    phases = {
        "latency": {"tok_s_aggregate": 50, "ttft_p95_ms": 200},
        "concurrency_10": {"tok_s_aggregate": 200, "ttft_p95_ms": 500},
        "concurrency_20": {"tok_s_aggregate": 300, "ttft_p95_ms": 1200},
    }
    agg = aggregate_phases(phases)
    assert agg["peak_tok_s"] == 300
    assert agg["p95_latency_at_20"] == 1200


def test_rank_configs_by_composite_score():
    """Higher composite score ranks first."""
    configs = [
        {"name": "A", "peak_tok_s": 500, "p95_latency_at_20": 1200, "quality": 8.2},
        {"name": "B", "peak_tok_s": 470, "p95_latency_at_20": 1450, "quality": 8.6},
        {"name": "C", "peak_tok_s": 430, "p95_latency_at_20": 1850, "quality": 8.9},
    ]
    ranked = rank_configs(configs)
    assert len(ranked) == 3
    assert all("composite_score" in c for c in ranked)
    assert ranked[0]["composite_score"] >= ranked[1]["composite_score"] >= ranked[2]["composite_score"]
