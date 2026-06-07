"""Per-device normal operating envelopes — default thresholds when no override exists.

Sources: ISO 10816-3 (vibration), NEMA MG-1 (motor limits), IEEE 1159 (power quality),
IEC 61000-2-4 (industrial voltage), LoRaWAN sensor convention (battery), 4-20mA process
instrumentation standard (PLC analog).

Units: every numeric threshold MUST carry a unit. Use SI metric.
"""

# Each envelope: {type, default_class (for vibration), thresholds: {measurement: {min/max, unit, severity}}, source}
SENSOR_ENVELOPES = {
    "Sensor-001": {
        "type": "wireless_vibration_temp_humidity_battery",
        "default_class": ("II", "rigid"),  # ISO 10816-3: 15-300 kW motor, rigid foundation
        "thresholds": {
            "temperature":   {"max_warning": 80, "max_danger": 90, "unit": "°C"},
            "velocity_rms":  {"max_warning": 2.8, "max_danger": 4.5, "unit": "mm/s"},
            "battery_pct":   {"min_warning": 20, "min_danger": 10, "unit": "%"},
            "humidity":      {"min_warning": 20, "max_warning": 80, "unit": "%"},
        },
        "source": "ISO 10816-3 Class II rigid; LoRaWAN sensor convention",
    },
    "PLC-001": {
        "type": "digital_io_analog_input",
        "thresholds": {
            "AI1_voltage":     {"min_normal": 0,  "max_normal": 10,  "unit": "V"},
            "AI1_ma_equiv":    {"min_normal": 4,  "max_normal": 20,  "unit": "mA"},
            "AI1_below_3p6ma": {"max": 3.6, "severity": "DANGER", "unit": "mA",
                                "note": "Below 4-20mA zero = broken sensor"},
            "AI1_above_21ma":  {"min": 21,  "severity": "DANGER", "unit": "mA",
                                "note": "Above 20mA range = broken sensor"},
            "DI_change_rate":  {"max_per_minute": 5, "severity": "WARNING",
                                "note": "Rapid DI changes indicate instability"},
        },
        "source": "Standard 4-20mA process instrumentation (Honeywell/Yokogawa/ABB convention)",
    },
    "Meter-001": {
        "type": "3_phase_electric",
        "thresholds": {
            # CRITICAL FIX: NEMA MG-1 says 2%/5%, not 10%
            "v_imbalance_pct":  {"max_warning": 2.0, "max_danger": 5.0, "unit": "%"},
            "f_hz":             {"min": 49.0, "max": 51.0, "unit": "Hz"},
            "v_min":            {"min_warning": 207, "min_danger": 195, "unit": "V"},
            "v_max":            {"max_warning": 233, "max_danger": 245, "unit": "V"},
            "i_imbalance_pct":  {"max_warning": 10, "max_danger": 25, "unit": "%"},
            "power_factor":     {"min": 0.7, "severity": "WARNING", "unit": "ratio"},
            "phase_loss":       {"min_current_a": 0.5, "other_phase_min_a": 5,
                                 "severity": "DANGER", "note": "Single phase near 0A while others loaded"},
            "all_phases_zero":  {"max_total": 0.5, "severity": "DANGER", "unit": "A",
                                 "note": "All 3 phases near 0A = machine off OR total phase loss"},
        },
        "source": "NEMA MG-1 Part 14, IEEE 1159, IEC 61000-2-4",
    },
}

# Default envelope for unknown device_ids (legacy compatibility).
# Used by the loader when device_id is not in SENSOR_ENVELOPES.
# Matches the legacy IHIAnalyzer.analyze_reading() thresholds so existing
# t/v/c callers (e.g., M-001, M-002) get the same verdict as before.
# Accepts both legacy (t/v/c) and new (temperature/velocity_rms/current) field names.
DEFAULT_ENVELOPE = {
    "type": "generic_legacy_fallback",
    "thresholds": {
        "temperature":  {"max_warning": 85, "max_danger": 90, "unit": "°C"},
        "velocity_rms": {"max_warning": 4.5, "max_danger": 6.0, "unit": "mm/s"},
        "velocity":     {"max_warning": 4.5, "max_danger": 6.0, "unit": "mm/s"},  # alias
        "current":      {"max_warning": 65, "max_danger": 75, "unit": "A"},
    },
    "source": "generic_default_envelope_legacy_thresholds",
}
