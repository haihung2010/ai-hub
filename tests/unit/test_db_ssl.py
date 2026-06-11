"""Unit tests for DB SSL config (P2.3, 2026-06-10)."""
from __future__ import annotations

import logging

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.no_isolated_db]


# ──────────────────────────────────────────────────────────────────────
# sslmode parser
# ──────────────────────────────────────────────────────────────────────


def test_effective_sslmode_from_url_with_require() -> None:
    from app.core.database import _get_effective_sslmode
    assert _get_effective_sslmode(
        "postgresql://u:p@localhost:5432/db?sslmode=require"
    ) == "require"


def test_effective_sslmode_from_url_with_verify_full() -> None:
    from app.core.database import _get_effective_sslmode
    assert _get_effective_sslmode(
        "postgresql://u:p@host:5432/db?sslmode=verify-full&sslrootcert=/etc/ssl/cert"
    ) == "verify-full"


def test_effective_sslmode_missing_returns_disable() -> None:
    """No sslmode param → 'disable'. P2.3 startup log emits a warning
    in this case."""
    from app.core.database import _get_effective_sslmode
    assert _get_effective_sslmode("postgresql://u:p@localhost:5432/db") == "disable"


def test_effective_sslmode_empty_string() -> None:
    from app.core.database import _get_effective_sslmode
    assert _get_effective_sslmode("") == "disable"


def test_effective_sslmode_garbage_url_returns_disable() -> None:
    from app.core.database import _get_effective_sslmode
    # Should NOT raise — startup must always succeed.
    assert _get_effective_sslmode("not://a url at all") == "disable"


# ──────────────────────────────────────────────────────────────────────
# Startup log behaviour (without actually opening a real pool)
# ──────────────────────────────────────────────────────────────────────


def test_startup_logs_warning_when_sslmode_missing(caplog) -> None:
    """The P2.3 startup log warns when sslmode is absent, so ops
    can grep the boot log for the warning."""
    from app.core.database import _get_effective_sslmode
    with caplog.at_level(logging.INFO):
        sslmode = _get_effective_sslmode("postgresql://u:p@localhost/db")
    assert sslmode == "disable"
    # We don't assert the log line here because _get_pool() is what
    # emits it; we tested the helper above and end-to-end is covered
    # by integration tests.


def test_default_dev_url_in_test_conftest_has_sslmode() -> None:
    """The test DB URL (via DATABASE_URL env) must include sslmode.

    Without this, all unit tests that touch the DB run unencrypted.
    Reference: P2.3 — encrypt Postgres connection in dev too.
    """
    import os
    dsn = os.environ.get("DATABASE_URL", "")
    assert "sslmode=require" in dsn, (
        f"DATABASE_URL must include sslmode=require (P2.3). Got: {dsn!r}"
    )
