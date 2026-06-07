from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from app.models.ihi import AlertLevel
from app.services.ihi_overrides_service import get_active_override
from app.services.thresholds.loader import evaluate_all_thresholds


@dataclass
class AlertResult:
    device_id: str
    alert: AlertLevel
    reason: Optional[str]
    devices: List[str] = field(default_factory=list)


class IHIAnalyzer:
    """Rule-based IHI alert analyzer.

    CRITICAL (DANGER): temperature > 90 OR vibration > 6.0 OR current > 75
    WARNING: 85 < temperature <= 90 OR 4.5 < vibration <= 6.0 OR 65 < current <= 75
    NORMAL: no match
    """

    def analyze_reading(
        self, device_id: str, temperature: float, vibration: float, current: float
    ) -> AlertResult:
        # CRITICAL rules checked first
        if temperature > 90:
            return AlertResult(
                device_id=device_id,
                alert=AlertLevel.DANGER,
                reason="temperature > 90",
            )
        if vibration > 6.0:
            return AlertResult(
                device_id=device_id,
                alert=AlertLevel.DANGER,
                reason="vibration > 6.0",
            )
        if current > 75:
            return AlertResult(
                device_id=device_id,
                alert=AlertLevel.DANGER,
                reason="current > 75",
            )

        # WARNING rules
        if temperature > 85:
            return AlertResult(
                device_id=device_id,
                alert=AlertLevel.WARNING,
                reason="85 < temperature <= 90",
            )
        if vibration > 4.5:
            return AlertResult(
                device_id=device_id,
                alert=AlertLevel.WARNING,
                reason="4.5 < vibration <= 6.0",
            )
        if current > 65:
            return AlertResult(
                device_id=device_id,
                alert=AlertLevel.WARNING,
                reason="65 < current <= 75",
            )

        # No match
        return AlertResult(device_id=device_id, alert=AlertLevel.NORMAL, reason=None)

    def analyze_batch(
        self, readings: List[Tuple[str, float, float, float]]
    ) -> List[AlertResult]:
        """Analyze multiple readings.

        Args:
            readings: List of (device_id, temperature, vibration, current) tuples
        """
        return [
            self.analyze_reading(device_id, temperature, vibration, current)
            for device_id, temperature, vibration, current in readings
        ]

    def get_danger_devices(
        self, readings: List[Tuple[str, float, float, float]]
    ) -> List[str]:
        """Return device IDs that triggered DANGER alert."""
        return [
            device_id
            for device_id, temperature, vibration, current in readings
            if self.analyze_reading(device_id, temperature, vibration, current).alert
            == AlertLevel.DANGER
        ]

    def get_warning_devices(
        self, readings: List[Tuple[str, float, float, float]]
    ) -> List[str]:
        """Return device IDs that triggered WARNING alert."""
        return [
            device_id
            for device_id, temperature, vibration, current in readings
            if self.analyze_reading(device_id, temperature, vibration, current).alert
            == AlertLevel.WARNING
        ]


# === New threshold-based analyzer (Layer 1 of 3-layer pipeline) ===


class IHIThresholdAnalyzer:
    """Analyzes readings using the thresholds module (Layer 1).

    Returns AlertResult based on threshold violations:
    - DANGER: any DANGER violation
    - WARNING: any WARNING violation
    - NORMAL: no violations

    Replaces the legacy IHIAnalyzer.analyze_reading() which only checks t/v/c.
    """

    def __init__(self, db_pool=None):
        self.db_pool = db_pool

    def analyze_readings(self, device_readings: Dict[str, Dict]) -> AlertResult:
        """Analyze readings for multiple devices.

        Args:
            device_readings: {device_id: {measurement: value}}

        Returns:
            AlertResult with alert level and reason summary
        """
        all_violations = []
        devices_with_violations = set()
        for device_id, readings in device_readings.items():
            violations = evaluate_all_thresholds(self.db_pool, device_id, readings)
            all_violations.extend(violations)
            for v in violations:
                devices_with_violations.add(v.device_id)

        if not all_violations:
            return AlertResult(
                device_id="",
                alert=AlertLevel.NORMAL,
                reason="all readings within thresholds",
            )

        # Check for DANGER first
        danger_violations = [v for v in all_violations if v.severity == "DANGER"]
        if danger_violations:
            first = danger_violations[0]
            return AlertResult(
                device_id=first.device_id,
                alert=AlertLevel.DANGER,
                reason=(
                    f"DANGER: {first.measurement}={first.value}{first.threshold.unit} "
                    f"(threshold: {first.threshold.severity} from {first.threshold.source})"
                ),
                devices=sorted(devices_with_violations),
            )

        # WARNING only
        warning = all_violations[0]
        return AlertResult(
            device_id=warning.device_id,
            alert=AlertLevel.WARNING,
            reason=(
                f"WARNING: {warning.measurement}={warning.value}{warning.threshold.unit} "
                f"(threshold: {warning.threshold.severity} from {warning.threshold.source})"
            ),
            devices=sorted(devices_with_violations),
        )