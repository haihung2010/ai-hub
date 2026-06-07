import json
import pytest
import tempfile
from pathlib import Path
from scripts.gen_final_report import (
    load_results, format_comparison_table, write_final_report,
)

pytestmark = pytest.mark.no_isolated_db


def test_load_results_reads_all_json():
    """Load all .json files from reports dir."""
    with tempfile.TemporaryDirectory() as d:
        Path(d, "q4.json").write_text(json.dumps({
            "config": "Q4-combo", "aggregate": {"peak_tok_s": 500, "p95_latency_at_20": 1200},
            "quality": 8.2, "stages": {"a": {}, "b": {}}
        }))
        Path(d, "q6.json").write_text(json.dumps({
            "config": "Q6-combo", "aggregate": {"peak_tok_s": 470, "p95_latency_at_20": 1450},
            "quality": 8.6, "stages": {"a": {}, "b": {}}
        }))
        results = load_results(Path(d))
        assert len(results) == 2
        assert "Q4-combo" in [r["config"] for r in results]


def test_format_comparison_table_includes_all_metrics():
    """Comparison table has 12B tok/s, latency, quality, composite score."""
    results = [
        {"config": "Q4-combo", "aggregate": {"peak_tok_s": 500, "p95_latency_at_20": 1200}, "quality": 8.2, "composite_score": 0.85},
        {"config": "Q8-standalone", "aggregate": {"peak_tok_s": 430, "p95_latency_at_20": 1850}, "quality": 8.9, "composite_score": 0.70},
    ]
    table = format_comparison_table(results)
    assert "Q4-combo" in table
    assert "Q8-standalone" in table
    assert "tok/s" in table
    assert "Quality" in table
    assert "Composite" in table
    assert "Winner:" in table


def test_write_final_report_creates_file():
    """End-to-end: write report to file."""
    with tempfile.TemporaryDirectory() as d:
        in_dir = Path(d) / "in"
        in_dir.mkdir()
        (in_dir / "q4.json").write_text(json.dumps({
            "config": "Q4-combo", "aggregate": {"peak_tok_s": 500, "p95_latency_at_20": 1200},
            "quality": 8.2, "composite_score": 0.85, "stages": {"basic": {}}
        }))
        out = Path(d) / "report.md"
        write_final_report(in_dir, out)
        assert out.exists()
        content = out.read_text()
        assert "Q4-combo" in content
        assert "500" in content
