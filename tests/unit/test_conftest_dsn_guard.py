"""Regression tests for the DSN guard in tests/conftest.py:isolated_db.

Prevents accidental TRUNCATE of the production ai_hub database. See
session checkpoint 2026-06-06 → 2026-06-07 (pytest run at 2026-06-07
00:14:29 wiped 14 production tables because both
``AI_HUB_ALLOW_DB_TRUNCATE_FOR_TESTS=1`` and the production
``DATABASE_URL`` were set).
"""

from __future__ import annotations

import pytest

# These tests exercise the DSN guard itself — they must NOT trigger
# the autouse isolated_db fixture (which is exactly what we're testing).
pytestmark = pytest.mark.no_isolated_db


# ── Pure-function tests for the pattern matcher ──────────────────────


def test_prod_dsn_detected_lochost():
    from tests import conftest

    assert conftest._is_prod_dsn(
        "postgresql://aihub:aihub_pass@localhost:5432/ai_hub"
    ) is True


def test_prod_dsn_detected_127():
    from tests import conftest

    assert conftest._is_prod_dsn(
        "postgresql://aihub:aihub_pass@127.0.0.1:5432/ai_hub"
    ) is True


def test_clearly_different_dsn_not_detected():
    """DSNs that differ in DB name (the last path component) must NOT
    be treated as the production database. The guard is exact-match
    on the DB name — different user/host/port is still prod (e.g.
    aihub_test or aihub_staging)."""
    from tests import conftest

    # Empty
    assert conftest._is_prod_dsn("") is False
    # Test DB with similar prefix — the fix that matters most
    assert conftest._is_prod_dsn(
        "postgresql://aihub:aihub_pass@localhost:5432/ai_hub_test"
    ) is False
    assert conftest._is_prod_dsn(
        "postgresql://aihub:aihub_pass@127.0.0.1:5432/ai_hub_test"
    ) is False
    # Staging DB with similar prefix
    assert conftest._is_prod_dsn(
        "postgresql://aihub:aihub_pass@localhost:5432/ai_hub_staging"
    ) is False
    # No path component at all
    assert conftest._is_prod_dsn("postgresql://aihub:aihub_pass@localhost:5432") is False


def test_prod_dsn_detected_regardless_of_user_or_host():
    """The guard matches by DB name, not user/host. Any DSN ending in
    /ai_hub is considered prod, even with different credentials."""
    from tests import conftest

    # Different user
    assert conftest._is_prod_dsn(
        "postgresql://aihub:test@localhost:5432/ai_hub"
    ) is True
    # Different host
    assert conftest._is_prod_dsn(
        "postgresql://aihub:aihub_pass@prod-server:5432/ai_hub"
    ) is True
    # Different port
    assert conftest._is_prod_dsn(
        "postgresql://aihub:aihub_pass@localhost:5433/ai_hub"
    ) is True


# ── Pure-function tests for _should_refuse_truncate ──────────────────


def test_refuse_when_no_env_var():
    from tests import conftest

    env = {}  # no AI_HUB_ALLOW_DB_TRUNCATE_FOR_TESTS, no DATABASE_URL
    err = conftest._should_refuse_truncate(env)
    assert err is not None
    assert "AI_HUB_ALLOW_DB_TRUNCATE_FOR_TESTS" in err


def test_refuse_when_prod_dsn_set():
    from tests import conftest

    env = {
        "AI_HUB_ALLOW_DB_TRUNCATE_FOR_TESTS": "1",
        "DATABASE_URL": "postgresql://aihub:aihub_pass@localhost:5432/ai_hub",
    }
    err = conftest._should_refuse_truncate(env)
    assert err is not None
    assert "Refusing to TRUNCATE production" in err


def test_refuse_when_127_prod_dsn_set():
    from tests import conftest

    env = {
        "AI_HUB_ALLOW_DB_TRUNCATE_FOR_TESTS": "1",
        "DATABASE_URL": "postgresql://aihub:aihub_pass@127.0.0.1:5432/ai_hub",
    }
    err = conftest._should_refuse_truncate(env)
    assert err is not None
    assert "Refusing to TRUNCATE production" in err


def test_allow_when_test_dsn():
    from tests import conftest

    # The actual test DB DSN — different DB name suffix, must be allowed.
    env = {
        "AI_HUB_ALLOW_DB_TRUNCATE_FOR_TESTS": "1",
        "DATABASE_URL": "postgresql://aihub:aihub_pass@localhost:5432/ai_hub_test",
    }
    err = conftest._should_refuse_truncate(env)
    assert err is None


def test_refuse_when_dsn_ends_with_ai_hub():
    from tests import conftest

    # Different host but same DB name (ai_hub) — still considered prod.
    env = {
        "AI_HUB_ALLOW_DB_TRUNCATE_FOR_TESTS": "1",
        "DATABASE_URL": "postgresql://aihub:aihub_pass@prod-server:5432/ai_hub",
    }
    err = conftest._should_refuse_truncate(env)
    assert err is not None
    assert "Refusing to TRUNCATE production" in err


def test_allow_when_env_var_set_and_no_dsn():
    from tests import conftest

    env = {"AI_HUB_ALLOW_DB_TRUNCATE_FOR_TESTS": "1"}
    err = conftest._should_refuse_truncate(env)
    # With the env var set and no DSN, we have nothing to guard against
    assert err is None


def test_allow_when_env_var_set_and_empty_dsn():
    from tests import conftest

    env = {
        "AI_HUB_ALLOW_DB_TRUNCATE_FOR_TESTS": "1",
        "DATABASE_URL": "",
    }
    err = conftest._should_refuse_truncate(env)
    assert err is None


# ── End-to-end: import-level smoke test ─────────────────────────────


def test_guard_module_imports_cleanly():
    """Sanity: importing the module does not run any DB I/O. The DSN
    guard is pure-Python and side-effect-free at import time."""
    import importlib
    import sys

    if "tests.conftest" in sys.modules:
        importlib.reload(sys.modules["tests.conftest"])
    from tests import conftest

    assert callable(conftest._is_prod_dsn)
    assert callable(conftest._should_refuse_truncate)
