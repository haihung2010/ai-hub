"""ISO 10816-3:2009 mechanical vibration severity zones.

Each zone has: (min_mm_s, max_mm_s, severity, description_vi, color)
- A: New machine, excellent
- B: Acceptable for long-term operation
- C: Unsatisfactory, plan maintenance
- D: Damage likely, immediate action

Class II rigid (15-300 kW motors) is the most common industrial use case.

Boundary convention: zone B's upper bound is inclusive (so the threshold value
itself is still "acceptable"). E.g. Class II rigid: 2.8 mm/s → zone B, not C.
"""

# Map: (machine_class, foundation_type) -> {zone: (min, max, severity, desc_vi, color)}
ISO_10816_ZONES = {
    # Class I — small machines (<15 kW)
    ("I", "rigid"): {
        "A": (0.0,   0.71, "NORMAL",  "Mới, rất tốt", "green"),
        "B": (0.71,  1.8,  "NORMAL",  "Chấp nhận được", "yellow"),
        "C": (1.8,   4.5,  "WARNING", "Không thỏa mãn, lên kế hoạch BT", "orange"),
        "D": (4.5,   float("inf"), "DANGER", "Nguy hại", "red"),
    },
    ("I", "flexible"): {
        "A": (0.0, 1.4, "NORMAL",  "Mới", "green"),
        "B": (1.4, 2.8, "NORMAL",  "Chấp nhận được", "yellow"),
        "C": (2.8, 7.1, "WARNING", "Lên kế hoạch BT", "orange"),
        "D": (7.1, float("inf"), "DANGER", "Nguy hại", "red"),
    },
    # Class II — medium machines (15-300 kW) — MOST COMMON
    ("II", "rigid"): {
        "A": (0.0,  1.4,  "NORMAL",  "Mới, rung rất tốt", "green"),
        "B": (1.4,  2.8,  "NORMAL",  "Chấp nhận lâu dài", "yellow"),
        "C": (2.8,  4.5,  "WARNING", "Lên kế hoạch bảo trì", "orange"),
        "D": (4.5,  float("inf"), "DANGER", "Nguy hại, hành động ngay", "red"),
    },
    ("II", "flexible"): {
        "A": (0.0, 2.3, "NORMAL",  "Mới", "green"),
        "B": (2.3, 4.5, "NORMAL",  "Chấp nhận được", "yellow"),
        "C": (4.5, 7.1, "WARNING", "Lên kế hoạch BT", "orange"),
        "D": (7.1, float("inf"), "DANGER", "Nguy hại", "red"),
    },
    # Class III — large machines on rigid foundations
    ("III", "rigid"): {
        "A": (0.0,  1.4,  "NORMAL",  "Mới", "green"),
        "B": (1.4,  2.8,  "NORMAL",  "Chấp nhận được", "yellow"),
        "C": (2.8,  4.5,  "WARNING", "Lên kế hoạch BT", "orange"),
        "D": (4.5,  float("inf"), "DANGER", "Nguy hại", "red"),
    },
    ("III", "flexible"): {
        "A": (0.0, 2.3, "NORMAL",  "Mới", "green"),
        "B": (2.3, 4.5, "NORMAL",  "Chấp nhận được", "yellow"),
        "C": (4.5, 7.1, "WARNING", "Lên kế hoạch BT", "orange"),
        "D": (7.1, float("inf"), "DANGER", "Nguy hại", "red"),
    },
    # Class IV — large machines on flexible foundations (turbines)
    ("IV", "rigid"): {
        "A": (0.0,  1.4,  "NORMAL",  "Mới", "green"),
        "B": (1.4,  2.8,  "NORMAL",  "Chấp nhận được", "yellow"),
        "C": (2.8,  4.5,  "WARNING", "Lên kế hoạch BT", "orange"),
        "D": (4.5,  float("inf"), "DANGER", "Nguy hại", "red"),
    },
    ("IV", "flexible"): {
        "A": (0.0, 2.3, "NORMAL",  "Mới", "green"),
        "B": (2.3, 4.5, "NORMAL",  "Chấp nhận được", "yellow"),
        "C": (4.5, 7.1, "WARNING", "Lên kế hoạch BT", "orange"),
        "D": (7.1, float("inf"), "DANGER", "Nguy hại", "red"),
    },
}

# Sensor-001 default: Class II rigid (15-300 kW motor on rigid foundation)
DEFAULT_ISO_CLASS = ("II", "rigid")


def classify_vibration_zone(velocity_rms_mm_s: float, machine_class: tuple) -> str:
    """Return zone letter ("A"/"B"/"C"/"D") for given velocity and machine class.

    Boundary convention: the upper bound of each zone is inclusive, so e.g.
    v=2.8 mm/s for Class II rigid → zone B (acceptable), not zone C.
    The "D" zone has no upper bound; any v >= D.min is D.
    """
    zones = ISO_10816_ZONES[machine_class]
    for zone in ("A", "B", "C", "D"):
        min_v, max_v, _, _, _ = zones[zone]
        if zone == "D":
            if velocity_rms_mm_s >= min_v:
                return "D"
        else:
            if min_v <= velocity_rms_mm_s <= max_v:
                return zone
    return "D"
