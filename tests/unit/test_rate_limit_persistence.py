"""Tests for InMemory rate limiter and failure tracker."""

from __future__ import annotations

import time

import pytest

from app.middleware.security import (
    AuthFailureTracker,
    InMemoryRateLimiter,
    SecurityMiddleware,
)


class TestInMemoryRateLimiter:
    def test_rate_limit_key_does_not_include_raw_api_key(self):
        raw_key = "sk-sec...sted"
        rate_key = SecurityMiddleware._rate_limit_key("127.0.0.1", raw_key, None)

        assert raw_key not in rate_key
        assert rate_key.startswith("127.0.0.1:")

    def test_allows_up_to_limit(self):
        limiter = InMemoryRateLimiter(limit=3, window_seconds=60)
        now = time.time()
        assert limiter.allow("k", now=now) is True
        assert limiter.allow("k", now=now + 1) is True
        assert limiter.allow("k", now=now + 2) is True
        assert limiter.allow("k", now=now + 3) is False

    def test_resets_after_window(self):
        limiter = InMemoryRateLimiter(limit=1, window_seconds=60)
        now = time.time()
        assert limiter.allow("k2", now=now) is True
        assert limiter.allow("k2", now=now + 30) is False
        assert limiter.allow("k2", now=now + 61) is True

    def test_separate_keys_are_independent(self):
        limiter = InMemoryRateLimiter(limit=1, window_seconds=60)
        now = time.time()
        assert limiter.allow("a_ind", now=now) is True
        assert limiter.allow("b_ind", now=now) is True
        assert limiter.allow("a_ind", now=now + 1) is False
        assert limiter.allow("b_ind", now=now + 1) is False

    def test_high_concurrency_no_crash(self):
        """Stress test: many keys at once."""
        limiter = InMemoryRateLimiter(limit=5, window_seconds=60)
        now = time.time()
        for i in range(100):
            for j in range(5):
                limiter.allow(f"key_{i}", now=now + j)
            assert limiter.allow(f"key_{i}", now=now + 5) is False


class TestAuthFailureTracker:
    def test_not_blocked_initially(self):
        tracker = AuthFailureTracker(limit=3, block_seconds=300)
        assert tracker.is_blocked("10.0.0.1") is False

    def test_blocks_after_limit(self):
        tracker = AuthFailureTracker(limit=3, block_seconds=300, window_seconds=60)
        now = time.time()
        tracker.record_failure("10.0.0.2", now=now)
        tracker.record_failure("10.0.0.2", now=now + 1)
        assert tracker.is_blocked("10.0.0.2", now=now + 2) is False
        tracker.record_failure("10.0.0.2", now=now + 2)
        assert tracker.is_blocked("10.0.0.2", now=now + 3) is True

    def test_block_expires(self):
        tracker = AuthFailureTracker(limit=2, block_seconds=60, window_seconds=300)
        now = time.time()
        tracker.record_failure("10.0.0.3", now=now)
        tracker.record_failure("10.0.0.3", now=now + 1)
        assert tracker.is_blocked("10.0.0.3", now=now + 2) is True
        assert tracker.is_blocked("10.0.0.3", now=now + 61) is False

    def test_reset_clears_block(self):
        tracker = AuthFailureTracker(limit=2, block_seconds=300, window_seconds=300)
        now = time.time()
        tracker.record_failure("10.0.0.4", now=now)
        tracker.record_failure("10.0.0.4", now=now + 1)
        assert tracker.is_blocked("10.0.0.4", now=now + 2) is True
        tracker.reset("10.0.0.4")
        assert tracker.is_blocked("10.0.0.4", now=now + 2) is False

    def test_old_failures_slide_out_of_window(self):
        tracker = AuthFailureTracker(limit=2, block_seconds=300, window_seconds=60)
        now = time.time()
        tracker.record_failure("10.0.0.6", now=now)
        tracker.record_failure("10.0.0.6", now=now + 1)
        assert tracker.is_blocked("10.0.0.6", now=now + 2) is True

        # After block expires AND fresh failures are outside the window
        tracker.record_failure("10.0.0.6", now=now + 400)
        assert tracker.is_blocked("10.0.0.6", now=now + 401) is False
