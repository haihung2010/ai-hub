"""Unified threshold resolution with trust hierarchy.

Trust order (highest first):
1. Manual override (`ihi_device_overrides` table)
2. Auto-learned (RAG case match with high confidence) — implemented in Phase 7 (retrieve_top_k)
3. Default standard (sensor_envelopes.py)
"""
from __future__ import annotations

from typing import Optional

from app.services.ihi_overrides_service import get_active_override
from app.services.thresholds.sensor_envelopes import SENSOR_ENVELOPES, DEFAULT_ENVELOPE
from app.services.thresholds.types import Threshold, ThresholdViolation


def get_effective_thresholds(db_pool, device_id: str, measurement: str) -> list[Threshold]:
    """Resolve ALL applicable thresholds for (device, measurement) using trust hierarchy.

    Returns a list (usually 1, but 2 for warning+danger bands like temperature 80°C/90°C).
    Returns [] if no threshold is known for this combination.
    """
    # Trust 1: manual override (single threshold, severity from override)
    override = get_active_override(db_pool, device_id, measurement)
    if override is not None:
        return [Threshold(
            measurement=measurement,
            min_value=override.min_value,
            max_value=override.max_value,
            severity=override.severity,
            unit=_get_unit(device_id, measurement),
            source=override.source,
            standard_ref=f"override set by {override.set_by}" if override.set_by else None,
            note=override.note,
        )]
    # Trust 3: default from sensor_envelopes (with DEFAULT_ENVELOPE fallback for unknown devices)
    envelope = SENSOR_ENVELOPES.get(device_id) or DEFAULT_ENVELOPE
    spec = envelope["thresholds"].get(measurement)
    if spec is None:
        return []
    return _thresholds_from_spec(measurement, spec, envelope["source"])


# Backward-compat: singular form returns first threshold (usually the DANGER one).
def get_effective_threshold(db_pool, device_id: str, measurement: str) -> Optional[Threshold]:
    """Return the first (most-severe) threshold, or None."""
    thresholds = get_effective_thresholds(db_pool, device_id, measurement)
    # Return DANGER threshold first if present
    for t in thresholds:
        if t.severity == "DANGER":
            return t
    return thresholds[0] if thresholds else None


def evaluate_all_thresholds(db_pool, device_id: str, readings: dict) -> list[ThresholdViolation]:
    """Evaluate all readings against effective thresholds; return violations.

    Each (measurement, severity) combination can produce a violation.
    """
    violations = []
    for measurement, value in readings.items():
        if value is None:
            continue
        thresholds = get_effective_thresholds(db_pool, device_id, measurement)
        for threshold in thresholds:
            severity = threshold.evaluate(value)
            if severity in ("WARNING", "DANGER"):
                violations.append(ThresholdViolation(
                    device_id=device_id, measurement=measurement,
                    value=value, threshold=threshold, severity=severity,
                ))
    return violations


def _thresholds_from_spec(measurement: str, spec: dict, source: str) -> list[Threshold]:
    """Build 0, 1, or 2 Threshold objects from a spec.

    Specs can have:
    - max_warning: produce WARNING threshold
    - max_danger:  produce DANGER threshold (above max_danger)
    - min_warning: produce WARNING threshold (below min_warning)
    - min_danger:  produce DANGER threshold (below min_danger)
    """
    thresholds = []
    unit = spec.get("unit", "?")
    note = spec.get("note")

    # Max side
    if "max_warning" in spec:
        thresholds.append(Threshold(
            measurement=measurement, min_value=None, max_value=spec["max_warning"],
            severity="WARNING", unit=unit, source=source, standard_ref=source, note=note,
        ))
    if "max_danger" in spec:
        thresholds.append(Threshold(
            measurement=measurement, min_value=None, max_value=spec["max_danger"],
            severity="DANGER", unit=unit, source=source, standard_ref=source, note=note,
        ))

    # Min side
    if "min_warning" in spec:
        thresholds.append(Threshold(
            measurement=measurement, min_value=spec["min_warning"], max_value=None,
            severity="WARNING", unit=unit, source=source, standard_ref=source, note=note,
        ))
    if "min_danger" in spec:
        thresholds.append(Threshold(
            measurement=measurement, min_value=spec["min_danger"], max_value=None,
            severity="DANGER", unit=unit, source=source, standard_ref=source, note=note,
        ))

    # Single-severity spec (e.g., phase_loss with just severity="DANGER") — preserve as-is
    if not thresholds and spec.get("severity"):
        # No numeric bounds, just a severity marker — pass through with None bounds
        # (won't trigger from evaluate() but the marker survives for callers that read .severity)
        thresholds.append(Threshold(
            measurement=measurement, min_value=None, max_value=None,
            severity=spec["severity"], unit=unit, source=source, standard_ref=source, note=note,
        ))

    return thresholds


def _get_unit(device_id: str, measurement: str) -> str:
    """Lookup unit from sensor_envelopes spec (with DEFAULT_ENVELOPE fallback)."""
    env = SENSOR_ENVELOPES.get(device_id) or DEFAULT_ENVELOPE
    spec = env.get("thresholds", {}).get(measurement, {})
    return spec.get("unit", "?")
