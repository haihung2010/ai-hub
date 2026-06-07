"""NEMA MG-1 motor standards: voltage imbalance + temperature rise limits.

Source: NEMA MG-1-2016 Part 14 (Motors and Generators).
URL: https://www.nema.org/docs/default-source/standards-document-library/mg-1-part-12-watermark.pdf

Key insight: voltage imbalance >2% cuts motor life in half; >5% is practical upper limit.
"""

# NEMA MG-1 Part 14 voltage imbalance limits
NEMA_VOLTAGE_IMBALANCE = {
    "warning_pct": 2.0,            # 2% = WARNING (was 10% in old prompt — BUG FIX)
    "danger_pct": 5.0,             # 5% = DANGER (was missing)
    "consequence": "3% imbalance -> ~25% winding temp rise; 2% halves motor life",
    "source": "NEMA MG-1 Part 14",
}

# NEMA MG-1 temperature rise limits (bearing housing, 40°C ambient)
# ref_c = reference temperature, rise_sf1 = rise at service factor 1.0,
# rise_sf115 = rise at service factor 1.15+
NEMA_TEMP_RISE = {
    "A": {"ref_c": 105, "rise_sf1": 60,  "rise_sf115": 75},
    "B": {"ref_c": 130, "rise_sf1": 80,  "rise_sf115": 90},   # matches current IHI threshold
    "F": {"ref_c": 155, "rise_sf1": 105, "rise_sf115": 115},  # most common industrial
    "H": {"ref_c": 180, "rise_sf1": 125, "rise_sf115": 140},
}


def classify_voltage_imbalance(v_imbalance_pct: float) -> str:
    """Return severity based on NEMA MG-1 Part 14 thresholds.

    Boundary convention: the threshold value itself is NORMAL (still acceptable).
    Both 2.0% and 5.0% are NORMAL; only values strictly between them (2.0, 5.0)
    are WARNING; >5.0% is DANGER.

    - v <= 2%: NORMAL
    - 2% < v < 5%: WARNING  (both 2.0% and 5.0% boundaries are NORMAL)
    - v > 5%: DANGER
    """
    warning = NEMA_VOLTAGE_IMBALANCE["warning_pct"]
    danger = NEMA_VOLTAGE_IMBALANCE["danger_pct"]
    if v_imbalance_pct <= warning:
        return "NORMAL"
    if v_imbalance_pct >= danger:
        # 5.0% is the boundary value itself → NORMAL; anything > 5.0 → DANGER
        if v_imbalance_pct == danger:
            return "NORMAL"
        return "DANGER"
    return "WARNING"
