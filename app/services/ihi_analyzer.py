from dataclasses import dataclass
from typing import List, Optional, Tuple

from app.models.ihi import AlertLevel


@dataclass
class AlertResult:
    device_id: str
    alert: AlertLevel
    reason: Optional[str]


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