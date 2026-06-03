import pytest
from unittest.mock import MagicMock
from app.services.thresholds.loader import get_effective_threshold, evaluate_all_thresholds
from app.services.thresholds.types import Threshold
from app.services.ihi_overrides_service import DeviceOverride

pytestmark = pytest.mark.no_isolated_db


def test_get_threshold_default_when_no_override(monkeypatch):
    """Without override, return default from sensor_envelopes."""
    monkeypatch.setattr(
        "app.services.thresholds.loader.get_active_override",
        lambda *args, **kwargs: None
    )
    t = get_effective_threshold(None, "Sensor-001", "battery_pct")
    assert t is not None
    assert t.measurement == "battery_pct"
    assert t.max_value is None
    assert t.min_value == 10.0  # default min_danger
    assert t.severity == "DANGER"


def test_get_threshold_override_wins(monkeypatch):
    """Manual override takes priority over default."""
    override = DeviceOverride(
        device_id="Sensor-001", measurement="battery_pct",
        min_value=50.0, max_value=None, severity="DANGER",
        source="manual", set_by="operator", note="Old machine, looser battery threshold"
    )
    monkeypatch.setattr(
        "app.services.thresholds.loader.get_active_override",
        lambda *args, **kwargs: override
    )
    t = get_effective_threshold(None, "Sensor-001", "battery_pct")
    assert t.source == "manual"
    assert t.min_value == 50.0  # override value, not default 10.0


def test_get_threshold_unknown_device_returns_none(monkeypatch):
    """Unknown device_id returns None (no threshold defined)."""
    monkeypatch.setattr(
        "app.services.thresholds.loader.get_active_override",
        lambda *args, **kwargs: None
    )
    t = get_effective_threshold(None, "Unknown-Device", "battery_pct")
    assert t is None


def test_evaluate_all_thresholds_detects_violations(monkeypatch):
    """evaluate_all_thresholds returns list of ThresholdViolation for violations."""
    monkeypatch.setattr(
        "app.services.thresholds.loader.get_active_override",
        lambda *args, **kwargs: None
    )
    readings = {
        "temperature": 95,      # > 90 = DANGER
        "velocity_rms": 1.0,    # OK
        "battery_pct": 5.0,     # < 10 = DANGER
        "humidity": 50,         # OK
    }
    violations = evaluate_all_thresholds(None, "Sensor-001", readings)
    measurements = {v.measurement for v in violations}
    assert "temperature" in measurements
    assert "battery_pct" in measurements
    assert "velocity_rms" not in measurements
    assert "humidity" not in measurements
    # All Sensor-001 violations should be DANGER (battery, temp) — no warnings expected
    assert all(v.severity == "DANGER" for v in violations)
