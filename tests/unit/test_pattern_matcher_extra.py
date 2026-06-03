"""Tests for the extended PatternRange model with `extra` field.

Backward compat: existing patterns (no `extra`) must keep working.
Extension: `extra` allows arbitrary measurement thresholds for new sensor
types (battery_pct, v_imbalance_pct, AI1_voltage, etc.).

These are pure pydantic-model unit tests, no DB access — opt out of the
``isolated_db`` autouse fixture via the ``no_isolated_db`` marker.
"""
import pytest
from app.models.ihi import PatternRange

pytestmark = pytest.mark.no_isolated_db


def test_pattern_range_extra_default_empty():
    """Backward compat: old pattern without extra should default to {}."""
    p = PatternRange(t_min=0, t_max=100, v_min=0, v_max=4.5, c_min=0, c_max=65)
    assert p.extra == {}


def test_pattern_range_with_extra_thresholds():
    """New pattern can carry extra measurement thresholds."""
    p = PatternRange(
        t_min=0, t_max=100, v_min=0, v_max=4.5, c_min=0, c_max=65,
        extra={"battery_pct": {"min_value": 10, "severity": "DANGER"}},
    )
    assert p.extra["battery_pct"]["min_value"] == 10
    assert p.extra["battery_pct"]["severity"] == "DANGER"


def test_pattern_range_json_serialization_includes_extra():
    """JSON output must include extra field for storage in PG JSONB."""
    p = PatternRange(extra={"v_imbalance_pct": {"max_value": 5.0}})
    json_str = p.model_dump_json()
    assert "v_imbalance_pct" in json_str
    assert "5.0" in json_str


# --- PatternMatcher.matches() extension tests (Task 12) ---
# Appended to verify PatternMatcher honors the new `extra` dict while staying
# backward-compatible with patterns that have no `extra` field.
from app.services.ihi_rag_service import PatternMatcher


def test_pattern_matcher_old_format_still_works():
    """Old patterns (no extra) should still work after extension."""
    m = PatternMatcher()
    pattern = {"t_min": 0, "t_max": 90, "v_min": 0, "v_max": 4.5, "c_min": 0, "c_max": 65}
    assert m.matches(pattern, {"t": 50, "v": 2.0, "c": 30}) is True
    assert m.matches(pattern, {"t": 95, "v": 2.0, "c": 30}) is False


def test_pattern_matcher_with_extra_battery_pct():
    """Pattern with extra battery_pct threshold."""
    m = PatternMatcher()
    pattern = {
        "t_min": 0, "t_max": 100, "v_min": 0, "v_max": 10, "c_min": 0, "c_max": 100,
        "extra": {"battery_pct": {"min_value": 10.0}},
    }
    # Reading with battery_pct above min_value → match
    assert m.matches(pattern, {"battery_pct": 50.0}) is True
    # Reading with battery_pct below min_value → no match
    assert m.matches(pattern, {"battery_pct": 5.0}) is False


def test_pattern_matcher_missing_extra_measurement_does_not_disqualify():
    """If reading doesn't have the extra measurement, it doesn't disqualify."""
    m = PatternMatcher()
    pattern = {
        "t_min": 0, "t_max": 100, "v_min": 0, "v_max": 10, "c_min": 0, "c_max": 100,
        "extra": {"battery_pct": {"min_value": 10.0}},
    }
    # Reading has no battery_pct → still matches (missing measurement OK)
    assert m.matches(pattern, {}) is True


def test_pattern_matcher_multiple_extra_thresholds():
    """Pattern with multiple extra thresholds — ALL must pass."""
    m = PatternMatcher()
    pattern = {
        "t_min": 0, "t_max": 100, "v_min": 0, "v_max": 10, "c_min": 0, "c_max": 100,
        "extra": {
            "battery_pct": {"min_value": 10.0},
            "v_imbalance_pct": {"max_value": 5.0},
        },
    }
    # Both within range → match
    assert m.matches(pattern, {"battery_pct": 50, "v_imbalance_pct": 2.0}) is True
    # v_imbalance too high → no match
    assert m.matches(pattern, {"battery_pct": 50, "v_imbalance_pct": 6.0}) is False
    # battery too low → no match
    assert m.matches(pattern, {"battery_pct": 5, "v_imbalance_pct": 2.0}) is False
