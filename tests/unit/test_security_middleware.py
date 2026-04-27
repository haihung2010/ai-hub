"""Unit coverage for in-memory rate limiting."""

from __future__ import annotations

import pytest

from app.middleware.security import InMemoryRateLimiter


@pytest.mark.unit
def test_rate_limiter_blocks_after_limit() -> None:
    limiter = InMemoryRateLimiter(limit=2, window_seconds=60)

    assert limiter.allow("key", now=0.0) is True
    assert limiter.allow("key", now=1.0) is True
    assert limiter.allow("key", now=2.0) is False


@pytest.mark.unit
def test_rate_limiter_resets_after_window() -> None:
    limiter = InMemoryRateLimiter(limit=1, window_seconds=60)

    assert limiter.allow("key", now=0.0) is True
    assert limiter.allow("key", now=1.0) is False
    assert limiter.allow("key", now=60.0) is True
