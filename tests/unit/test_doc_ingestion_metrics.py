"""Test the document ingestion POC eval runner CLI interface."""
import subprocess
import sys
from pathlib import Path

import pytest

# These tests don't touch the database — opt out of the autouse
# isolated_db fixture so they run without a live Postgres connection.
pytestmark = [pytest.mark.no_isolated_db]

POC_DIR = Path("pocs/doc_ingestion")


def test_eval_runner_help():
    """CLI --help must succeed and mention candidate + fixture."""
    result = subprocess.run(
        [sys.executable, str(POC_DIR / "eval_runner.py"), "--help"],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, f"--help failed: {result.stderr}"
    assert "candidate" in result.stdout.lower()
    assert "fixture" in result.stdout.lower()


def test_eval_runner_candidates_dict():
    """Module must export CANDIDATES with 3 keys."""
    sys.path.insert(0, str(POC_DIR))
    import eval_runner
    assert set(eval_runner.CANDIDATES.keys()) == {"docling", "marker", "unstructured"}


def test_requirements_poc_exists():
    """requirements_poc.txt must exist with all 3 candidates."""
    req_path = POC_DIR / "requirements_poc.txt"
    assert req_path.exists(), f"Missing {req_path}"
    content = req_path.read_text()
    for pkg in ["docling", "marker", "unstructured"]:
        assert pkg in content, f"Missing {pkg} in requirements_poc.txt"