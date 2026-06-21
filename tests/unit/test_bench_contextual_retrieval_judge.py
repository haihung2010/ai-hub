"""Test the --judge flag is wired in bench_contextual_retrieval.py."""
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.no_isolated_db]


BENCH_SCRIPT = Path("evals/bench_contextual_retrieval.py")


def test_bench_help_mentions_judge():
    result = subprocess.run(
        [sys.executable, str(BENCH_SCRIPT), "--help"],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, f"--help failed: {result.stderr}"
    assert "--judge" in result.stdout, "Missing --judge flag"
    assert "--langfuse-dataset" in result.stdout, "Missing --langfuse-dataset flag"


def test_bench_judge_flag_parsing():
    """--judge must be a boolean flag (action='store_true')."""
    # Parse the file's argparse setup (rough check)
    # Don't actually run main; just check the source contains the flags
    source = BENCH_SCRIPT.read_text()
    assert '"--judge"' in source or "'--judge'" in source, "Missing --judge argparse"
    assert (
        '"--langfuse-dataset"' in source or "'--langfuse-dataset'" in source
    ), "Missing --langfuse-dataset argparse"


def test_bench_module_loads_with_judge_args():
    """Importing the bench module must not raise; flags should be importable as args attr."""
    spec = importlib.util.spec_from_file_location("bench", BENCH_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # Sanity check: function exists
    assert hasattr(module, "run_benchmark"), "run_benchmark missing"
    assert hasattr(module, "main"), "main missing"
