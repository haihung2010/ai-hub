"""Unit tests for the 5060 Ti 16GB multi-user load test (2026-06-12)."""
from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.no_isolated_db]


SCRIPT = Path(__file__).resolve().parent.parent.parent / "scripts" / "load_test_5060ti_16gb.py"


def test_load_test_script_exists_and_executable() -> None:
    assert SCRIPT.exists()
    import stat
    assert SCRIPT.stat().st_mode & stat.S_IXUSR


def test_load_test_script_parses() -> None:
    import ast
    ast.parse(SCRIPT.read_text())


def test_load_test_help_works() -> None:
    import subprocess
    r = subprocess.run(
        ["/home/hung/ai-hub/venv/bin/python3", str(SCRIPT), "--help"],
        capture_output=True, text=True, timeout=5,
    )
    assert r.returncode == 0
    assert "--users" in r.stdout
    assert "--duration" in r.stdout


def test_load_test_smoke_against_unreachable_host() -> None:
    """With an unreachable --base, the warmup should fail and
    the script should return a clean error JSON instead of crashing."""
    import subprocess
    r = subprocess.run(
        ["/home/hung/ai-hub/venv/bin/python3", str(SCRIPT),
         "--base", "http://127.0.0.1:1", "--users", "1", "--duration", "1",
         "--json"],
        capture_output=True, text=True, timeout=10,
    )
    # Returns 1 on warmup failure, 0 otherwise — either way the
    # JSON body should have a clean "error" key.
    assert r.returncode in (0, 1)
    import json
    report = json.loads(r.stdout)
    assert "error" in report
    assert "warmup" in report["error"].lower()


def test_verdict_classifier() -> None:
    """The _verdict() helper makes sane calls based on status mix."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("lt", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    # All-200 + low p95 → ✅
    assert "✅" in mod._verdict({200: 10}, 10, 100)
    # 5xx → ❌
    assert "❌" in mod._verdict({200: 5, 500: 1}, 6, 100)
    # 413 → ⚠️ ctx overflow
    assert "⚠️" in mod._verdict({200: 5, 413: 2}, 7, 100)
    # >50% 429 → ⚠️ rate-limited
    assert "⚠️" in mod._verdict({200: 1, 429: 9}, 10, 100)
    # p95 > 5s → ⚠️ slow
    assert "⚠️" in mod._verdict({200: 10}, 10, 6000)
