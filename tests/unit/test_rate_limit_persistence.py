"""Tests for SQLite-backed rate limiter and failure tracker."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

import app.core.database as _db_module
from app.core.database import init_db
from app.middleware.security import SecurityMiddleware, SqliteFailureTracker, SqliteRateLimiter


@pytest.fixture
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Fresh isolated SQLite for each test — overrides the autouse fixture's db."""
    monkeypatch.setattr(_db_module, "DB_PATH", tmp_path / "rl_test.db")
    init_db()


class TestSqliteRateLimiter:
    def test_rate_limit_key_does_not_include_raw_api_key(self):
        raw_key = "sk-secret-should-not-be-persisted"
        rate_key = SecurityMiddleware._rate_limit_key("127.0.0.1", raw_key, None)

        assert raw_key not in rate_key
        assert rate_key.startswith("127.0.0.1:")

    def test_allows_up_to_limit(self, db):
        limiter = SqliteRateLimiter(limit=3, window_seconds=60)
        now = time.time()
        assert limiter.allow("k", now=now) is True
        assert limiter.allow("k", now=now + 1) is True
        assert limiter.allow("k", now=now + 2) is True
        assert limiter.allow("k", now=now + 3) is False

    def test_resets_after_window(self, db):
        limiter = SqliteRateLimiter(limit=1, window_seconds=60)
        now = time.time()
        assert limiter.allow("k", now=now) is True
        assert limiter.allow("k", now=now + 30) is False
        assert limiter.allow("k", now=now + 61) is True

    def test_state_survives_restart(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        db_path = tmp_path / "persist_rl.db"
        monkeypatch.setattr(_db_module, "DB_PATH", db_path)
        init_db()

        now = time.time()
        limiter1 = SqliteRateLimiter(limit=2, window_seconds=60)
        limiter1.allow("k", now=now)
        limiter1.allow("k", now=now + 1)

        # Simulate restart: fresh instance, same DB file
        limiter2 = SqliteRateLimiter(limit=2, window_seconds=60)
        assert limiter2.allow("k", now=now + 2) is False

    def test_separate_keys_are_independent(self, db):
        limiter = SqliteRateLimiter(limit=1, window_seconds=60)
        now = time.time()
        assert limiter.allow("a", now=now) is True
        assert limiter.allow("b", now=now) is True
        assert limiter.allow("a", now=now + 1) is False
        assert limiter.allow("b", now=now + 1) is False


class TestSqliteFailureTracker:
    def test_not_blocked_initially(self, db):
        tracker = SqliteFailureTracker(limit=3, block_seconds=300)
        assert tracker.is_blocked("1.2.3.4") is False

    def test_blocks_after_limit(self, db):
        tracker = SqliteFailureTracker(limit=3, block_seconds=300, window_seconds=60)
        now = time.time()
        tracker.record_failure("1.2.3.4", now=now)
        tracker.record_failure("1.2.3.4", now=now + 1)
        assert tracker.is_blocked("1.2.3.4", now=now + 2) is False
        tracker.record_failure("1.2.3.4", now=now + 2)
        assert tracker.is_blocked("1.2.3.4", now=now + 3) is True

    def test_block_expires(self, db):
        tracker = SqliteFailureTracker(limit=2, block_seconds=60, window_seconds=300)
        now = time.time()
        tracker.record_failure("1.2.3.4", now=now)
        tracker.record_failure("1.2.3.4", now=now + 1)
        assert tracker.is_blocked("1.2.3.4", now=now + 2) is True
        assert tracker.is_blocked("1.2.3.4", now=now + 61) is False

    def test_reset_clears_block(self, db):
        tracker = SqliteFailureTracker(limit=2, block_seconds=300, window_seconds=300)
        now = time.time()
        tracker.record_failure("1.2.3.4", now=now)
        tracker.record_failure("1.2.3.4", now=now + 1)
        assert tracker.is_blocked("1.2.3.4", now=now + 2) is True
        tracker.reset("1.2.3.4")
        assert tracker.is_blocked("1.2.3.4", now=now + 2) is False

    def test_block_survives_restart(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        db_path = tmp_path / "persist_ft.db"
        monkeypatch.setattr(_db_module, "DB_PATH", db_path)
        init_db()

        now = time.time()
        tracker1 = SqliteFailureTracker(limit=2, block_seconds=300, window_seconds=300)
        tracker1.record_failure("1.2.3.4", now=now)
        tracker1.record_failure("1.2.3.4", now=now + 1)
        assert tracker1.is_blocked("1.2.3.4", now=now + 2) is True

        # Simulate restart: fresh instance, same DB file
        tracker2 = SqliteFailureTracker(limit=2, block_seconds=300, window_seconds=300)
        assert tracker2.is_blocked("1.2.3.4", now=now + 2) is True

    def test_old_failures_slide_out_of_window(self, db):
        tracker = SqliteFailureTracker(limit=2, block_seconds=300, window_seconds=60)
        now = time.time()
        tracker.record_failure("1.2.3.4", now=now)
        tracker.record_failure("1.2.3.4", now=now + 1)
        assert tracker.is_blocked("1.2.3.4", now=now + 2) is True

        # After block expires AND fresh failures are outside the window,
        # one new failure should not re-block.
        tracker.record_failure("1.2.3.4", now=now + 400)
        assert tracker.is_blocked("1.2.3.4", now=now + 401) is False
