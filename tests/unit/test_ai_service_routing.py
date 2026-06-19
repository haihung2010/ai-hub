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


# AIService._is_order_lookup_intent — detects order-code queries so we can
# route them to a more reliable model setting (deterministic temperature).
# E-commerce 100-user test (2026-06-14) showed ~20% accuracy variance with
# temperature=0.7 because the LLM occasionally hallucinated the order code.
@pytest.mark.unit
@pytest.mark.no_isolated_db
def test_is_order_lookup_intent_detects_ord_prefix() -> None:
    assert AIService._is_order_lookup_intent("Tôi muốn đổi trả đơn ORD-2026-0001") is True
    assert AIService._is_order_lookup_intent("ORD-ABC-12345 bị lỗi") is True
    assert AIService._is_order_lookup_intent("mã đơn ord-_098-25842 là gì") is True


@pytest.mark.unit
@pytest.mark.no_isolated_db
def test_is_order_lookup_intent_returns_false_for_normal_chat() -> None:
    assert AIService._is_order_lookup_intent("Có áo thun trắng size M không?") is False
    assert AIService._is_order_lookup_intent("Tôi muốn mua thêm áo thun") is False
    assert AIService._is_order_lookup_intent("Bạn còn nhớ tôi mua gì hôm trước?") is False


def test_no_fast_background_projects_constant_includes_fanpage():
    """fanpage is in the no-fast-background whitelist (regression 2026-06-20)."""
    from app.services.ai_service import _NO_FAST_BACKGROUND_PROJECTS
    assert "fanpage" in _NO_FAST_BACKGROUND_PROJECTS
    assert "ecommerce" in _NO_FAST_BACKGROUND_PROJECTS
    assert "vehix" in _NO_FAST_BACKGROUND_PROJECTS
    assert "iot" in _NO_FAST_BACKGROUND_PROJECTS
    assert "playground" in _NO_FAST_BACKGROUND_PROJECTS
    # ihi is NOT in the whitelist (it's already excluded by the ihi check above)
