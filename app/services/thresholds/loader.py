"""Unified threshold resolution with trust hierarchy.

Trust order (highest first):
1. Manual override (`ihi_device_overrides` table)
2. Auto-learned (RAG case match with high confidence) — implemented in Phase 7 (retrieve_top_k)
3. Default standard (sensor_envelopes.py)
"""
from __future__ import annotations

from typing import Optional

from app.services.ihi_overrides_service import get_active_override
from app.services.thresholds.sensor_envelopes import SENSOR_ENVELOPES
from app.services.thresholds.types import Threshold, ThresholdViolation


def get_effective_threshold(db_pool, device_id: str, measurement: str) -> Optional[Threshold]:
    """Resolve threshold for (device, measurement) using trust hierarchy.

    Returns None if no threshold is known for this combination.
    """
    # Trust 1: manual override
    override = get_active_override(db_pool, device_id, measurement)
    if override is not None:
        return Threshold(
            measurement=measurement,
            min_value=override.min_value,
            max_value=override.max_value,
            severity=override.severity,
            unit=_get_unit(device_id, measurement),
            source=override.source,  # "manual" or "auto_learned"
            standard_ref=f"override set by {override.set_by}" if override.set_by else None,
            note=override.note,
        )
    # Trust 3: default from sensor_envelopes
    envelope = SENSOR_ENVELOPES.get(device_id)
    if envelope is None:
        return None
    spec = envelope["thresholds"].get(measurement)
    if spec is None:
        return None
    return _threshold_from_spec(device_id, measurement, spec, envelope["source"])


def evaluate_all_thresholds(db_pool, device_id: str, readings: dict) -> list[ThresholdViolation]:
    """Evaluate all readings against effective thresholds; return violations."""
    violations = []
    for measurement, value in readings.items():
        if value is None:
            continue
        threshold = get_effective_threshold(db_pool, device_id, measurement)
        if threshold is None:
            continue
        severity = threshold.evaluate(value)
        if severity in ("WARNING", "DANGER"):
            violations.append(ThresholdViolation(
                device_id=device_id, measurement=measurement,
                value=value, threshold=threshold, severity=severity,
            ))
    return violations


def _threshold_from_spec(device_id, measurement, spec: dict, source: str) -> Threshold:
    """Build a Threshold from a sensor_envelopes spec dict."""
    min_v = spec.get("min_danger") or spec.get("min_warning") or spec.get("min_normal")
    max_v = spec.get("max_danger") or spec.get("max_warning") or spec.get("max_normal")
    severity = spec.get("severity", "DANGER")
    # For ranges: pick the more severe of (min_warning, min_danger) and (max_warning, max_danger)
    # Since the spec already includes both, we use the warning band as the boundary;
    # the danger band is the violation.
    if "max_danger" in spec:
        min_v = spec.get("min_danger", min_v)
        max_v = spec["max_danger"]
    elif "max_warning" in spec and "min_warning" not in spec:
        # Only one side
        max_v = spec["max_warning"]
    if "min_danger" in spec:
        min_v = spec["min_danger"]
    return Threshold(
        measurement=measurement,
        min_value=min_v, max_value=max_v,
        severity=severity, unit=spec.get("unit", "?"),
        source=source, standard_ref=source,
        note=spec.get("note"),
    )


def _get_unit(device_id: str, measurement: str) -> str:
    """Lookup unit from sensor_envelopes spec."""
    env = SENSOR_ENVELOPES.get(device_id, {})
    spec = env.get("thresholds", {}).get(measurement, {})
    return spec.get("unit", "?")
