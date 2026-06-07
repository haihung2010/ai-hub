"""Unit tests for IHIThresholdAnalyzer (Task 13).

Layer 1 of the 3-layer pipeline: threshold-based analysis using
the app.services.thresholds module.
"""
import pytest
from unittest.mock import MagicMock

from app.services.ihi_analyzer import IHIThresholdAnalyzer, AlertResult, AlertLevel

pytestmark = pytest.mark.no_isolated_db


@pytest.fixture
def mock_db_pool(monkeypatch):
    """Mock db_pool + override service to return no overrides.

    Patches the lookup site used by evaluate_all_thresholds so default
    sensor_envelopes thresholds are used (no DB-backed overrides).
    """
    monkeypatch.setattr(
        "app.services.thresholds.loader.get_active_override",
        lambda *args, **kwargs: None,
    )
    return MagicMock()


def test_analyzer_returns_normal_when_all_readings_ok(mock_db_pool):
    """All readings within default thresholds → NORMAL."""
    a = IHIThresholdAnalyzer(mock_db_pool)
    readings = {
        "Sensor-001": {"temperature": 50, "velocity_rms": 1.0, "battery_pct": 80, "humidity": 50},
    }
    result = a.analyze_readings(readings)
    assert result.alert == AlertLevel.NORMAL
    assert result.reason == "all readings within thresholds"


def test_analyzer_returns_danger_on_battery_critical(mock_db_pool):
    """battery 4% (Sensor-001 default min_danger=10) → DANGER."""
    a = IHIThresholdAnalyzer(mock_db_pool)
    readings = {
        "Sensor-001": {"temperature": 50, "velocity_rms": 1.0, "battery_pct": 4.0, "humidity": 50},
    }
    result = a.analyze_readings(readings)
    assert result.alert == AlertLevel.DANGER
    assert "battery_pct" in result.reason
    assert "Sensor-001" in result.devices


def test_analyzer_returns_danger_on_voltage_imbalance(mock_db_pool):
    """Meter-001 v_imbalance_pct=6 (NEMA: >5 = DANGER) → DANGER."""
    a = IHIThresholdAnalyzer(mock_db_pool)
    readings = {
        "Meter-001": {"v_imbalance_pct": 6.0, "f_hz": 50.0, "i_imbalance_pct": 5.0, "power_factor": 0.9},
    }
    result = a.analyze_readings(readings)
    assert result.alert == AlertLevel.DANGER
    assert "v_imbalance_pct" in result.reason
