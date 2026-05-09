"""Tests for DB-backed rate limiter and failure tracker (PostgreSQL)."""

from __future__ import annotations

import time

import pytest

from app.middleware.security import SecurityMiddleware, SqliteFailureTracker, SqliteRateLimiter


class TestSqliteRateLimiter:
    def test_rate_limit_key_does_not_include_raw_api_key(self):
        raw_key = "sk-secret-should-not-be-persisted"
        rate_key = SecurityMiddleware._rate_limit_key("127.0.0.1", raw_key, None)

        assert raw_key not in rate_key
        assert rate_key.startswith("127.0.0.1:")

    def test_allows_up_to_limit(self):
        limiter = SqliteRateLimiter(limit=3, window_seconds=60)
        now = time.time()
        assert limiter.allow("k", now=now) is True
        assert limiter.allow("k", now=now + 1) is True
        assert limiter.allow("k", now=now + 2) is True
        assert limiter.allow("k", now=now + 3) is False

    def test_resets_after_window(self):
        limiter = SqliteRateLimiter(limit=1, window_seconds=60)
        now = time.time()
        assert limiter.allow("k2", now=now) is True
        assert limiter.allow("k2", now=now + 30) is False
        assert limiter.allow("k2", now=now + 61) is True

    def test_state_survives_restart(self):
        now = time.time()
        limiter1 = SqliteRateLimiter(limit=2, window_seconds=60)
        limiter1.allow("k_restart", now=now)
        limiter1.allow("k_restart", now=now + 1)

        # Simulate restart: fresh instance, same PostgreSQL DB
        limiter2 = SqliteRateLimiter(limit=2, window_seconds=60)
        assert limiter2.allow("k_restart", now=now + 2) is False

    def test_separate_keys_are_independent(self):
        limiter = SqliteRateLimiter(limit=1, window_seconds=60)
        now = time.time()
        assert limiter.allow("a_ind", now=now) is True
        assert limiter.allow("b_ind", now=now) is True
        assert limiter.allow("a_ind", now=now + 1) is False
        assert limiter.allow("b_ind", now=now + 1) is False


class TestSqliteFailureTracker:
    def test_not_blocked_initially(self):
        tracker = SqliteFailureTracker(limit=3, block_seconds=300)
        assert tracker.is_blocked("10.0.0.1") is False

    def test_blocks_after_limit(self):
        tracker = SqliteFailureTracker(limit=3, block_seconds=300, window_seconds=60)
        now = time.time()
        tracker.record_failure("10.0.0.2", now=now)
        tracker.record_failure("10.0.0.2", now=now + 1)
        assert tracker.is_blocked("10.0.0.2", now=now + 2) is False
        tracker.record_failure("10.0.0.2", now=now + 2)
        assert tracker.is_blocked("10.0.0.2", now=now + 3) is True

    def test_block_expires(self):
        tracker = SqliteFailureTracker(limit=2, block_seconds=60, window_seconds=300)
        now = time.time()
        tracker.record_failure("10.0.0.3", now=now)
        tracker.record_failure("10.0.0.3", now=now + 1)
        assert tracker.is_blocked("10.0.0.3", now=now + 2) is True
        assert tracker.is_blocked("10.0.0.3", now=now + 61) is False

    def test_reset_clears_block(self):
        tracker = SqliteFailureTracker(limit=2, block_seconds=300, window_seconds=300)
        now = time.time()
        tracker.record_failure("10.0.0.4", now=now)
        tracker.record_failure("10.0.0.4", now=now + 1)
        assert tracker.is_blocked("10.0.0.4", now=now + 2) is True
        tracker.reset("10.0.0.4")
        assert tracker.is_blocked("10.0.0.4", now=now + 2) is False

    def test_block_survives_restart(self):
        now = time.time()
        tracker1 = SqliteFailureTracker(limit=2, block_seconds=300, window_seconds=300)
        tracker1.record_failure("10.0.0.5", now=now)
        tracker1.record_failure("10.0.0.5", now=now + 1)
        assert tracker1.is_blocked("10.0.0.5", now=now + 2) is True

        # Simulate restart: fresh instance, same PostgreSQL DB
        tracker2 = SqliteFailureTracker(limit=2, block_seconds=300, window_seconds=300)
        assert tracker2.is_blocked("10.0.0.5", now=now + 2) is True

    def test_old_failures_slide_out_of_window(self):
        tracker = SqliteFailureTracker(limit=2, block_seconds=300, window_seconds=60)
        now = time.time()
        tracker.record_failure("10.0.0.6", now=now)
        tracker.record_failure("10.0.0.6", now=now + 1)
        assert tracker.is_blocked("10.0.0.6", now=now + 2) is True

        # After block expires AND fresh failures are outside the window,
        # one new failure should not re-block.
        tracker.record_failure("10.0.0.6", now=now + 400)
        assert tracker.is_blocked("10.0.0.6", now=now + 401) is False
