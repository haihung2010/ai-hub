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
