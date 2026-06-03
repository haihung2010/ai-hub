import pytest
from app.services.thresholds.types import Threshold, ThresholdViolation

pytestmark = pytest.mark.no_isolated_db


def test_threshold_evaluate_within_range():
    """Reading within range returns NORMAL."""
    t = Threshold(
        measurement="temperature", min_value=0, max_value=90,
        severity="DANGER", unit="°C", source="test",
    )
    assert t.evaluate(50) == "NORMAL"
    assert t.evaluate(0) == "NORMAL"
    assert t.evaluate(90) == "NORMAL"  # boundary inclusive


def test_threshold_evaluate_above_max():
    """Reading above max returns severity."""
    t = Threshold(
        measurement="velocity_rms", min_value=None, max_value=4.5,
        severity="DANGER", unit="mm/s", source="ISO 10816-3",
    )
    assert t.evaluate(5.0) == "DANGER"
    assert t.evaluate(100) == "DANGER"


def test_threshold_evaluate_below_min():
    """Reading below min returns severity."""
    t = Threshold(
        measurement="battery_pct", min_value=10.0, max_value=None,
        severity="DANGER", unit="%", source="LoRaWAN convention",
    )
    assert t.evaluate(5.0) == "DANGER"
    assert t.evaluate(0.0) == "DANGER"


def test_threshold_violation_dataclass():
    """ThresholdViolation carries device + measurement + value."""
    t = Threshold("battery_pct", None, 10.0, "DANGER", "%", "test")
    v = ThresholdViolation(device_id="Sensor-001", measurement="battery_pct",
                           value=4.18, threshold=t, severity="DANGER")
    assert v.device_id == "Sensor-001"
    assert v.value == 4.18
    assert v.severity == "DANGER"
