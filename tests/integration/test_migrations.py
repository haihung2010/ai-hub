"""Integration tests for idempotent database migration scripts.

These tests invoke the migration scripts as subprocesses against a
real PostgreSQL instance to confirm they:
- succeed on first run
- succeed on subsequent runs (idempotent)
- emit the expected success markers in stdout

Note: this test deliberately does not use the autouse ``isolated_db``
fixture in tests/conftest.py because the migration script is a black-box
subprocess — we want to validate behaviour on a real, existing schema,
not a freshly-truncated one.
"""

import subprocess
import sys
from pathlib import Path

import pytest


# Skip entire module if DATABASE_URL is not reachable — keeps CI fast for
# branches where Postgres is unavailable.
DATABASE_URL = "postgresql://aihub:aihub_pass@localhost:5432/ai_hub"

PROJECT_ROOT = Path(__file__).parent.parent.parent
SCRIPTS_DIR = PROJECT_ROOT / "scripts"


def _pg_reachable() -> bool:
    try:
        import psycopg

        with psycopg.connect(DATABASE_URL, connect_timeout=3) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                return cur.fetchone() == (1,)
    except Exception:
        return False


pytestmark = [
    pytest.mark.no_isolated_db,
    pytest.mark.skipif(
        not _pg_reachable(),
        reason=f"PostgreSQL not reachable at {DATABASE_URL}",
    ),
]

# This test runs as a subprocess and does not depend on the autouse
# ``isolated_db`` fixture in tests/conftest.py. Run with
# ``AI_HUB_ALLOW_DB_TRUNCATE_FOR_TESTS=1`` to satisfy the guard.


def _run_script(script_name: str) -> subprocess.CompletedProcess:
    script = SCRIPTS_DIR / script_name
    assert script.exists(), f"Script not found: {script}"
    env = {"DATABASE_URL": DATABASE_URL, "PATH": "/usr/bin:/bin"}
    return subprocess.run(
        [sys.executable, str(script)],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_pgvector_migration_idempotent():
    """Migration script should be idempotent — run twice without error."""
    r1 = _run_script("migrate_add_pgvector.py")
    r2 = _run_script("migrate_add_pgvector.py")

    assert r1.returncode == 0, f"First run failed: {r1.stderr}"
    assert r2.returncode == 0, f"Second run failed: {r2.stderr}"
    assert "pgvector extension enabled" in r1.stdout
    assert "pgvector extension enabled" in r2.stdout
