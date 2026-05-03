from __future__ import annotations

import pytest

from app.services.ai_service import _LatencyTracker, AIService


# ---------------------------------------------------------------------------
# _LatencyTracker
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_latency_tracker_not_elevated_before_min_samples() -> None:
    tracker = _LatencyTracker(window=20, threshold_ms=8000.0)
    # min_samples = max(3, 20 // 4) = 5
    for _ in range(4):
        tracker.record(15000.0)  # very high, but not enough samples
    assert not tracker.is_elevated()


@pytest.mark.unit
def test_latency_tracker_elevated_when_avg_exceeds_threshold() -> None:
    tracker = _LatencyTracker(window=20, threshold_ms=8000.0)
    for _ in range(5):
        tracker.record(10000.0)  # avg = 10000 > 8000
    assert tracker.is_elevated()


@pytest.mark.unit
def test_latency_tracker_not_elevated_when_avg_below_threshold() -> None:
    tracker = _LatencyTracker(window=20, threshold_ms=8000.0)
    for _ in range(5):
        tracker.record(4000.0)  # avg = 4000 < 8000
    assert not tracker.is_elevated()


@pytest.mark.unit
def test_latency_tracker_recovers_when_window_fills_with_fast_samples() -> None:
    tracker = _LatencyTracker(window=5, threshold_ms=8000.0)
    # Fill with slow samples
    for _ in range(5):
        tracker.record(12000.0)
    assert tracker.is_elevated()
    # Overwrite with fast samples
    for _ in range(5):
        tracker.record(2000.0)
    assert not tracker.is_elevated()


@pytest.mark.unit
def test_latency_tracker_window_evicts_oldest_sample() -> None:
    tracker = _LatencyTracker(window=3, threshold_ms=8000.0)
    # min_samples = max(3, 3//4) = 3
    tracker.record(12000.0)
    tracker.record(12000.0)
    tracker.record(12000.0)
    assert tracker.is_elevated()
    # Add 3 fast samples — oldest 3 (slow) get evicted
    tracker.record(1000.0)
    tracker.record(1000.0)
    tracker.record(1000.0)
    assert not tracker.is_elevated()


# ---------------------------------------------------------------------------
# AIService._extract_explicit_search_query (static, no DI needed)
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_extract_explicit_search_query_detects_colon_form() -> None:
    result = AIService._extract_explicit_search_query("/search: latest AI news")
    assert result == "latest AI news"


@pytest.mark.unit
def test_extract_explicit_search_query_detects_space_form() -> None:
    result = AIService._extract_explicit_search_query("/search latest AI news")
    assert result == "latest AI news"


@pytest.mark.unit
def test_extract_explicit_search_query_returns_none_for_regular_message() -> None:
    result = AIService._extract_explicit_search_query("what is the weather today")
    assert result is None


@pytest.mark.unit
def test_extract_explicit_search_query_case_insensitive() -> None:
    result = AIService._extract_explicit_search_query("/SEARCH: bitcoin price")
    assert result == "bitcoin price"
