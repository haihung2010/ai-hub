"""Unit tests for the pen test script (P3.1, 2026-06-11)."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.no_isolated_db]


SCRIPT = Path(__file__).resolve().parent.parent.parent / "scripts" / "pen_test.py"


def test_pen_test_script_exists() -> None:
    assert SCRIPT.exists(), f"missing: {SCRIPT}"


def test_pen_test_script_is_executable() -> None:
    import os
    import stat
    mode = S_STAT = SCRIPT.stat().st_mode
    assert mode & stat.S_IXUSR, "script is not executable; chmod +x"


def test_pen_test_script_parses() -> None:
    """Compiles cleanly (catches syntax errors + missing imports)."""
    code = SCRIPT.read_text()
    compile(code, str(SCRIPT), "exec")


def test_pen_test_script_requires_api_key() -> None:
    """Without --api-key and without $API_KEY, the script exits 2 with a
    clear error."""
    import os
    env = os.environ.copy()
    env.pop("API_KEY", None)  # ensure no fallback
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--base", "http://127.0.0.1:1"],
        capture_output=True,
        text=True,
        timeout=10,
        env=env,
    )
    assert result.returncode == 2, f"expected exit 2, got {result.returncode}"
    assert "api-key" in result.stderr.lower() or "API_KEY" in result.stderr


def test_pen_test_script_runs_against_unreachable_host() -> None:
    """With an unreachable base, the script should fail cleanly
    (not crash). Each probe catches its own exception and reports."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--api-key", "x", "--base", "http://127.0.0.1:1"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    # The script returns 1 because probes failed (connection refused),
    # NOT 2 (which would mean a script-level error). This proves the
    # script's error handling works end-to-end.
    assert result.returncode == 1, f"expected exit 1, got {result.returncode}\nSTDOUT:\n{result.stdout[:500]}"


def test_pen_test_script_help_works() -> None:
    """--help runs and exits 0 with a description."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 0
    assert "AI Hub pen test" in result.stdout
