# IHI RAG Optimization Design

**Date:** 2026-06-03
**Status:** Draft (pending review)
**Author:** Brainstorming session
**Related:** `2026-05-31-ihi-rag-learning-design.md` (predecessor)

---

## 1. Background & Motivation

### Current state
- IHI (Industrial Health Index) monitors 3 device types: **Sensor-001** (vibration/temperature/humidity/battery), **PLC-001** (analog/digital I/O), **Meter-001** (3-phase electric).
- Scheduler polls each device every 30 min, calls local LLM (Gemma 4 E2B Q4) for verdict.
- Verdicts stored in `alert.db` (SQLite). `ihi_rag_cases` table (PG) has 51 cases but is **not used for retrieval today**.
- `IHIAnalyzer` (rule layer) checks only 3 thresholds: temperature > 90, vibration > 6.0, current > 75.
- LLM system prompt has **incorrect voltage imbalance threshold** (>10% = WARNING; NEMA says 2% / 5%).

### Observed problems (from 11 cycles self-evaluation 2026-06-03)
| Issue | Impact | Frequency |
|-------|--------|-----------|
| False negative: LLM says NORMAL when readings clearly abnormal (V23=0, battery 4%, AI1=1970V) | **CRITICAL** — hides real problems | 4/22 verdicts (~18%) |
| Hallucination: LLM outputs "ArrayList" / "CLASS-NORMAL" as verdict | Data corruption → fallback to NORMAL | 3/22 verdicts (~14%) |
| Inconsistency: same data pattern → different verdicts between phases | Operator can't trust verdict | 4 cycles inconsistent |
| LLM misses signals: battery 4% (only 1/4 cycles caught), PLC AI 1970V (1/3 caught) | Missed anomaly detection | Multiple cases |
| `_IHI_LLM_SYSTEM` prompt uses wrong voltage threshold (10% vs NEMA 2%/5%) | All voltage-related verdicts are off | Always |

### Goal
Build a **3-layer pipeline** that combines hard-coded standards (NEMA MG-1, ISO 10816-3, IEEE 1159, IEC 61000) with operator-set per-device overrides and RAG-retrieved historical context, so verdicts are:
- **More accurate** (machine-detectable rules catch what LLM misses)
- **More consistent** (same readings → same verdict regardless of phase)
- **Tunable per device** (operator can adjust for new vs old machines)
- **Grounded in standards** (NEMA/ISO references, not magic numbers)

### Reference implementation
Pattern adopted from `LGDiMaggio/predictive-maintenance-mcp` (https://github.com/LGDiMaggio/predictive-maintenance-mcp):
- ISO 10816-3 zone boundaries encoded as **Python dict constants** in a module, not YAML/JSON.
- Hybrid retrieval: pattern-match (exact) + vector similarity (semantic).
- Static `policy_fallback.json` for empty KB scenarios.

---

## 2. Architecture: 3-Layer Pipeline

```
┌─────────────────────────────────────────────────────────────┐
│  Layer 3: LLM verdict (Gemma 4 E2B Q4 on :8083)             │
│  - System prompt: NEMA + ISO + top-3 RAG cases               │
│  - Returns: DANGER / WARNING / NORMAL + narrative            │
│  - Used as tie-breaker / narrator                            │
└─────────────────────────────────────────────────────────────┘
                              ▲
                              │ (only if Layer 1 + 2 uncertain)
                              │
┌─────────────────────────────────────────────────────────────┐
│  Layer 2: RAG retrieval (PG ihi_rag_cases, k=3)             │
│  - Pattern-match (t/v/c + extended thresholds)               │
│  - Vector similarity (description embeddings)               │
│  - Returns: (case, confidence) tuple                        │
│  - Empty result = "uncertain" → escalate to Layer 3          │
└─────────────────────────────────────────────────────────────┘
                              ▲
                              │
┌─────────────────────────────────────────────────────────────┐
│  Layer 1: Rule pre-check (Python thresholds module)         │
│  - Hard thresholds: temp, vib, current, voltage_imbalance,  │
│    phase_loss, battery_low, ai_out_of_range                 │
│  - Returns: "DANGER" / "WARNING" / "uncertain"               │
│  - If hard rule fires → SKIP Layer 2 + 3, return immediately│
└─────────────────────────────────────────────────────────────┘
                              ▲
                              │
                          (readings)
```

### Fallback rules
- **Layer 1 fires (DANGER):** return immediately, skip RAG + LLM. Reasoning: machine-detectable rules are more reliable than LLM narrative.
- **Layer 1 fires (WARNING):** still escalate to Layer 2 for context, but bias toward WARNING.
- **Layer 1 "uncertain" (no rule violation):** escalate to Layer 2 (RAG). If confidence ≥ 0.7, use case verdict. Else Layer 3.
- **Layer 3 always runs last** as narrator + safety net.

### Trade-off vs. current
- **+30-50ms latency** for Layer 2 PG query (negligible vs LLM 1-3s).
- **+reduction in false negatives** (rule layer catches Phase loss, battery 4%, AI 1970V which LLM misses).
- **+consistency** (same readings → same verdict via deterministic rules).

---

## 3. Trust Hierarchy & Per-Device Overrides

### Insight from operator (2026-06-03)
- `pdm.tmainnovation.com/dashboard/data-center` is reference for device resources.
- **Manual Analyze in ihi-feed-v2.html = highest trust** (operator-confirmed readings).
- **Default thresholds (NEMA, ISO) = reference only.** Real machines vary: new machines have ideal readings, old machines have higher baseline.
- **Production depends on operator updates** — system must learn from operator input.

### Trust priority (highest first)

| Priority | Source | When used | Stored at |
|----------|--------|-----------|-----------|
| 🥇 1 | **Manual override** | Operator explicitly set for `device_id` | `ihi_device_overrides` (new table) |
| 🥈 2 | **Auto-learned from RAG match** | RAG case confidence > 0.85, `confirmed_by="auto_learned"` | `ihi_rag_cases` |
| 🥉 3 | **Default standard** (NEMA MG-1, ISO 10816) | No override + no RAG match | `app/services/thresholds/*.py` |

### New table: `ihi_device_overrides`

```sql
CREATE TABLE ihi_device_overrides (
    id              SERIAL PRIMARY KEY,
    device_id       VARCHAR(50) NOT NULL,
    measurement     VARCHAR(50) NOT NULL,     -- e.g. "temperature", "velocity", "battery_pct"
    min_value       REAL,                      -- NULL = unbounded
    max_value       REAL,                      -- NULL = unbounded
    severity        VARCHAR(20) NOT NULL,      -- DANGER / WARNING / NORMAL
    source          VARCHAR(50) NOT NULL,      -- "manual" / "auto_learned"
    set_by          VARCHAR(100),              -- operator name, "system", etc.
    note            TEXT,
    valid_from      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    valid_to        TIMESTAMP,                 -- NULL = permanent
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(device_id, measurement)
);
CREATE INDEX idx_overrides_device ON ihi_device_overrides(device_id) WHERE valid_to IS NULL;
```

### New API endpoints

| Endpoint | Purpose |
|----------|---------|
| `GET /v1/ihi/devices/{device_id}/thresholds` | List effective thresholds (override + default merged) |
| `POST /v1/ihi/devices/{device_id}/thresholds` | Set manual override — body: `{measurement, min, max, severity, note}` |
| `DELETE /v1/ihi/devices/{device_id}/thresholds/{measurement}` | Clear override (revert to default) |
| `GET /v1/ihi/devices/{device_id}/thresholds/history` | Audit log: who changed what when |

### Schema extension to `PatternRange` (backward-compat)

```python
class PatternRange(BaseModel):
    # Existing 3-parameter pattern (unchanged)
    t_min: float = 0.0
    t_max: float = 0.0
    v_min: float = 0.0
    v_max: float = 0.0
    c_min: float = 0.0
    c_max: float = 0.0
    # NEW: flexible threshold dict
    extra: dict[str, dict] = Field(
        default_factory=dict,
        description="Extended thresholds: {measurement_name: {min_value, max_value, severity}}",
    )
```

Existing 51 cases with old format still work (default `extra={}`).

### `ihi_rag_cases.confirmed_by` semantic (no schema change)
- `"manager"` / `"operator_{name}"` → manual, high trust (+0.2 confidence boost)
- `"system_seed"` → from standards, default trust (+0.1)
- `"auto_learned"` → derived from RAG case history, low trust (+0.05)

### ihi-feed-v2.html UI additions
- **New tab: "Device Thresholds"** — per device_id view of effective thresholds (override + default merged), with source label.
- **"Edit threshold" button** — opens form to set manual override.
- **"Reset to default" button** — clears override.
- **Manual Analyze extension** — new checkbox "Treat as new baseline for this device" → POST with `override_thresholds: true` writes to `ihi_device_overrides`.

---

## 4. Thresholds Module

### File structure
```
app/services/thresholds/
├── __init__.py
├── types.py             # Threshold, Violation dataclasses
├── iso_10816.py         # ISO 10816-3 vibration zones
├── nema_mg1.py           # NEMA MG-1 motor limits + voltage imbalance
├── iec_61000.py          # IEC 61000-2-4 power quality THD/harmonics
├── ieee_1159.py          # IEEE 1159 power quality phenomena
├── sensor_envelopes.py   # Per-device normal envelopes (Sensor-001/PLC-001/Meter-001)
└── loader.py             # get_effective_threshold() with trust hierarchy
```

### `types.py` — shared dataclasses

```python
@dataclass(frozen=True)
class Threshold:
    measurement: str
    min_value: float | None
    max_value: float | None
    severity: str       # "NORMAL" / "WARNING" / "DANGER"
    unit: str
    source: str         # "manual" | "auto_learned" | "ISO 10816" | "NEMA MG-1" | ...
    standard_ref: str | None = None
    note: str | None = None

    def evaluate(self, value: float) -> str:
        """Return severity if violated, else 'NORMAL'."""
        if self.min_value is not None and value < self.min_value: return self.severity
        if self.max_value is not None and value > self.max_value: return self.severity
        return "NORMAL"

@dataclass(frozen=True)
class ThresholdViolation:
    device_id: str
    measurement: str
    value: float
    threshold: Threshold
    severity: str       # "WARNING" | "DANGER"
```

### `iso_10816.py` — vibration severity zones

```python
ISO_10816_ZONES = {
    # Class II rigid — most common (15-300 kW motors)
    ("II", "rigid"): {
        "A": (0.0,  1.4,  "NORMAL",  "Mới, rung rất tốt", "green"),
        "B": (1.4,  2.8,  "NORMAL",  "Chấp nhận lâu dài", "yellow"),
        "C": (2.8,  4.5,  "WARNING", "Lên kế hoạch bảo trì", "orange"),
        "D": (4.5,  float("inf"), "DANGER", "Nguy hại, hành động ngay", "red"),
    },
    ("II", "flexible"): {
        "A": (0.0, 2.3, "NORMAL", ...),
        "B": (2.3, 4.5, "NORMAL", ...),
        "C": (4.5, 7.1, "WARNING", ...),
        "D": (7.1, float("inf"), "DANGER", ...),
    },
    # Class I, III, IV similarly
}
DEFAULT_ISO_CLASS = ("II", "rigid")
```

### `nema_mg1.py` — motor standards

```python
# NEMA MG-1 Part 14 voltage imbalance (CRITICAL FIX: was 10% in old prompt)
NEMA_VOLTAGE_IMBALANCE = {
    "warning_pct": 2.0,        # 2% = warning (was 10%)
    "danger_pct": 5.0,         # 5% = critical (was missing)
    "consequence": "3% imbalance → ~25% winding temp rise; 2% halves motor life",
    "source": "NEMA MG-1 Part 14",
}

# NEMA MG-1 temperature rise (bearing housing, 40°C ambient)
NEMA_TEMP_RISE = {
    "A": {"ref_c": 105, "rise_sf1": 60,  "rise_sf115": 75},
    "B": {"ref_c": 130, "rise_sf1": 80,  "rise_sf115": 90},   # matches current 90°C
    "F": {"ref_c": 155, "rise_sf1": 105, "rise_sf115": 115},  # most industrial
    "H": {"ref_c": 180, "rise_sf1": 125, "rise_sf115": 140},
}
```

### `sensor_envelopes.py` — per-device defaults

```python
SENSOR_ENVELOPES = {
    "Sensor-001": {
        "type": "wireless_vibration_temp_humidity_battery",
        "default_class": ("II", "rigid"),
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
            "AI1_voltage":     {"min_normal": 0, "max_normal": 10, "unit": "V"},
            "AI1_ma_equiv":    {"min_normal": 4, "max_normal": 20, "unit": "mA"},
            "AI1_below_3p6ma": {"max": 3.6, "severity": "DANGER", "note": "Below 4-20mA zero = broken sensor"},
            "AI1_above_21ma":  {"min": 21, "severity": "DANGER", "note": "Above 20mA range = broken sensor"},
            "DI_change_rate":  {"max_per_minute": 5, "severity": "WARNING", "note": "Rapid DI changes = instability"},
        },
        "source": "Standard 4-20mA instrumentation (Honeywell/Yokogawa/ABB convention)",
    },
    "Meter-001": {
        "type": "3_phase_electric",
        "thresholds": {
            "v_imbalance_pct":  {"max_warning": 2.0, "max_danger": 5.0, "unit": "%"},   # NEMA FIX
            "f_hz":             {"min": 49.0, "max": 51.0, "unit": "Hz"},
            "v_min":            {"min_warning": 207, "min_danger": 195, "unit": "V"},
            "v_max":            {"max_warning": 233, "max_danger": 245, "unit": "V"},
            "i_imbalance_pct":  {"max_warning": 10, "max_danger": 25, "unit": "%"},
            "power_factor":     {"min": 0.7, "severity": "WARNING"},
            "phase_loss":       {"min_current_a": 0.5, "other_phase_min_a": 5, "severity": "DANGER"},
            "all_phases_zero":  {"max_total": 0.5, "severity": "DANGER", "note": "Machine off OR all 3 phases lost"},
        },
        "source": "NEMA MG-1 Part 14, IEEE 1159, IEC 61000-2-4",
    },
}
```

### `loader.py` — unified access with trust hierarchy

```python
def get_effective_threshold(device_id: str, measurement: str) -> Threshold | None:
    """Trust 1 (manual override) > Trust 3 (default)."""
    override = ihi_overrides_service.get_active_override(device_id, measurement)
    if override:
        return Threshold.from_override(override)
    envelope = SENSOR_ENVELOPES.get(device_id)
    if envelope and measurement in envelope["thresholds"]:
        return Threshold.from_default(envelope["thresholds"][measurement], source=envelope["source"])
    return None


def evaluate_all_thresholds(device_id: str, readings: dict) -> list[ThresholdViolation]:
    violations = []
    for measurement, value in readings.items():
        if value is None: continue
        threshold = get_effective_threshold(device_id, measurement)
        if threshold is None: continue
        severity = threshold.evaluate(value)
        if severity in ("WARNING", "DANGER"):
            violations.append(ThresholdViolation(device_id, measurement, value, threshold, severity))
    return violations
```

---

## 5. Rule Pre-Check Integration (Layer 1)

### Pipeline

```
Input: (device_id, readings: dict)

  evaluate_all_thresholds(device_id, readings)
       │
       ▼
  Has DANGER violation?
     YES → return {alert: DANGER, source: "rule_override|rule_default", violations}
     NO  ↓
  Has WARNING OR no clear verdict?
     YES → Layer 2 (RAG)
     NO  → return {alert: NORMAL, source: "rule"}
```

### Integration with `/v1/ihi/analyze`

**Request (extended):**
```python
class AnalyzeRequest(BaseModel):
    ts: str
    data: list[SensorReading]
    extra: dict[str, dict] = {}             # NEW: any extra measurements per device
    override_thresholds: bool = False       # NEW: treat as new baseline
    note: str = ""                          # NEW: operator note
```

**Response (extended):**
```python
class AnalyzeResponse(BaseModel):
    alert: AlertLevel
    devices: list[str]
    case_id: str | None
    confidence: float
    symptom: str | None
    violations: list[ThresholdViolation] = []     # NEW
    source_layer: str = "unknown"                  # NEW: "rule_override" | "rule_default" | "rag" | "llm"
    narrative: str = ""                            # NEW: LLM explanation
```

### `override_thresholds: bool` handling

When `True`:
1. Write each measurement in `extra` to `ihi_device_overrides`:
   - `min_value` / `max_value` = submitted value (treating as new boundary)
   - `severity` = returned alert
   - `source` = `"manual"`
   - `set_by` = API key name
   - `note` = payload.note
2. Log: "Operator {name} set override for {device}.{measurement} = {value} (severity: {alert})"

### IHIAnalyzer migration

- `app/services/ihi_analyzer.py` deprecated but kept for backward compat.
- Old logic (t/v/c only) wrapped in `LegacyIHIAnalyzer` class.
- New code uses `IHIThresholdAnalyzer` (via `loader.py`).
- `analyze_batch()` calls redirect to new analyzer when `extra` readings present.

---

## 6. RAG Retrieval + Case Saving (Layer 2)

### Components

```
┌──────────────────────────────────────────────────────────────┐
│ Layer 2: IHIragService (extended)                            │
│                                                               │
│  ┌─────────────────┐  ┌─────────────────┐  ┌──────────────┐│
│  │ PatternMatcher  │  │ VectorIndex     │  │ CaseSaver    ││
│  │ (t/v/c + extra) │  │ (FastEmbed +    │  │ (background) ││
│  │                 │  │  cosine sim)    │  │              ││
│  └─────────────────┘  └─────────────────┘  └──────────────┘│
│         ▲                     ▲                    ▲          │
│  load_cases()          retrieve_top_k()      save_verdict()  │
└─────────┼─────────────────────┼────────────────────┼──────────┘
          ▼                     ▼                    ▼
   ┌────────────────────────────────────────────────────────┐
   │ PG ihi_rag_cases (51 existing + new auto-learned)      │
   │ + ihi_case_embeddings (pgvector)                       │
   └────────────────────────────────────────────────────────┘
```

### `PatternMatcher` (extended)

```python
def matches(self, pattern: dict, reading: dict) -> bool:
    # Existing t/v/c check (backward-compat)
    for field in ("t", "v", "c"):
        ...
    # NEW: check extra thresholds
    extra = pattern.get("extra", {})
    for measurement, bounds in extra.items():
        value = reading.get(measurement)
        if value is None: continue
        if "min_value" in bounds and value < bounds["min_value"]: return False
        if "max_value" in bounds and value > bounds["max_value"]: return False
    return True
```

### `ihi_case_embeddings` (new table)

```sql
CREATE TABLE ihi_case_embeddings (
    case_id INTEGER PRIMARY KEY REFERENCES ihi_rag_cases(id) ON DELETE CASCADE,
    embedding vector(384),  -- paraphrase-multilingual-MiniLM-L12-v2
    model_version VARCHAR(50) DEFAULT 'paraphrase-multilingual-MiniLM-L12-v2',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX ihi_case_embeddings_ivfflat
ON ihi_case_embeddings USING ivfflat (embedding vector_cosine_ops)
WITH (lists = 10);
```

**Note:** Requires `pgvector` extension. Fallback: in-memory FAISS index rebuild on case load (slower but works).

### Hybrid retrieval

```python
def retrieve_top_k(self, readings: dict, k: int = 3) -> list[tuple[dict, float]]:
    candidates = {}
    # 1. Pattern-match (weight 0.7, high precision)
    for case in self._case_cache:
        if self.matcher.matches(case["pattern"], readings):
            candidates[case["id"]] = (case, 0.7)
    # 2. Vector similarity (weight 0.3, recall)
    query_text = self._readings_to_text(readings)
    query_embedding = embedder.embed(query_text)
    for case_id, sim in vector_search(query_embedding, top_n=k*2):
        if case_id in candidates:
            old_case, old_score = candidates[case_id]
            candidates[case_id] = (old_case, min(1.0, old_score + 0.3 * sim))
        else:
            case = self._case_map.get(case_id)
            if case: candidates[case_id] = (case, 0.3 * sim)
    sorted_cases = sorted(candidates.values(), key=lambda x: x[1], reverse=True)
    return [(case, score) for case, score in sorted_cases[:k]]
```

### Confidence scoring (extended)

```python
def _calculate_confidence(self, pattern, reading, symptom, case) -> float:
    confidence = 0.5
    # Trust source boost (NEW)
    confirmed_by = case.get("confirmed_by", "")
    if confirmed_by.startswith("operator_") or confirmed_by == "manager":
        confidence += 0.2  # manual
    elif confirmed_by == "system_seed":
        confidence += 0.1  # default
    elif confirmed_by == "auto_learned":
        confidence += 0.05
    # Severity boost
    severity = case.get("severity", "medium").lower()
    if severity == "critical": confidence += 0.2
    elif severity == "warning": confidence += 0.1
    # Match count boost
    match_count = case.get("match_count", 0)
    if match_count > 10: confidence += 0.15
    elif match_count > 5: confidence += 0.1
    elif match_count > 0: confidence += 0.05
    # Symptom alignment
    if case.get("symptom") == symptom: confidence += 0.1
    return min(confidence, 1.0)
```

### LLM context injection

```python
def _format_rag_context(cases: list[tuple[dict, float]]) -> str:
    if not cases: return "Không có case tương tự trong knowledge base."
    parts = ["Các case tương tự từ knowledge base:"]
    for i, (case, score) in enumerate(cases, 1):
        parts.append(f"""
[{i}] case_id={case['id']} severity={case['severity']} symptom={case['symptom']} (match: {score:.2f})
    Pattern: {case['pattern']}
    Mô tả: {case['description']}
    Resolution: {case.get('resolution', 'N/A')}
    Nguồn: {case.get('confirmed_by', 'system_seed')}
""")
    return "\n".join(parts)
```

### Updated `_IHI_LLM_SYSTEM` (with NEMA FIX)

```python
_IHI_LLM_SYSTEM = """Bạn là chuyên gia giám sát tình trạng máy công nghiệp...

Ngưỡng tham khảo (đã cập nhật theo NEMA MG-1, ISO 10816-3):
- Temperature: >90°C DANGER, 80-90°C WARNING (NEMA Class B SF=1.15)
- Velocity (Class II rigid): >4.5 mm/s DANGER, 2.8-4.5 WARNING (ISO 10816-3)
- Velocity (Class II flexible): >7.1 mm/s DANGER, 4.5-7.1 WARNING
- Current: >75A DANGER, 65-75A WARNING
- Voltage imbalance: >5% DANGER, 2-5% WARNING (NEMA MG-1 Part 14 — KHÔNG dùng 10% cũ)
- Battery sensor: <10% DANGER, 10-20% WARNING
- PLC AI range: 0-10V (hoặc 4-20mA); ngoài range = DANGER (broken sensor)
- Phase loss: 1 phase <0.5A trong khi 2 phase >5A = DANGER
- All phases near 0A: DANGER (machine off hoặc mất toàn bộ pha)
- Power factor: <0.7 WARNING
- DI đột ngột đổi trạng thái: cảnh báo

Lưu ý: Mỗi device có thể có manual override (set bởi operator). Override luôn ưu tiên cao nhất.

{rag_context}  ← injected dynamically

Trả lời ngắn gọn bằng tiếng Việt, format:
**Verdict:** NORMAL / WARNING / DANGER
**Giải thích:** ...
**Khuyến nghị:** ..."""
```

### CaseSaver (background)

```python
class IHICaseSaver:
    """Save LLM verdicts as RAG cases for future retrieval."""

    async def save_verdict(self, scrape_id: int, phase: int, sample_time: str,
                           readings: dict, llm_result: AnalyzeResponse) -> int | None:
        # Skip if NORMAL or low confidence
        if llm_result.alert == AlertLevel.NORMAL: return None
        if llm_result.confidence < 0.5: return None

        # Build pattern
        pattern = {
            "t_min": 0, "t_max": 100, "v_min": 0, "v_max": 10, "c_min": 0, "c_max": 100,
            "extra": {
                m: {"min_value": v * 0.95, "max_value": v * 1.05}
                for m, v in readings.items()
                if m in ("battery_pct", "v_imbalance_pct", "AI1_voltage")
            }
        }
        case_id = self.rag.create_case(
            device_id=f"scrape_{scrape_id}_p{phase}",
            severity=llm_result.alert.value.lower(),
            symptom=llm_result.symptom or "auto_detected",
            pattern=pattern,
            description=llm_result.narrative or "(auto from LLM verdict)",
            confirmed_by="auto_learned",
        )
        self.rag.add_case_to_vector_index(case_id)
        return case_id
```

### Backward compat
- 51 existing cases: descriptions re-embedded on first `rebuild_vector_index()`.
- `PatternMatcher` extended without breaking old patterns (no `extra` field).
- `find_matching_case()` signature unchanged; new `retrieve_top_k()` is additive.

---

## 7. Test Strategy & Ground Truth

### 3 test layers

**1. Unit tests** — per module, no I/O
- `test_thresholds_iso_10816.py` (zone boundaries)
- `test_thresholds_nema_mg1.py` (voltage imbalance 2%/5%)
- `test_thresholds_sensor_envelopes.py` (per-device defaults)
- `test_loader.py` (override > default priority)
- `test_pattern_matcher_extended.py` (backward compat + extra)
- `test_case_saver.py` (save → retrieve round-trip)

**2. Integration tests** — pipeline end-to-end, no LLM
- `test_analyze_with_rag.py`
- `test_override_flow.py`
- `test_trust_hierarchy.py`
- `test_rag_retrieval.py`
- `test_llm_prompt_with_rag.py` (verify prompt contains retrieved cases)

**3. Ground truth tests** — 50-70 labeled cases, run full pipeline with LLM
- `test_ground_truth_ihi.py`
- Pass criteria: ≥85% verdict match, ≤5% false negative rate
- Each case: (readings, expected_alert, expected_source_layer, expected_violations)

### Ground truth generation

**Input:** 11 cycles in `alert.db` (22 verdicts), `sensors.db` (1GB raw).

**Process:**
1. **Heuristic labeling** for clear cases (see code in `scripts/generate_ground_truth.py`).
2. **Cross-check with LLM verdicts** — flag disagreements.
3. **Manual review queue** — disagreements → `ground_truth_review.jsonl`.
4. **Operator labels disagreements** → `ground_truth_final.jsonl`.

**Heuristics:**
- `v_imbalance_pct > 5%` → DANGER
- `all_phases_zero = true` → DANGER
- `battery_pct < 5%` → DANGER
- `AI1_voltage > 100` → DANGER (broken sensor, impossible for 0-10V)
- `temperature > 90` → DANGER
- `velocity_rms > 4.5` → DANGER (ISO 10816 Class II rigid)
- All safe → NORMAL

### Pass criteria

| Metric | Target |
|--------|--------|
| Verdict accuracy | ≥85% |
| False negative rate | ≤5% |
| False positive rate | ≤15% |
| Source layer accuracy | ≥80% |
| Override priority | 100% |

### Test execution

```bash
# Unit (fast, no LLM)
./venv/bin/pytest tests/unit/ -v

# Integration (no LLM)
./venv/bin/pytest tests/integration/ -v -k "not test_llm"

# Ground truth (LLM required)
./venv/bin/pytest tests/ground_truth/ -v --no-cov

# All
./venv/bin/pytest tests/ -v
```

---

## 8. Files Affected

### New files
- `app/services/thresholds/__init__.py`
- `app/services/thresholds/types.py`
- `app/services/thresholds/iso_10816.py`
- `app/services/thresholds/nema_mg1.py`
- `app/services/thresholds/iec_61000.py`
- `app/services/thresholds/ieee_1159.py`
- `app/services/thresholds/sensor_envelopes.py`
- `app/services/thresholds/loader.py`
- `app/services/ihi_overrides_service.py` (PG CRUD for `ihi_device_overrides`)
- `app/services/ihi_case_saver.py`
- `scripts/generate_ground_truth.py`
- `scripts/seed_ihi_rag_v2.py` (10 new cases for 4 new symptoms)
- `tests/unit/test_thresholds_*.py` (6 files)
- `tests/unit/test_threshold_loader.py`
- `tests/unit/test_pattern_matcher_extended.py`
- `tests/unit/test_case_saver.py`
- `tests/integration/test_analyze_with_rag.py`
- `tests/integration/test_override_flow.py`
- `tests/integration/test_trust_hierarchy.py`
- `tests/integration/test_rag_retrieval.py`
- `tests/integration/test_llm_prompt_with_rag.py`
- `tests/ground_truth/test_ground_truth_ihi.py`
- `tests/ground_truth/ground_truth_v1.jsonl`

### Modified files
- `app/models/ihi.py` (extend `PatternRange` with `extra` field)
- `app/services/ihi_analyzer.py` (deprecated; legacy wrapper)
- `app/services/ihi_rag_service.py` (PatternMatcher extended, new `retrieve_top_k`, vector index)
- `app/routes/ihi.py` (new `analyze` pipeline, updated `_IHI_LLM_SYSTEM`, new endpoints)
- `app/core/database.py` (new tables: `ihi_device_overrides`, `ihi_case_embeddings`)
- `static/ihi-feed-v2.html` (Device Thresholds tab, override form, baseline checkbox)

### Database migrations
- Add `ihi_device_overrides` table
- Add `ihi_case_embeddings` table
- Add `pgvector` extension (if not present)

---

## 9. Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| `pgvector` extension not installed | Medium | High | Fallback: in-memory FAISS index, slower but works |
| LLM still hallucinates despite updated prompt | Medium | Medium | Strict post-validate (regex match `**Verdict:** NORMAL\|WARNING\|DANGER`); fallback to WARNING if parse fails |
| Pattern schema change breaks old cases | Low | Medium | Backward compat via `extra` default `{}`; old cases still work |
| Override table grows unbounded | Low | Low | Add `valid_to` timestamp for temporary overrides; periodic cleanup job |
| `pgvector` index slow on large case library | Low | Low | IVFFlat index, lists=10, balance speed/recall |
| Vector embedding model changes (FastEmbed upgrade) | Low | Medium | Store `model_version` per embedding; re-embed on mismatch |

---

## 10. Success Criteria (Definition of Done)

- [ ] All 6 sections implemented and merged.
- [ ] Unit test pass rate: 100%.
- [ ] Integration test pass rate: 100% (excluding LLM-dependent tests).
- [ ] Ground truth test: ≥85% verdict accuracy, ≤5% false negative rate.
- [ ] `/v1/ihi/analyze` returns extended response (`source_layer`, `violations`, `narrative`).
- [ ] `/v1/ihi/devices/{device_id}/thresholds` CRUD endpoints working.
- [ ] ihi-feed-v2.html shows Device Thresholds tab with override UI.
- [ ] `_IHI_LLM_SYSTEM` updated to NEMA MG-1 / ISO 10816-3 standards.
- [ ] Manual Analyze with `override_thresholds=true` writes to `ihi_device_overrides`.
- [ ] LLM verdicts auto-saved as RAG cases (`confirmed_by="auto_learned"`).
- [ ] Existing 51 RAG cases still work (no migration needed).
- [ ] 11 existing cycles re-evaluated; false negative count drops from 4/22 to ≤1/22.

---

## 11. References

**Repos (best patterns):**
- https://github.com/LGDiMaggio/predictive-maintenance-mcp — gold standard
- https://github.com/IBM/AssetOpsBench
- https://github.com/intel/predictive-maintenance-pipeline
- https://github.com/thingsboard/thingsboard
- https://github.com/emqx/neuron

**Standards:**
- ISO 10816-3:2009 — mechanical vibration severity
- NEMA MG-1 Part 14 — motor voltage imbalance, temperature rise
- IEEE 1159 — power quality monitoring
- IEC 61000-2-2 / 61000-2-4 — voltage quality compatibility

**RAG literature:**
- https://www.mdpi.com/2079-9292/14/16/3284 (IHGR-RAG)
- https://arxiv.org/html/2601.05266v1

**Internal context:**
- `docs/superpowers/specs/2026-05-31-ihi-rag-learning-design.md` (predecessor)
- `app/services/ihi_rag_service.py` (existing RAG service)
- `app/services/ihi_analyzer.py` (existing rule layer)
- `static/ihi-alert-feed.html`, `static/ihi-feed-v2.html` (current UIs)
- `app/routes/ihi.py` (current endpoints, includes `_IHI_LLM_SYSTEM` at L377)
- `/home/hung/ihi_test/alert.db` (11 cycles, 22 verdicts)
- `/home/hung/ihi_test/sensors.db` (1GB raw readings)
- `https://pdm.tmainnovation.com/dashboard/data-center` (external device reference)
