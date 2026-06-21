"""Test the Vietnamese contextual retrieval dataset schema + Langfuse sync script."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.no_isolated_db]

DATASET_PATH = Path("evals/datasets/contextual_retrieval_vi.jsonl")
SYNC_SCRIPT = Path("evals/langfuse_dataset_sync.py")


def test_dataset_file_exists():
    assert DATASET_PATH.exists(), f"Missing dataset: {DATASET_PATH}"


def test_dataset_has_at_least_50_items():
    count = 0
    with DATASET_PATH.open() as f:
        for line in f:
            line = line.strip()
            if line:
                count += 1
    assert count >= 50, f"Dataset too small: {count} items (want >=50)"


def test_dataset_items_have_required_fields():
    required = {"query", "expected_card_id", "domain"}
    with DATASET_PATH.open() as f:
        for i, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            missing = required - set(item.keys())
            assert not missing, f"Line {i}: missing fields {missing}"
            assert isinstance(item["query"], str) and len(item["query"]) > 5, f"Line {i}: query too short"
            assert isinstance(item["expected_card_id"], str), f"Line {i}: expected_card_id not str"


def test_dataset_covers_all_five_domains():
    domains_seen: set[str] = set()
    with DATASET_PATH.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            domains_seen.add(item.get("domain", ""))
    expected = {"vehix", "fanpage", "ihi", "ecommerce", "default"}
    assert expected <= domains_seen, f"Missing domains: {expected - domains_seen}"


def test_dataset_has_queries_per_domain():
    """Each domain must have at least 8 queries (close to 10 target)."""
    counts: dict[str, int] = {}
    with DATASET_PATH.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            domain = item.get("domain", "unknown")
            counts[domain] = counts.get(domain, 0) + 1
    for domain in ("vehix", "fanpage", "ihi", "ecommerce", "default"):
        assert counts.get(domain, 0) >= 8, f"Domain {domain} has only {counts.get(domain, 0)} queries (want >=8)"


def test_sync_script_help():
    """--help must succeed and mention dataset + file."""
    result = subprocess.run(
        [sys.executable, str(SYNC_SCRIPT), "--help"],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, f"--help failed: {result.stderr}"
    assert "dataset" in result.stdout.lower()
    assert "file" in result.stdout.lower()