"""Tests for NEMA MG-1 motor standards thresholds.

Source: NEMA MG-1-2016 Part 14.
"""
import pytest
from app.services.thresholds.nema_mg1 import (
    NEMA_VOLTAGE_IMBALANCE,
    NEMA_TEMP_RISE,
    classify_voltage_imbalance,
)

pytestmark = pytest.mark.no_isolated_db


def test_nema_voltage_imbalance_thresholds():
    """NEMA MG-1 Part 14: 2% = warning, 5% = critical. Was 10% in old prompt."""
    assert NEMA_VOLTAGE_IMBALANCE["warning_pct"] == 2.0
    assert NEMA_VOLTAGE_IMBALANCE["danger_pct"] == 5.0
    assert "NEMA MG-1" in NEMA_VOLTAGE_IMBALANCE["source"]


def test_classify_voltage_imbalance():
    """v_imbalance=1% -> NORMAL; 3% -> WARNING; 6% -> DANGER."""
    assert classify_voltage_imbalance(1.0) == "NORMAL"
    assert classify_voltage_imbalance(2.0) == "NORMAL"  # boundary inclusive
    assert classify_voltage_imbalance(3.0) == "WARNING"
    assert classify_voltage_imbalance(5.0) == "NORMAL"  # boundary inclusive
    assert classify_voltage_imbalance(6.0) == "DANGER"


def test_nema_temp_rise_class_b():
    """NEMA Class B at SF=1.15: rise limit 90C, ref 130C."""
    assert NEMA_TEMP_RISE["B"]["ref_c"] == 130
    assert NEMA_TEMP_RISE["B"]["rise_sf115"] == 90
