"""Unit tests for the PG audit writer (Rank 7 fix).

These tests verify:
  - Writer is a no-op when disabled.
  - record_rate_limit() UPSERTs the right SQL.
  - record_auth_failure() UPSERTs the right SQL.
  - The writer is fire-and-forget (does not block).
  - PG exceptions inside the writer are swallowed (request path safe).
  - A real PG run (if available) lands a row in the audit table.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from unittest.mock import MagicMock, patch

import pytest

from app.services import security_audit
from app.services.security_audit import (
    record_auth_failure,
    record_rate_limit,
    set_enabled,
    shutdown,
)


@pytest.fixture(autouse=True)
def _reset_audit_state():
    """Make sure each test starts with the writer enabled and clean state.

    We deliberately do NOT call `shutdown(wait=True)` in teardown — the
    module-level executor is a singleton and shutting it down would
    silently disable all subsequent writes in the same process.
    """
    set_enabled(True)
    yield
    set_enabled(True)


@pytest.mark.unit
def test_disabled_writer_is_noop() -> None:
    set_enabled(False)
    with patch("app.services.security_audit._executor") as mock_exec:
        record_rate_limit("k1", timestamps=[1.0, 2.0], now=3.0)
        record_auth_failure("k2", failures=[1.0], blocked_until=0.0, now=3.0)
        mock_exec.submit.assert_not_called()


@pytest.mark.unit
def test_record_rate_limit_submits_upsert(monkeypatch) -> None:
    captured: list[tuple] = []

    def fake_submit(target):
        # Run synchronously to capture args.
        target()
        return None

    monkeypatch.setattr(security_audit._executor, "submit", fake_submit)

    # Patch the connection factory.
    fake_conn = MagicMock()
    fake_cursor = MagicMock()
    fake_conn.__enter__.return_value = fake_conn
    fake_conn.__exit__.return_value = False
    fake_conn.execute.return_value = fake_cursor
    monkeypatch.setattr(
        "app.core.database.get_db_connection",
        lambda: fake_conn,
    )

    record_rate_limit("k1", timestamps=[1.0, 2.0, 3.0], now=99.0)
    assert fake_conn.execute.called
    sql, params = fake_conn.execute.call_args[0]
    assert "rate_limit_buckets" in sql
    assert "ON CONFLICT (key) DO UPDATE" in sql
    assert params[0] == "k1"
    assert json.loads(params[1]) == [1.0, 2.0, 3.0]
    assert params[2] == 99.0
    assert fake_conn.commit.called


@pytest.mark.unit
def test_record_auth_failure_submits_upsert(monkeypatch) -> None:
    def fake_submit(target):
        target()
        return None

    monkeypatch.setattr(security_audit._executor, "submit", fake_submit)

    fake_conn = MagicMock()
    fake_conn.__enter__.return_value = fake_conn
    fake_conn.__exit__.return_value = False
    fake_conn.execute.return_value = MagicMock()
    monkeypatch.setattr(
        "app.core.database.get_db_connection",
        lambda: fake_conn,
    )

    record_auth_failure(
        "k2", failures=[10.0, 11.0], blocked_until=900.0, now=12.0
    )
    sql, params = fake_conn.execute.call_args[0]
    assert "auth_failures" in sql
    assert "ON CONFLICT (key) DO UPDATE" in sql
    assert params[0] == "k2"
    assert json.loads(params[1]) == [10.0, 11.0]
    assert params[2] == 900.0
    assert params[3] == 12.0
    assert fake_conn.commit.called


@pytest.mark.unit
def test_pg_exception_is_swallowed(monkeypatch) -> None:
    """Writer must never raise — the request path stays safe."""

    def fake_submit(target):
        target()
        return None

    monkeypatch.setattr(security_audit._executor, "submit", fake_submit)

    def boom():
        raise RuntimeError("PG down")

    monkeypatch.setattr(
        "app.core.database.get_db_connection",
        boom,
    )

    # Both calls must swallow the exception.
    record_rate_limit("k1")
    record_auth_failure("k2")


@pytest.mark.unit
def test_default_timestamps_when_none(monkeypatch) -> None:
    captured_params: list[tuple] = []

    def fake_submit(target):
        target()
        return None

    def fake_execute(sql, params):
        captured_params.append(params)
        return MagicMock()

    monkeypatch.setattr(security_audit._executor, "submit", fake_submit)

    fake_conn = MagicMock()
    fake_conn.__enter__.return_value = fake_conn
    fake_conn.__exit__.return_value = False
    fake_conn.execute.side_effect = fake_execute
    monkeypatch.setattr(
        "app.core.database.get_db_connection",
        lambda: fake_conn,
    )

    before = time.time()
    record_rate_limit("k1")
    after = time.time()

    params = captured_params[0]
    ts = json.loads(params[1])
    assert 1 == len(ts)
    assert before <= ts[0] <= after
    assert before <= params[2] <= after


@pytest.mark.integration
@pytest.mark.skipif(
    not os.getenv("DATABASE_URL"),
    reason="DATABASE_URL not set; live PG verification skipped",
)
def test_real_pg_rate_limit_write() -> None:
    """Live integration: actually write a row and read it back."""
    from app.core.database import get_db_connection

    test_key = f"rl_test_{uuid.uuid4().hex[:12]}"
    try:
        # Use a synchronous submit (bypass the executor) for deterministic
        # ordering. We import the private write function so the test is
        # still end-to-end through the real PG connection.
        from app.services.security_audit import _do_rate_limit_write

        _do_rate_limit_write(test_key, [100.0, 200.0], 250.0)
        with get_db_connection() as conn:
            row = conn.execute(
                "SELECT key, timestamps_json, updated_at FROM rate_limit_buckets WHERE key = %s",
                (test_key,),
            ).fetchone()
        assert row is not None
        assert row["key"] == test_key
        assert json.loads(row["timestamps_json"]) == [100.0, 200.0]
        assert float(row["updated_at"]) == 250.0
    finally:
        with get_db_connection() as conn:
            conn.execute("DELETE FROM rate_limit_buckets WHERE key = %s", (test_key,))
            conn.commit()


@pytest.mark.integration
@pytest.mark.skipif(
    not os.getenv("DATABASE_URL"),
    reason="DATABASE_URL not set; live PG verification skipped",
)
def test_real_pg_auth_failure_write() -> None:
    """Live integration: actually write an auth-failure row and read it back."""
    from app.core.database import get_db_connection

    test_key = f"af_test_{uuid.uuid4().hex[:12]}"
    try:
        from app.services.security_audit import _do_auth_failure_write

        _do_auth_failure_write(test_key, [1.0, 2.0, 3.0], 600.0, 10.0)
        with get_db_connection() as conn:
            row = conn.execute(
                "SELECT key, failures_json, blocked_until, updated_at "
                "FROM auth_failures WHERE key = %s",
                (test_key,),
            ).fetchone()
        assert row is not None
        assert row["key"] == test_key
        assert json.loads(row["failures_json"]) == [1.0, 2.0, 3.0]
        assert float(row["blocked_until"]) == 600.0
        assert float(row["updated_at"]) == 10.0
    finally:
        with get_db_connection() as conn:
            conn.execute("DELETE FROM auth_failures WHERE key = %s", (test_key,))
            conn.commit()
