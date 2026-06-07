"""Unit tests for ISO 10816-3 vibration severity zones."""
import pytest
from app.services.thresholds.iso_10816 import (
    ISO_10816_ZONES, DEFAULT_ISO_CLASS, classify_vibration_zone,
)

pytestmark = pytest.mark.no_isolated_db


def test_class_ii_rigid_zone_boundaries():
    """Class II rigid: A=0-1.4, B=1.4-2.8, C=2.8-4.5, D>4.5."""
    zones = ISO_10816_ZONES[("II", "rigid")]
    assert zones["A"][1] == 1.4
    assert zones["B"][1] == 2.8
    assert zones["C"][1] == 4.5


def test_default_iso_class_is_ii_rigid():
    """Default machine class for Sensor-001 should be Class II rigid (most common)."""
    assert DEFAULT_ISO_CLASS == ("II", "rigid")


def test_classify_vibration_zone_class_ii_rigid():
    """v=1.0 → A (NORMAL); v=3.0 → C (WARNING); v=5.0 → D (DANGER)."""
    machine_class = ("II", "rigid")
    assert classify_vibration_zone(1.0, machine_class) == "A"
    assert classify_vibration_zone(3.0, machine_class) == "C"
    assert classify_vibration_zone(5.0, machine_class) == "D"
    # Boundary: v=2.8 is in zone B (not C, since 2.8 == C.min)
    assert classify_vibration_zone(2.8, machine_class) == "B"


def test_all_classes_defined():
    """All 4 ISO classes × 2 foundations should be defined."""
    for cls in ("I", "II", "III", "IV"):
        for foundation in ("rigid", "flexible"):
            assert (cls, foundation) in ISO_10816_ZONES, f"Missing {cls}/{foundation}"
