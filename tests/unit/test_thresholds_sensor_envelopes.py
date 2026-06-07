import pytest
from app.services.thresholds.sensor_envelopes import SENSOR_ENVELOPES

pytestmark = pytest.mark.no_isolated_db


def test_sensor_001_envelope_has_required_measurements():
    """Sensor-001 must have temperature, velocity_rms, battery_pct, humidity."""
    env = SENSOR_ENVELOPES["Sensor-001"]
    assert "temperature" in env["thresholds"]
    assert "velocity_rms" in env["thresholds"]
    assert "battery_pct" in env["thresholds"]
    assert "humidity" in env["thresholds"]


def test_plc_001_ai_range_thresholds():
    """PLC-001 must have AI1_voltage, broken-low/broken-high checks."""
    env = SENSOR_ENVELOPES["PLC-001"]
    assert "AI1_voltage" in env["thresholds"]
    assert "AI1_below_3p6ma" in env["thresholds"]  # broken sensor
    assert "AI1_above_21ma" in env["thresholds"]


def test_meter_001_voltage_imbalance_corrected():
    """Meter-001 voltage_imbalance warning/danger per NEMA (FIX: was 10%)."""
    env = SENSOR_ENVELOPES["Meter-001"]
    v = env["thresholds"]["v_imbalance_pct"]
    assert v["max_warning"] == 2.0  # NEMA: 2%
    assert v["max_danger"] == 5.0   # NEMA: 5%


def test_meter_001_phase_loss_threshold():
    """Phase loss: 1 phase <0.5A while others >5A = DANGER."""
    env = SENSOR_ENVELOPES["Meter-001"]
    pl = env["thresholds"]["phase_loss"]
    assert pl["min_current_a"] == 0.5
    assert pl["other_phase_min_a"] == 5
    assert pl["severity"] == "DANGER"


def test_all_thresholds_have_units():
    """Every threshold with numeric bounds must have a unit."""
    for device_id, env in SENSOR_ENVELOPES.items():
        for measurement, spec in env["thresholds"].items():
            if "min" in spec or "max" in spec:
                assert "unit" in spec, f"{device_id}.{measurement} missing unit"
