"""Shared dataclasses for IHI thresholds."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Threshold:
    """A single numeric threshold with min/max bounds and severity.

    `evaluate(value)` returns the severity if the value violates the bounds,
    or "NORMAL" if it does not. Boundary values (== min or == max) are NORMAL.
    """
    measurement: str
    min_value: float | None
    max_value: float | None
    severity: str         # "NORMAL" / "WARNING" / "DANGER"
    unit: str
    source: str           # "manual" | "auto_learned" | "ISO 10816" | "NEMA MG-1" | ...
    standard_ref: str | None = None
    note: str | None = None

    def evaluate(self, value: float) -> str:
        """Return severity if value violates bounds, else NORMAL."""
        if self.min_value is not None and value < self.min_value:
            return self.severity
        if self.max_value is not None and value > self.max_value:
            return self.severity
        return "NORMAL"


@dataclass(frozen=True)
class ThresholdViolation:
    """A recorded threshold violation: which device, which measurement, which value, what threshold."""
    device_id: str
    measurement: str
    value: float
    threshold: Threshold
    severity: str         # "WARNING" or "DANGER" (not NORMAL)
