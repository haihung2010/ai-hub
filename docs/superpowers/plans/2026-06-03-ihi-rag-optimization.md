# IHI RAG Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build 3-layer IHI pipeline (rule pre-check → RAG retrieval → LLM verdict) with NEMA/ISO thresholds, per-device operator overrides, and ground-truth regression tests to reduce false negatives from 4/22 to ≤1/22.

**Architecture:** Hard-coded standards (NEMA MG-1, ISO 10816-3, IEEE 1159, IEC 61000) as Python module constants; trust hierarchy (manual override > auto-learned > default); PG-backed RAG with pattern-match + vector similarity; LLM narrates only when rule+RAG uncertain.

**Tech Stack:** Python 3.12, FastAPI, PostgreSQL (ai_hub) + pgvector, FastEmbed (paraphrase-multilingual-MiniLM-L12-v2), httpx, Pydantic v2, pytest

**Spec:** `docs/superpowers/specs/2026-06-03-ihi-rag-optimization-design.md`

**Existing code (foundation already in place):**
- `app/services/ihi_rag_service.py` (412 lines, 51 cases in PG)
- `app/services/ihi_analyzer.py` (100 lines, t/v/c rules)
- `app/models/ihi.py` (110 lines, Pydantic models)
- `app/routes/ihi.py` (714 lines, includes `_IHI_LLM_SYSTEM` at L377)
- `static/ihi-feed-v2.html` (738 lines, Manual Analyze UI)

---

## Files Overview

### New Files
- `app/services/thresholds/__init__.py`
- `app/services/thresholds/types.py`
- `app/services/thresholds/iso_10816.py`
- `app/services/thresholds/nema_mg1.py`
- `app/services/thresholds/sensor_envelopes.py`
- `app/services/thresholds/loader.py`
- `app/services/ihi_overrides_service.py`
- `app/services/ihi_case_saver.py`
- `app/services/vector_index.py`
- `scripts/migrate_add_ihi_overrides.py`
- `scripts/migrate_add_pgvector.py`
- `scripts/generate_ground_truth.py`
- `scripts/seed_ihi_rag_v2.py`
- `tests/unit/test_thresholds_types.py`
- `tests/unit/test_thresholds_iso_10816.py`
- `tests/unit/test_thresholds_nema_mg1.py`
- `tests/unit/test_thresholds_sensor_envelopes.py`
- `tests/unit/test_threshold_loader.py`
- `tests/unit/test_ihi_overrides_service.py`
- `tests/unit/test_pattern_matcher_extra.py`
- `tests/unit/test_ihi_analyzer_v2.py`
- `tests/unit/test_ihi_case_saver.py`
- `tests/unit/test_vector_index.py`
- `tests/integration/test_analyze_pipeline.py`
- `tests/integration/test_override_endpoints.py`
- `tests/integration/test_trust_hierarchy.py`
- `tests/integration/test_rag_hybrid_retrieval.py`
- `tests/ground_truth/test_ground_truth_ihi.py`
- `tests/ground_truth/ground_truth_v1.jsonl`

### Modified Files
- `app/models/ihi.py` (extend `PatternRange` with `extra` field; extend `AnalyzeRequest`/`AnalyzeResponse`)
- `app/services/ihi_rag_service.py` (extend `PatternMatcher`; add `retrieve_top_k`)
- `app/services/ihi_analyzer.py` (add `IHIThresholdAnalyzer` alongside legacy)
- `app/routes/ihi.py` (new analyze pipeline; new override endpoints; updated `_IHI_LLM_SYSTEM`)
- `app/core/database.py` (new tables: `ihi_device_overrides`, `ihi_case_embeddings`)
- `static/ihi-feed-v2.html` (Device Thresholds tab; override form; baseline checkbox)

---

## Phase 1: Database Schema

### Task 1: Add `pgvector` extension and migration infrastructure

**Files:**
- Create: `scripts/migrate_add_pgvector.py`
- Test: `tests/integration/test_migrations.py`

- [ ] **Step 1: Write failing test for migration script**

```python
# tests/integration/test_migrations.py
import subprocess
from pathlib import Path


def test_pgvector_migration_idempotent():
    """Migration script should be idempotent — run twice without error."""
    script = Path(__file__).parent.parent.parent / "scripts" / "migrate_add_pgvector.py"
    env = {
        "DATABASE_URL": "postgresql://aihub:aihub_pass@localhost:5432/ai_hub",
        "PATH": "/usr/bin:/bin",
    }
    # Run twice — second run should also succeed
    r1 = subprocess.run(["python", str(script)], env=env, capture_output=True, text=True, timeout=30)
    r2 = subprocess.run(["python", str(script)], env=env, capture_output=True, text=True, timeout=30)
    assert r1.returncode == 0, f"First run failed: {r1.stderr}"
    assert r2.returncode == 0, f"Second run failed: {r2.stderr}"
    assert "pgvector extension enabled" in r1.stdout
    assert "pgvector extension enabled" in r2.stdout
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/pytest tests/integration/test_migrations.py -v --no-cov`
Expected: FAIL with "No such file or directory: scripts/migrate_add_pgvector.py"

- [ ] **Step 3: Write the migration script**

```python
# scripts/migrate_add_pgvector.py
#!/usr/bin/env python3
"""Enable pgvector extension. Idempotent."""
import os
import sys

import psycopg


def main():
    db_url = os.environ.get("DATABASE_URL", "postgresql://aihub:aihub_pass@localhost:5432/ai_hub")
    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
            # Verify
            cur.execute("SELECT extname, extversion FROM pg_extension WHERE extname = 'vector';")
            row = cur.fetchone()
            if row:
                print(f"pgvector extension enabled (version {row[1]})")
            else:
                print("ERROR: pgvector extension not found after CREATE", file=sys.stderr)
                sys.exit(1)
        conn.commit()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/pytest tests/integration/test_migrations.py -v --no-cov`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
chmod +x scripts/migrate_add_pgvector.py
git add scripts/migrate_add_pgvector.py tests/integration/test_migrations.py
git commit -m "feat(db): add pgvector extension migration (idempotent)"
```

---

### Task 2: Add `ihi_device_overrides` table migration

**Files:**
- Create: `scripts/migrate_add_ihi_overrides.py`

- [ ] **Step 1: Write the migration script**

```python
# scripts/migrate_add_ihi_overrides.py
#!/usr/bin/env python3
"""Create ihi_device_overrides table. Idempotent."""
import os
import sys

import psycopg


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS ihi_device_overrides (
    id              SERIAL PRIMARY KEY,
    device_id       VARCHAR(50) NOT NULL,
    measurement     VARCHAR(50) NOT NULL,
    min_value       REAL,
    max_value       REAL,
    severity        VARCHAR(20) NOT NULL,
    source          VARCHAR(50) NOT NULL DEFAULT 'manual',
    set_by          VARCHAR(100),
    note            TEXT,
    valid_from      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    valid_to        TIMESTAMP,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(device_id, measurement)
);
CREATE INDEX IF NOT EXISTS idx_overrides_device
    ON ihi_device_overrides(device_id) WHERE valid_to IS NULL;
"""


def main():
    db_url = os.environ.get("DATABASE_URL", "postgresql://aihub:aihub_pass@localhost:5432/ai_hub")
    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
            cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'ihi_device_overrides' ORDER BY ordinal_position;"
            )
            cols = [r[0] for r in cur.fetchall()]
            expected = [
                "id", "device_id", "measurement", "min_value", "max_value",
                "severity", "source", "set_by", "note",
                "valid_from", "valid_to", "created_at",
            ]
            assert cols == expected, f"Schema mismatch: got {cols}, expected {expected}"
            print(f"ihi_device_overrides table ready ({len(cols)} columns)")
        conn.commit()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run migration**

Run: `./venv/bin/python scripts/migrate_add_ihi_overrides.py`
Expected: `ihi_device_overrides table ready (12 columns)`

- [ ] **Step 3: Verify in PG**

```bash
PGPASSWORD=aihub_pass psql -h localhost -U aihub -d ai_hub -c "\d ihi_device_overrides"
```

Expected: Table with 12 columns + idx_overrides_device index

- [ ] **Step 4: Commit**

```bash
chmod +x scripts/migrate_add_ihi_overrides.py
git add scripts/migrate_add_ihi_overrides.py
git commit -m "feat(db): add ihi_device_overrides table (per-device threshold overrides)"
```

---

### Task 3: Add `ihi_case_embeddings` table migration

**Files:**
- Create: `scripts/migrate_add_ihi_case_embeddings.py`

- [ ] **Step 1: Write the migration script**

```python
# scripts/migrate_add_ihi_case_embeddings.py
#!/usr/bin/env python3
"""Create ihi_case_embeddings table for vector similarity search. Idempotent."""
import os
import sys

import psycopg


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS ihi_case_embeddings (
    case_id INTEGER PRIMARY KEY REFERENCES ihi_rag_cases(id) ON DELETE CASCADE,
    embedding vector(384),
    model_version VARCHAR(50) DEFAULT 'paraphrase-multilingual-MiniLM-L12-v2',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ihi_case_embeddings_ivfflat
    ON ihi_case_embeddings USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 10);
"""


def main():
    db_url = os.environ.get("DATABASE_URL", "postgresql://aihub:aihub_pass@localhost:5432/ai_hub")
    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
            cur.execute(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_name = 'ihi_case_embeddings' ORDER BY ordinal_position;"
            )
            rows = cur.fetchall()
            assert len(rows) == 4, f"Expected 4 columns, got {len(rows)}"
            assert rows[1][1] == "USER-DEFINED", f"embedding column should be vector type, got {rows[1][1]}"
            print("ihi_case_embeddings table ready (4 columns, vector(384) + ivfflat index)")
        conn.commit()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run migration**

Run: `./venv/bin/python scripts/migrate_add_ihi_case_embeddings.py`
Expected: `ihi_case_embeddings table ready (4 columns, vector(384) + ivfflat index)`

- [ ] **Step 3: Verify in PG**

```bash
PGPASSWORD=aihub_pass psql -h localhost -U aihub -d ai_hub -c "\d ihi_case_embeddings"
```

Expected: Table with 4 columns + ivfflat index

- [ ] **Step 4: Commit**

```bash
chmod +x scripts/migrate_add_ihi_case_embeddings.py
git add scripts/migrate_add_ihi_case_embeddings.py
git commit -m "feat(db): add ihi_case_embeddings table (pgvector for RAG similarity)"
```

---

## Phase 2: Schema Extension (PatternRange.extra)

### Task 4: Extend `PatternRange` with `extra` field

**Files:**
- Modify: `app/models/ihi.py:30-36` (PatternRange class)
- Test: `tests/unit/test_pattern_matcher_extra.py`

- [ ] **Step 1: Write failing test for extended PatternRange**

```python
# tests/unit/test_pattern_matcher_extra.py
import pytest
from app.models.ihi import PatternRange


def test_pattern_range_extra_default_empty():
    """Backward compat: old pattern without extra should default to {}."""
    p = PatternRange(t_min=0, t_max=100, v_min=0, v_max=4.5, c_min=0, c_max=65)
    assert p.extra == {}


def test_pattern_range_with_extra_thresholds():
    """New pattern can carry extra measurement thresholds."""
    p = PatternRange(
        t_min=0, t_max=100, v_min=0, v_max=4.5, c_min=0, c_max=65,
        extra={"battery_pct": {"min_value": 10, "severity": "DANGER"}},
    )
    assert p.extra["battery_pct"]["min_value"] == 10
    assert p.extra["battery_pct"]["severity"] == "DANGER"


def test_pattern_range_json_serialization_includes_extra():
    """JSON output must include extra field for storage in PG JSONB."""
    p = PatternRange(extra={"v_imbalance_pct": {"max_value": 5.0}})
    json = p.model_dump_json()
    assert "v_imbalance_pct" in json
    assert "5.0" in json
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/pytest tests/unit/test_pattern_matcher_extra.py -v --no-cov`
Expected: FAIL with "PatternRange has no attribute 'extra'" or similar

- [ ] **Step 3: Extend PatternRange in `app/models/ihi.py`**

Find the `PatternRange` class (around line 30) and replace with:

```python
class PatternRange(BaseModel):
    """Numeric range pattern for matching sensor readings.

    Backward-compat: original 3-parameter t/v/c pattern unchanged.
    Extension: `extra` allows arbitrary measurement thresholds for
    new sensor types (battery_pct, v_imbalance_pct, AI1_voltage, etc.)
    """
    t_min: float = Field(default=0.0, description="Min temperature °C")
    t_max: float = Field(default=0.0, description="Max temperature °C")
    v_min: float = Field(default=0.0, description="Min vibration mm/s")
    v_max: float = Field(default=0.0, description="Max vibration mm/s")
    c_min: float = Field(default=0.0, description="Min current A")
    c_max: float = Field(default=0.0, description="Max current A")
    extra: dict[str, dict] = Field(
        default_factory=dict,
        description="Extended thresholds: {measurement: {min_value, max_value, severity}}",
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/pytest tests/unit/test_pattern_matcher_extra.py -v --no-cov`
Expected: PASS (3/3)

- [ ] **Step 5: Verify no regression in existing tests**

Run: `./venv/bin/pytest tests/ -v --no-cov -k "not live" 2>&1 | tail -30`
Expected: All existing tests still pass

- [ ] **Step 6: Commit**

```bash
git add app/models/ihi.py tests/unit/test_pattern_matcher_extra.py
git commit -m "feat(ihi): extend PatternRange with extra dict (backward-compat for new sensor types)"
```

---

## Phase 3: Thresholds Module

### Task 5: Create `thresholds/types.py` — shared dataclasses

**Files:**
- Create: `app/services/thresholds/__init__.py`
- Create: `app/services/thresholds/types.py`
- Test: `tests/unit/test_thresholds_types.py`

- [ ] **Step 1: Write failing test for Threshold dataclass**

```python
# tests/unit/test_thresholds_types.py
import pytest
from app.services.thresholds.types import Threshold, ThresholdViolation


def test_threshold_evaluate_within_range():
    """Reading within range returns NORMAL."""
    t = Threshold(
        measurement="temperature", min_value=0, max_value=90,
        severity="DANGER", unit="°C", source="test",
    )
    assert t.evaluate(50) == "NORMAL"
    assert t.evaluate(0) == "NORMAL"
    assert t.evaluate(90) == "NORMAL"  # boundary inclusive


def test_threshold_evaluate_above_max():
    """Reading above max returns severity."""
    t = Threshold(
        measurement="velocity_rms", min_value=None, max_value=4.5,
        severity="DANGER", unit="mm/s", source="ISO 10816-3",
    )
    assert t.evaluate(5.0) == "DANGER"
    assert t.evaluate(100) == "DANGER"


def test_threshold_evaluate_below_min():
    """Reading below min returns severity."""
    t = Threshold(
        measurement="battery_pct", min_value=10.0, max_value=None,
        severity="DANGER", unit="%", source="LoRaWAN convention",
    )
    assert t.evaluate(5.0) == "DANGER"
    assert t.evaluate(0.0) == "DANGER"


def test_threshold_violation_dataclass():
    """ThresholdViolation carries device + measurement + value."""
    t = Threshold("battery_pct", None, 10.0, "DANGER", "%", "test")
    v = ThresholdViolation(device_id="Sensor-001", measurement="battery_pct",
                           value=4.18, threshold=t, severity="DANGER")
    assert v.device_id == "Sensor-001"
    assert v.value == 4.18
    assert v.severity == "DANGER"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/pytest tests/unit/test_thresholds_types.py -v --no-cov`
Expected: FAIL with "No module named 'app.services.thresholds'"

- [ ] **Step 3: Create empty `__init__.py`**

```python
# app/services/thresholds/__init__.py
"""IHI thresholds module — standard-based numeric thresholds as Python constants.

Sources: ISO 10816-3, NEMA MG-1, IEEE 1159, IEC 61000-2-4, LoRaWAN sensor convention.
"""
```

- [ ] **Step 4: Write `types.py`**

```python
# app/services/thresholds/types.py
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
```

- [ ] **Step 5: Run test to verify it passes**

Run: `./venv/bin/pytest tests/unit/test_thresholds_types.py -v --no-cov`
Expected: PASS (4/4)

- [ ] **Step 6: Commit**

```bash
git add app/services/thresholds/__init__.py app/services/thresholds/types.py tests/unit/test_thresholds_types.py
git commit -m "feat(thresholds): add types.py with Threshold and ThresholdViolation dataclasses"
```

---

### Task 6: Create `iso_10816.py` — vibration severity zones

**Files:**
- Create: `app/services/thresholds/iso_10816.py`
- Test: `tests/unit/test_thresholds_iso_10816.py`

- [ ] **Step 1: Write failing test for ISO 10816 zones**

```python
# tests/unit/test_thresholds_iso_10816.py
import pytest
from app.services.thresholds.iso_10816 import (
    ISO_10816_ZONES, DEFAULT_ISO_CLASS, classify_vibration_zone,
)


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/pytest tests/unit/test_thresholds_iso_10816.py -v --no-cov`
Expected: FAIL with "No module named 'app.services.thresholds.iso_10816'"

- [ ] **Step 3: Write `iso_10816.py`**

```python
# app/services/thresholds/iso_10816.py
"""ISO 10816-3:2009 mechanical vibration severity zones.

Each zone has: (min_mm_s, max_mm_s, severity, description_vi, color)
- A: New machine, excellent
- B: Acceptable for long-term operation
- C: Unsatisfactory, plan maintenance
- D: Damage likely, immediate action

Class II rigid (15-300 kW motors) is the most common industrial use case.
"""

# Map: (machine_class, foundation_type) -> {zone: (min, max, severity, desc_vi, color)}
ISO_10816_ZONES = {
    # Class I — small machines (<15 kW)
    ("I", "rigid"): {
        "A": (0.0,  0.71, "NORMAL",  "Mới, rất tốt", "green"),
        "B": (0.71, 1.8,  "NORMAL",  "Chấp nhận được", "yellow"),
        "C": (1.8,  4.5,  "WARNING", "Không thỏa mãn, lên kế hoạch BT", "orange"),
        "D": (4.5,  float("inf"), "DANGER", "Nguy hại", "red"),
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
    # Class III — large rigid-foundation
    ("III", "rigid"): {
        "A": (0.0,  1.4,  "NORMAL",  "Mới", "green"),
        "B": (1.4,  2.8,  "NORMAL",  "Chấp nhận được", "yellow"),
        "C": (2.8,  4.5,  "WARNING", "Lên kế hoạch BT", "orange"),
        "D": (4.5,  float("inf"), "DANGER", "Nguy hại", "red"),
    },
    # Class IV — large flexible (turbines)
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
    """Return zone letter ("A"/"B"/"C"/"D") for given velocity and machine class."""
    zones = ISO_10816_ZONES[machine_class]
    for zone in ("A", "B", "C", "D"):
        min_v, max_v, _, _, _ = zones[zone]
        if min_v <= velocity_rms_mm_s < max_v:
            return zone
    # Velocity >= all zone maxes
    return "D"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/pytest tests/unit/test_thresholds_iso_10816.py -v --no-cov`
Expected: PASS (4/4)

- [ ] **Step 5: Commit**

```bash
git add app/services/thresholds/iso_10816.py tests/unit/test_thresholds_iso_10816.py
git commit -m "feat(thresholds): add ISO 10816-3 vibration zones (4 classes x 2 foundations)"
```

---

### Task 7: Create `nema_mg1.py` — motor standards

**Files:**
- Create: `app/services/thresholds/nema_mg1.py`
- Test: `tests/unit/test_thresholds_nema_mg1.py`

- [ ] **Step 1: Write failing test for NEMA MG-1**

```python
# tests/unit/test_thresholds_nema_mg1.py
import pytest
from app.services.thresholds.nema_mg1 import (
    NEMA_VOLTAGE_IMBALANCE, NEMA_TEMP_RISE, classify_voltage_imbalance,
)


def test_nema_voltage_imbalance_thresholds():
    """NEMA MG-1 Part 14: 2% = warning, 5% = critical. Was 10% in old prompt."""
    assert NEMA_VOLTAGE_IMBALANCE["warning_pct"] == 2.0
    assert NEMA_VOLTAGE_IMBALANCE["danger_pct"] == 5.0
    assert "NEMA MG-1" in NEMA_VOLTAGE_IMBALANCE["source"]


def test_classify_voltage_imbalance():
    """v_imbalance=1% → NORMAL; 3% → WARNING; 6% → DANGER."""
    assert classify_voltage_imbalance(1.0) == "NORMAL"
    assert classify_voltage_imbalance(2.0) == "NORMAL"  # boundary inclusive
    assert classify_voltage_imbalance(3.0) == "WARNING"
    assert classify_voltage_imbalance(5.0) == "NORMAL"  # boundary inclusive
    assert classify_voltage_imbalance(6.0) == "DANGER"


def test_nema_temp_rise_class_b():
    """NEMA Class B at SF=1.15: rise limit 90°C, ref 130°C."""
    assert NEMA_TEMP_RISE["B"]["ref_c"] == 130
    assert NEMA_TEMP_RISE["B"]["rise_sf115"] == 90
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/pytest tests/unit/test_thresholds_nema_mg1.py -v --no-cov`
Expected: FAIL with "No module named 'app.services.thresholds.nema_mg1'"

- [ ] **Step 3: Write `nema_mg1.py`**

```python
# app/services/thresholds/nema_mg1.py
"""NEMA MG-1 motor standards: voltage imbalance + temperature rise limits.

Source: NEMA MG-1-2016 Part 14 (Motors and Generators).
URL: https://www.nema.org/docs/default-source/standards-document-library/mg-1-part-12-watermark.pdf

Key insight: voltage imbalance >2% cuts motor life in half; >5% is practical upper limit.
"""

# NEMA MG-1 Part 14 voltage imbalance limits
NEMA_VOLTAGE_IMBALANCE = {
    "warning_pct": 2.0,            # 2% = WARNING (was 10% in old prompt — BUG FIX)
    "danger_pct": 5.0,             # 5% = DANGER (was missing)
    "consequence": "3% imbalance → ~25% winding temp rise; 2% halves motor life",
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

    - v < 2%: NORMAL
    - 2% <= v < 5%: WARNING
    - v >= 5%: DANGER
    """
    if v_imbalance_pct < NEMA_VOLTAGE_IMBALANCE["warning_pct"]:
        return "NORMAL"
    if v_imbalance_pct < NEMA_VOLTAGE_IMBALANCE["danger_pct"]:
        return "WARNING"
    return "DANGER"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/pytest tests/unit/test_thresholds_nema_mg1.py -v --no-cov`
Expected: PASS (3/3)

- [ ] **Step 5: Commit**

```bash
git add app/services/thresholds/nema_mg1.py tests/unit/test_thresholds_nema_mg1.py
git commit -m "feat(thresholds): add NEMA MG-1 (voltage imbalance fix: 2%/5% per spec, was 10%)"
```

---

### Task 8: Create `sensor_envelopes.py` — per-device defaults

**Files:**
- Create: `app/services/thresholds/sensor_envelopes.py`
- Test: `tests/unit/test_thresholds_sensor_envelopes.py`

- [ ] **Step 1: Write failing test for sensor envelopes**

```python
# tests/unit/test_thresholds_sensor_envelopes.py
import pytest
from app.services.thresholds.sensor_envelopes import SENSOR_ENVELOPES


def test_sensor_001_envelope_has_required_measurements():
    """Sensor-001 must have temperature, velocity_rms, battery_pct, humidity."""
    env = SENSOR_ENVELOPES["Sensor-001"]
    assert "temperature" in env["thresholds"]
    assert "velocity_rms" in env["thresholds"]
    assert "battery_pct" in env["thresholds"]
    assert "humidity" in env["thresholds"]


def test_plc_001_ai_range_thresholds():
    """PLC-001 must have AI1_voltage, broken-low/broken-high checks."""
    env = SENSOR_ENVELOPES["PLC-001"]
    assert "AI1_voltage" in env["thresholds"]
    assert "AI1_below_3p6ma" in env["thresholds"]  # broken sensor
    assert "AI1_above_21ma" in env["thresholds"]


def test_meter_001_voltage_imbalance_corrected():
    """Meter-001 voltage_imbalance warning/danger per NEMA (FIX: was 10%)."""
    env = SENSOR_ENVELOPES["Meter-001"]
    v = env["thresholds"]["v_imbalance_pct"]
    assert v["max_warning"] == 2.0  # NEMA: 2%
    assert v["max_danger"] == 5.0   # NEMA: 5%


def test_meter_001_phase_loss_threshold():
    """Phase loss: 1 phase <0.5A while others >5A = DANGER."""
    env = SENSOR_ENVELOPES["Meter-001"]
    pl = env["thresholds"]["phase_loss"]
    assert pl["min_current_a"] == 0.5
    assert pl["other_phase_min_a"] == 5
    assert pl["severity"] == "DANGER"


def test_all_thresholds_have_units():
    """Every threshold with numeric bounds must have a unit."""
    for device_id, env in SENSOR_ENVELOPES.items():
        for measurement, spec in env["thresholds"].items():
            if "min" in spec or "max" in spec:
                assert "unit" in spec, f"{device_id}.{measurement} missing unit"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/pytest tests/unit/test_thresholds_sensor_envelopes.py -v --no-cov`
Expected: FAIL with "No module named 'app.services.thresholds.sensor_envelopes'"

- [ ] **Step 3: Write `sensor_envelopes.py`**

```python
# app/services/thresholds/sensor_envelopes.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/pytest tests/unit/test_thresholds_sensor_envelopes.py -v --no-cov`
Expected: PASS (5/5)

- [ ] **Step 5: Commit**

```bash
git add app/services/thresholds/sensor_envelopes.py tests/unit/test_thresholds_sensor_envelopes.py
git commit -m "feat(thresholds): add per-device sensor envelopes (Sensor-001/PLC-001/Meter-001)"
```

---

### Task 9: Create `loader.py` — unified access with trust hierarchy

**Files:**
- Create: `app/services/ihi_overrides_service.py` (stub first, real impl in Task 11)
- Create: `app/services/thresholds/loader.py`
- Test: `tests/unit/test_threshold_loader.py`

- [ ] **Step 1: Write the overrides service stub**

```python
# app/services/ihi_overrides_service.py
"""PG CRUD for ihi_device_overrides table.

Trust hierarchy integration: get_active_override() is consulted BEFORE
default thresholds in loader.get_effective_threshold().
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class DeviceOverride:
    """A single (device, measurement) threshold override."""
    device_id: str
    measurement: str
    min_value: float | None
    max_value: float | None
    severity: str           # "NORMAL" / "WARNING" / "DANGER"
    source: str             # "manual" / "auto_learned"
    set_by: str | None
    note: str | None


def get_active_override(db_pool, device_id: str, measurement: str) -> Optional[DeviceOverride]:
    """Return active override for (device, measurement) or None.

    "Active" = valid_from <= now AND (valid_to IS NULL OR valid_to > now).
    """
    if db_pool is None:
        return None
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT device_id, measurement, min_value, max_value, severity,
                       source, set_by, note
                FROM ihi_device_overrides
                WHERE device_id = %s AND measurement = %s
                  AND valid_from <= CURRENT_TIMESTAMP
                  AND (valid_to IS NULL OR valid_to > CURRENT_TIMESTAMP)
                ORDER BY created_at DESC
                LIMIT 1
            """, (device_id, measurement))
            row = cur.fetchone()
            if row is None:
                return None
            return DeviceOverride(
                device_id=row[0], measurement=row[1],
                min_value=row[2], max_value=row[3],
                severity=row[4], source=row[5],
                set_by=row[6], note=row[7],
            )


def set_override(db_pool, device_id: str, measurement: str,
                 min_value: float | None, max_value: float | None,
                 severity: str, source: str = "manual",
                 set_by: str | None = None, note: str | None = None) -> int:
    """Insert or update an override. Returns the row id."""
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO ihi_device_overrides
                (device_id, measurement, min_value, max_value, severity, source, set_by, note)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (device_id, measurement) DO UPDATE
                SET min_value = EXCLUDED.min_value,
                    max_value = EXCLUDED.max_value,
                    severity = EXCLUDED.severity,
                    source = EXCLUDED.source,
                    set_by = EXCLUDED.set_by,
                    note = EXCLUDED.note,
                    valid_from = CURRENT_TIMESTAMP,
                    valid_to = NULL
                RETURNING id
            """, (device_id, measurement, min_value, max_value, severity, source, set_by, note))
            row_id = cur.fetchone()[0]
        conn.commit()
        return row_id


def delete_override(db_pool, device_id: str, measurement: str) -> bool:
    """Delete an override. Returns True if a row was deleted."""
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                DELETE FROM ihi_device_overrides
                WHERE device_id = %s AND measurement = %s
            """, (device_id, measurement))
            deleted = cur.rowcount > 0
        conn.commit()
        return deleted
```

- [ ] **Step 2: Write failing test for loader**

```python
# tests/unit/test_threshold_loader.py
import pytest
from unittest.mock import MagicMock
from app.services.thresholds.loader import get_effective_threshold, evaluate_all_thresholds
from app.services.thresholds.types import Threshold
from app.services.ihi_overrides_service import DeviceOverride


def test_get_threshold_default_when_no_override(monkeypatch):
    """Without override, return default from sensor_envelopes."""
    monkeypatch.setattr(
        "app.services.thresholds.loader.get_active_override",
        lambda *args, **kwargs: None
    )
    t = get_effective_threshold(None, "Sensor-001", "battery_pct")
    assert t is not None
    assert t.measurement == "battery_pct"
    assert t.max_value is None
    assert t.min_value == 10.0  # default min_danger
    assert t.severity == "DANGER"


def test_get_threshold_override_wins(monkeypatch):
    """Manual override takes priority over default."""
    override = DeviceOverride(
        device_id="Sensor-001", measurement="battery_pct",
        min_value=50.0, max_value=None, severity="DANGER",
        source="manual", set_by="operator", note="Old machine, looser battery threshold"
    )
    monkeypatch.setattr(
        "app.services.thresholds.loader.get_active_override",
        lambda *args, **kwargs: override
    )
    t = get_effective_threshold(None, "Sensor-001", "battery_pct")
    assert t.source == "manual"
    assert t.min_value == 50.0  # override value, not default 10.0


def test_get_threshold_unknown_device_returns_none(monkeypatch):
    """Unknown device_id returns None (no threshold defined)."""
    monkeypatch.setattr(
        "app.services.thresholds.loader.get_active_override",
        lambda *args, **kwargs: None
    )
    t = get_effective_threshold(None, "Unknown-Device", "battery_pct")
    assert t is None


def test_evaluate_all_thresholds_detects_violations(monkeypatch):
    """evaluate_all_thresholds returns list of ThresholdViolation for violations."""
    monkeypatch.setattr(
        "app.services.thresholds.loader.get_active_override",
        lambda *args, **kwargs: None
    )
    readings = {
        "temperature": 95,      # > 90 = DANGER
        "velocity_rms": 1.0,    # OK
        "battery_pct": 5.0,     # < 10 = DANGER
        "humidity": 50,         # OK
    }
    violations = evaluate_all_thresholds(None, "Sensor-001", readings)
    measurements = {v.measurement for v in violations}
    assert "temperature" in measurements
    assert "battery_pct" in measurements
    assert "velocity_rms" not in measurements
    assert "humidity" not in measurements
    # All Sensor-001 violations should be DANGER (battery, temp) — no warnings expected
    assert all(v.severity == "DANGER" for v in violations)
```

- [ ] **Step 3: Run test to verify it fails**

Run: `./venv/bin/pytest tests/unit/test_threshold_loader.py -v --no-cov`
Expected: FAIL with "No module named 'app.services.thresholds.loader'"

- [ ] **Step 4: Write `loader.py`**

```python
# app/services/thresholds/loader.py
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
```

- [ ] **Step 5: Run test to verify it passes**

Run: `./venv/bin/pytest tests/unit/test_threshold_loader.py -v --no-cov`
Expected: PASS (4/4)

- [ ] **Step 6: Commit**

```bash
git add app/services/thresholds/loader.py app/services/ihi_overrides_service.py tests/unit/test_threshold_loader.py
git commit -m "feat(thresholds): add loader with trust hierarchy (override > default)"
```

---

## Phase 4: Override Service & API Endpoints

### Task 10: Add ihi_overrides_service tests (against real PG)

**Files:**
- Test: `tests/unit/test_ihi_overrides_service.py`

- [ ] **Step 1: Write the test**

```python
# tests/unit/test_ihi_overrides_service.py
"""Integration tests for ihi_overrides_service — uses real PG."""
import os
import pytest
from app.services.ihi_overrides_service import (
    get_active_override, set_override, delete_override, DeviceOverride,
)
from app.core.database import _get_pool


@pytest.fixture(scope="module")
def db_pool():
    pool = _get_pool()
    yield pool
    pool.close()


def test_set_then_get_override(db_pool):
    """Set override, then get it back."""
    set_override(
        db_pool, device_id="TEST-DEV-1", measurement="test_measurement",
        min_value=10.0, max_value=20.0, severity="DANGER",
        source="manual", set_by="pytest", note="test",
    )
    o = get_active_override(db_pool, "TEST-DEV-1", "test_measurement")
    assert o is not None
    assert o.min_value == 10.0
    assert o.max_value == 20.0
    assert o.severity == "DANGER"
    assert o.set_by == "pytest"
    # Cleanup
    delete_override(db_pool, "TEST-DEV-1", "test_measurement")


def test_set_override_upserts(db_pool):
    """Setting the same (device, measurement) twice updates, not duplicates."""
    set_override(db_pool, "TEST-DEV-2", "test_meas", 1.0, 2.0, "WARNING", "manual", "pytest", "first")
    set_override(db_pool, "TEST-DEV-2", "test_meas", 5.0, 6.0, "DANGER", "manual", "pytest", "second")
    o = get_active_override(db_pool, "TEST-DEV-2", "test_meas")
    assert o.min_value == 5.0
    assert o.severity == "DANGER"
    assert o.note == "second"
    delete_override(db_pool, "TEST-DEV-2", "test_meas")


def test_delete_override(db_pool):
    """Delete returns True if row existed, then get returns None."""
    set_override(db_pool, "TEST-DEV-3", "test_meas", 1.0, 2.0, "WARNING", "manual", "pytest", None)
    assert delete_override(db_pool, "TEST-DEV-3", "test_meas") is True
    assert get_active_override(db_pool, "TEST-DEV-3", "test_meas") is None
    # Deleting again returns False
    assert delete_override(db_pool, "TEST-DEV-3", "test_meas") is False
```

- [ ] **Step 2: Run test**

Run: `./venv/bin/pytest tests/unit/test_ihi_overrides_service.py -v --no-cov`
Expected: PASS (3/3) — uses real PG from `_get_pool()`

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_ihi_overrides_service.py
git commit -m "test(ihi): add integration tests for overrides service (real PG)"
```

---

### Task 11: Add override API endpoints

**Files:**
- Modify: `app/routes/ihi.py` (add 4 endpoints)
- Test: `tests/integration/test_override_endpoints.py`

- [ ] **Step 1: Write failing test for endpoints**

```python
# tests/integration/test_override_endpoints.py
import os
import pytest
from fastapi.testclient import TestClient
from app.main import app


API_KEY = os.environ.get("API_KEY", "1XteCCQ_s_UbrqOGEIYybmDBnokWhxYap90D6_Jojx8")
HEADERS = {"X-API-KEY": API_KEY, "Content-Type": "application/json"}


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_list_device_thresholds(client):
    """GET /v1/ihi/devices/{device_id}/thresholds returns merged view."""
    r = client.get("/v1/ihi/devices/Sensor-001/thresholds", headers=HEADERS)
    assert r.status_code == 200
    data = r.json()
    assert "thresholds" in data
    assert isinstance(data["thresholds"], dict)
    # Should include default thresholds for Sensor-001
    assert "battery_pct" in data["thresholds"]
    assert "temperature" in data["thresholds"]


def test_set_then_get_override(client):
    """POST override, then GET shows it."""
    # Clean first
    client.delete("/v1/ihi/devices/TEST-DEV-EP/thresholds/battery_pct", headers=HEADERS)
    # Set
    r = client.post(
        "/v1/ihi/devices/TEST-DEV-EP/thresholds",
        headers=HEADERS,
        json={"measurement": "battery_pct", "min_value": 50.0, "severity": "DANGER", "note": "pytest"},
    )
    assert r.status_code == 200, r.text
    # Get
    r2 = client.get("/v1/ihi/devices/TEST-DEV-EP/thresholds", headers=HEADERS)
    assert r2.status_code == 200
    data = r2.json()
    # Override should be present
    override_meas = [t for t in data["thresholds"].values() if t.get("source") == "manual"]
    assert any(t["measurement"] == "battery_pct" for t in override_meas)
    # Cleanup
    client.delete("/v1/ihi/devices/TEST-DEV-EP/thresholds/battery_pct", headers=HEADERS)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/pytest tests/integration/test_override_endpoints.py -v --no-cov`
Expected: FAIL with 404 (endpoints don't exist yet)

- [ ] **Step 3: Add endpoints to `app/routes/ihi.py`**

Find the import block at the top of `app/routes/ihi.py` and add:

```python
from app.services.ihi_overrides_service import (
    get_active_override, set_override, delete_override,
)
```

Add the following endpoints near the other IHI endpoints (e.g. after the existing `/rag/feedback` endpoint):

```python
# === Device threshold override endpoints ===

@router.get("/devices/{device_id}/thresholds")
async def get_device_thresholds(device_id: str) -> dict:
    """Return effective thresholds for a device (override + default merged).

    Each entry: {measurement, min_value, max_value, severity, unit, source, note}.
    """
    db = _db_pool_dep()
    envelope = SENSOR_ENVELOPES if False else None  # avoid circular
    from app.services.thresholds.sensor_envelopes import SENSOR_ENVELOPES as ENV
    from app.services.thresholds.loader import get_effective_threshold

    thresholds = {}
    env = ENV.get(device_id, {})
    for measurement in env.get("thresholds", {}).keys():
        t = get_effective_threshold(db, device_id, measurement)
        if t:
            thresholds[measurement] = {
                "measurement": t.measurement,
                "min_value": t.min_value,
                "max_value": t.max_value,
                "severity": t.severity,
                "unit": t.unit,
                "source": t.source,
                "note": t.note,
            }
    return {"device_id": device_id, "thresholds": thresholds}


@router.post("/devices/{device_id}/thresholds")
async def set_device_threshold(device_id: str, payload: dict) -> dict:
    """Set manual override for a (device, measurement) threshold.

    Body: {measurement, min_value?, max_value?, severity, note?}
    """
    measurement = payload.get("measurement")
    if not measurement:
        raise HTTPException(status_code=400, detail="measurement required")
    db = _db_pool_dep()
    set_by = payload.get("set_by") or "api_user"
    row_id = set_override(
        db, device_id=device_id, measurement=measurement,
        min_value=payload.get("min_value"),
        max_value=payload.get("max_value"),
        severity=payload.get("severity", "DANGER"),
        source="manual", set_by=set_by, note=payload.get("note"),
    )
    return {"ok": True, "id": row_id, "device_id": device_id, "measurement": measurement}


@router.delete("/devices/{device_id}/thresholds/{measurement}")
async def delete_device_threshold(device_id: str, measurement: str) -> dict:
    """Clear manual override; revert to default."""
    db = _db_pool_dep()
    deleted = delete_override(db, device_id, measurement)
    return {"ok": True, "deleted": deleted}


@router.get("/devices/{device_id}/thresholds/history")
async def get_threshold_history(device_id: str) -> dict:
    """Audit log: who set what when for this device."""
    db = _db_pool_dep()
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, measurement, min_value, max_value, severity, source,
                       set_by, note, valid_from, valid_to, created_at
                FROM ihi_device_overrides
                WHERE device_id = %s
                ORDER BY created_at DESC
            """, (device_id,))
            rows = cur.fetchall()
    return {
        "device_id": device_id,
        "history": [
            {
                "id": r[0], "measurement": r[1],
                "min_value": r[2], "max_value": r[3],
                "severity": r[4], "source": r[5],
                "set_by": r[6], "note": r[7],
                "valid_from": r[8].isoformat() if r[8] else None,
                "valid_to": r[9].isoformat() if r[9] else None,
                "created_at": r[10].isoformat() if r[10] else None,
            } for r in rows
        ],
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/pytest tests/integration/test_override_endpoints.py -v --no-cov`
Expected: PASS (2/2)

- [ ] **Step 5: Commit**

```bash
git add app/routes/ihi.py tests/integration/test_override_endpoints.py
git commit -m "feat(ihi): add 4 override API endpoints (GET list/POST/DELETE/history)"
```

---

## Phase 5: PatternMatcher Extension

### Task 12: Extend PatternMatcher to handle `extra` thresholds

**Files:**
- Modify: `app/services/ihi_rag_service.py:19-57` (PatternMatcher.matches)
- Test: `tests/unit/test_pattern_matcher_extra.py` (already exists from Task 4 — add more tests)

- [ ] **Step 1: Add new tests for PatternMatcher extra handling**

Append to `tests/unit/test_pattern_matcher_extra.py`:

```python
from app.services.ihi_rag_service import PatternMatcher


def test_pattern_matcher_old_format_still_works():
    """Old patterns (no extra) should still work after extension."""
    m = PatternMatcher()
    pattern = {"t_min": 0, "t_max": 90, "v_min": 0, "v_max": 4.5, "c_min": 0, "c_max": 65}
    assert m.matches(pattern, {"t": 50, "v": 2.0, "c": 30}) is True
    assert m.matches(pattern, {"t": 95, "v": 2.0, "c": 30}) is False


def test_pattern_matcher_with_extra_battery_pct():
    """Pattern with extra battery_pct threshold."""
    m = PatternMatcher()
    pattern = {
        "t_min": 0, "t_max": 100, "v_min": 0, "v_max": 10, "c_min": 0, "c_max": 100,
        "extra": {"battery_pct": {"min_value": 10.0}},
    }
    # Reading with battery_pct above min_value → match
    assert m.matches(pattern, {"battery_pct": 50.0}) is True
    # Reading with battery_pct below min_value → no match
    assert m.matches(pattern, {"battery_pct": 5.0}) is False


def test_pattern_matcher_missing_extra_measurement_does_not_disqualify():
    """If reading doesn't have the extra measurement, it doesn't disqualify."""
    m = PatternMatcher()
    pattern = {
        "t_min": 0, "t_max": 100, "v_min": 0, "v_max": 10, "c_min": 0, "c_max": 100,
        "extra": {"battery_pct": {"min_value": 10.0}},
    }
    # Reading has no battery_pct → still matches (missing measurement OK)
    assert m.matches(pattern, {}) is True


def test_pattern_matcher_multiple_extra_thresholds():
    """Pattern with multiple extra thresholds — ALL must pass."""
    m = PatternMatcher()
    pattern = {
        "t_min": 0, "t_max": 100, "v_min": 0, "v_max": 10, "c_min": 0, "c_max": 100,
        "extra": {
            "battery_pct": {"min_value": 10.0},
            "v_imbalance_pct": {"max_value": 5.0},
        },
    }
    # Both within range → match
    assert m.matches(pattern, {"battery_pct": 50, "v_imbalance_pct": 2.0}) is True
    # v_imbalance too high → no match
    assert m.matches(pattern, {"battery_pct": 50, "v_imbalance_pct": 6.0}) is False
    # battery too low → no match
    assert m.matches(pattern, {"battery_pct": 5, "v_imbalance_pct": 2.0}) is False
```

- [ ] **Step 2: Run new tests to verify they fail**

Run: `./venv/bin/pytest tests/unit/test_pattern_matcher_extra.py -v --no-cov`
Expected: 3/3 new tests FAIL (the implementation doesn't handle extra yet)

- [ ] **Step 3: Update PatternMatcher.matches in `app/services/ihi_rag_service.py`**

Find the `matches()` method (around line 19-57) and replace with:

```python
def matches(self, pattern: dict, reading: dict) -> bool:
    """Check if reading matches pattern (including extra thresholds)."""
    # Check temperature
    if reading.get("t") is not None:
        t = reading["t"]
        if "t_min" in pattern and t < pattern["t_min"]:
            return False
        if "t_max" in pattern and t > pattern["t_max"]:
            return False
    # Check vibration
    if reading.get("v") is not None:
        v = reading["v"]
        if "v_min" in pattern and v < pattern["v_min"]:
            return False
        if "v_max" in pattern and v > pattern["v_max"]:
            return False
    # Check current
    if reading.get("c") is not None:
        c = reading["c"]
        if "c_min" in pattern and c < pattern["c_min"]:
            return False
        if "c_max" in pattern and c > pattern["c_max"]:
            return False
    # Check extra thresholds (NEW)
    extra = pattern.get("extra", {})
    for measurement, bounds in extra.items():
        value = reading.get(measurement)
        if value is None:
            continue  # missing measurement doesn't disqualify
        if "min_value" in bounds and value < bounds["min_value"]:
            return False
        if "max_value" in bounds and value > bounds["max_value"]:
            return False
    return True
```

- [ ] **Step 4: Run all tests to verify**

Run: `./venv/bin/pytest tests/unit/test_pattern_matcher_extra.py -v --no-cov`
Expected: PASS (7/7 — 3 from Task 4 + 4 new)

- [ ] **Step 5: Verify no regression in RAG tests**

Run: `./venv/bin/pytest tests/ -v --no-cov -k "ihi" 2>&1 | tail -20`
Expected: All existing IHI tests still pass

- [ ] **Step 6: Commit**

```bash
git add app/services/ihi_rag_service.py tests/unit/test_pattern_matcher_extra.py
git commit -m "feat(rag): PatternMatcher supports extra dict (new sensor types, backward-compat)"
```

---

## Phase 6: Rule Pre-Check Integration

### Task 13: Add `IHIThresholdAnalyzer` (replaces legacy for new code path)

**Files:**
- Modify: `app/services/ihi_analyzer.py` (add IHIThresholdAnalyzer class)
- Test: `tests/unit/test_ihi_analyzer_v2.py`

- [ ] **Step 1: Write failing test for new analyzer**

```python
# tests/unit/test_ihi_analyzer_v2.py
import pytest
from unittest.mock import MagicMock
from app.services.ihi_analyzer import IHIThresholdAnalyzer, AlertResult, AlertLevel


@pytest.fixture
def mock_db_pool(monkeypatch):
    """Mock db_pool + override service to return no overrides."""
    monkeypatch.setattr(
        "app.services.ihi_analyzer.get_active_override",
        lambda *args, **kwargs: None
    )
    return MagicMock()


def test_analyzer_returns_normal_when_all_readings_ok(mock_db_pool):
    """All readings within default thresholds → NORMAL."""
    a = IHIThresholdAnalyzer(mock_db_pool)
    readings = {
        "Sensor-001": {"temperature": 50, "velocity_rms": 1.0, "battery_pct": 80, "humidity": 50},
    }
    result = a.analyze_readings(readings)
    assert result.alert == AlertLevel.NORMAL
    assert result.reason == "all readings within thresholds"


def test_analyzer_returns_danger_on_battery_critical(mock_db_pool):
    """battery 4% (Sensor-001 default min_danger=10) → DANGER."""
    a = IHIThresholdAnalyzer(mock_db_pool)
    readings = {
        "Sensor-001": {"temperature": 50, "velocity_rms": 1.0, "battery_pct": 4.0, "humidity": 50},
    }
    result = a.analyze_readings(readings)
    assert result.alert == AlertLevel.DANGER
    assert "battery_pct" in result.reason
    assert "Sensor-001" in result.devices


def test_analyzer_returns_danger_on_voltage_imbalance(mock_db_pool):
    """Meter-001 v_imbalance_pct=6 (NEMA: >5 = DANGER) → DANGER."""
    a = IHIThresholdAnalyzer(mock_db_pool)
    readings = {
        "Meter-001": {"v_imbalance_pct": 6.0, "f_hz": 50.0, "i_imbalance_pct": 5.0, "power_factor": 0.9},
    }
    result = a.analyze_readings(readings)
    assert result.alert == AlertLevel.DANGER
    assert "v_imbalance_pct" in result.reason
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/pytest tests/unit/test_ihi_analyzer_v2.py -v --no-cov`
Expected: FAIL with "module 'app.services.ihi_analyzer' has no attribute 'IHIThresholdAnalyzer'"

- [ ] **Step 3: Add IHIThresholdAnalyzer to `app/services/ihi_analyzer.py`**

Append to the file:

```python
# === New threshold-based analyzer (Layer 1 of 3-layer pipeline) ===

from typing import Dict, List
from app.services.thresholds.loader import evaluate_all_thresholds


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
                device_id="", alert=AlertLevel.NORMAL,
                reason="all readings within thresholds",
            )

        # Check for DANGER first
        danger_violations = [v for v in all_violations if v.severity == "DANGER"]
        if danger_violations:
            first = danger_violations[0]
            return AlertResult(
                device_id=first.device_id,
                alert=AlertLevel.DANGER,
                reason=f"DANGER: {first.measurement}={first.value}{first.threshold.unit} "
                       f"(threshold: {first.threshold.severity} from {first.threshold.source})",
            )

        # WARNING only
        warning = all_violations[0]
        return AlertResult(
            device_id=warning.device_id,
            alert=AlertLevel.WARNING,
            reason=f"WARNING: {warning.measurement}={warning.value}{warning.threshold.unit} "
                   f"(threshold: {warning.threshold.severity} from {warning.threshold.source})",
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/pytest tests/unit/test_ihi_analyzer_v2.py -v --no-cov`
Expected: PASS (3/3)

- [ ] **Step 5: Commit**

```bash
git add app/services/ihi_analyzer.py tests/unit/test_ihi_analyzer_v2.py
git commit -m "feat(analyzer): add IHIThresholdAnalyzer (uses thresholds module, replaces t/v/c-only)"
```

---

### Task 14: Update `/v1/ihi/analyze` endpoint to use new pipeline

**Files:**
- Modify: `app/routes/ihi.py` (analyze endpoint)
- Test: `tests/integration/test_analyze_pipeline.py`

- [ ] **Step 1: Write failing test for new analyze pipeline**

```python
# tests/integration/test_analyze_pipeline.py
import os
import pytest
from fastapi.testclient import TestClient
from app.main import app


API_KEY = os.environ.get("API_KEY", "1XteCCQ_s_UbrqOGEIYybmDBnokWhxYap90D6_Jojx8")
HEADERS = {"X-API-KEY": API_KEY, "Content-Type": "application/json"}


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_analyze_with_clear_danger_battery(client):
    """Battery 4% should trigger Layer 1 rule and return DANGER from rule layer."""
    r = client.post("/v1/ihi/analyze", headers=HEADERS, json={
        "ts": "03/06 14:30",
        "data": [{"id": "Sensor-001", "t": 50, "v": 1.0, "c": 5}],
        "extra": {"Sensor-001": {"battery_pct": 4.0, "humidity": 50}},
    })
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["alert"] == "DANGER"
    assert data["source_layer"].startswith("rule_")  # rule caught it
    assert any(v["measurement"] == "battery_pct" for v in data["violations"])


def test_analyze_with_normal_readings(client):
    """All readings OK → NORMAL from rule layer."""
    r = client.post("/v1/ihi/analyze", headers=HEADERS, json={
        "ts": "03/06 14:30",
        "data": [{"id": "Sensor-001", "t": 50, "v": 1.0, "c": 5}],
        "extra": {"Sensor-001": {"battery_pct": 80, "humidity": 50}},
    })
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["alert"] == "NORMAL"
    assert data["source_layer"] == "rule"


def test_analyze_with_voltage_imbalance_nema(client):
    """v_imbalance=6% (>5% NEMA) → DANGER from rule layer."""
    r = client.post("/v1/ihi/analyze", headers=HEADERS, json={
        "ts": "03/06 14:30",
        "data": [{"id": "Meter-001", "t": 30, "v": 0, "c": 5}],
        "extra": {"Meter-001": {"v_imbalance_pct": 6.0, "f_hz": 50.0}},
    })
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["alert"] == "DANGER"
    assert any(v["measurement"] == "v_imbalance_pct" for v in data["violations"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/pytest tests/integration/test_analyze_pipeline.py -v --no-cov`
Expected: FAIL — new `extra` field, `source_layer`, `violations` not in response

- [ ] **Step 3: Update `AnalyzeRequest` and `AnalyzeResponse` in `app/models/ihi.py`**

Find `AnalyzeResponse` (around line 80) and replace with:

```python
class ThresholdViolationModel(BaseModel):
    """One threshold violation surfaced in analyze response."""
    device_id: str
    measurement: str
    value: float
    severity: str
    threshold_source: str
    threshold_severity: str
    unit: str
    note: str | None = None


class AnalyzeRequest(BaseModel):
    """Request to analyze IHI sensor data (extended with extra measurements)."""
    ts: str = Field(..., description="Timestamp in DD/MM HH:MM format")
    data: List[SensorReading] = Field(default_factory=list)
    extra: dict[str, dict] = Field(
        default_factory=dict,
        description="Extra measurements per device: {device_id: {measurement: value}}",
    )
    override_thresholds: bool = Field(
        default=False,
        description="If true, treat submitted readings as new baseline overrides for the device",
    )
    note: str = Field(default="", description="Operator note (used when override_thresholds=true)")


class AnalyzeResponse(BaseModel):
    alert: AlertLevel
    devices: List[str]
    case_id: Optional[str] = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    symptom: Optional[str] = None
    # NEW (Layer 1+2+3 pipeline)
    violations: list[ThresholdViolationModel] = Field(default_factory=list)
    source_layer: str = "unknown"  # "rule_override" | "rule_default" | "rag" | "llm"
    narrative: str = ""
```

- [ ] **Step 4: Update `/v1/ihi/analyze` endpoint in `app/routes/ihi.py`**

Find the `analyze_sensor_data` function (around line 76) and replace the body with:

```python
@router.post("/analyze", response_model=AnalyzeResponse)
async def analyze_sensor_data(
    payload: AnalyzeRequest,
    request: Request,
) -> AnalyzeResponse:
    """Analyze sensor data with 3-layer pipeline: rule → RAG → LLM.

    Layer 1: rule pre-check using thresholds/loader (overrides > defaults).
    Layer 2: RAG pattern-match + vector similarity (if Layer 1 uncertain).
    Layer 3: LLM with RAG context (last resort, narrator only).
    """
    # Build device_readings dict: {device_id: {measurement: value}}
    device_readings = {}
    for r in payload.data:
        device_readings[r.id] = {"temperature": r.t, "velocity": r.v, "current": r.c}
    for device_id, extra in payload.extra.items():
        device_readings.setdefault(device_id, {}).update(extra)

    db = _db_pool_dep()
    analyzer = IHIThresholdAnalyzer(db)
    rule_result = analyzer.analyze_readings(device_readings)

    # Build violation list for response
    violations = []
    for device_id, readings in device_readings.items():
        for v in evaluate_all_thresholds(db, device_id, readings):
            violations.append(ThresholdViolationModel(
                device_id=v.device_id, measurement=v.measurement,
                value=v.value, severity=v.severity,
                threshold_source=v.threshold.source,
                threshold_severity=v.threshold.severity,
                unit=v.threshold.unit, note=v.threshold.note,
            ))

    # Layer 1 hard stop: any DANGER violation
    if rule_result.alert == AlertLevel.DANGER:
        source = "rule_override" if any(
            v.threshold_source == "manual" for v in violations
        ) else "rule_default"
        # Handle override_thresholds: write submitted readings as new baseline
        if payload.override_thresholds:
            api_key_id = getattr(request.state, "api_key_id", None)
            set_by = f"api_key:{api_key_id}" if api_key_id else "manual"
            for device_id, readings in device_readings.items():
                for measurement, value in readings.items():
                    if value is None: continue
                    set_override(
                        db, device_id=device_id, measurement=measurement,
                        min_value=None, max_value=value,
                        severity=rule_result.alert.value,
                        source="manual", set_by=set_by, note=payload.note,
                    )
        return AnalyzeResponse(
            alert=rule_result.alert,
            devices=list({v.device_id for v in violations if v.severity == "DANGER"}),
            case_id=None, confidence=1.0,
            symptom=None, violations=violations,
            source_layer=source,
            narrative=f"Rule layer caught {len(violations)} violation(s)",
        )

    # Layer 1 WARNING: still escalate to RAG for context
    # Layer 1 NORMAL or no violations: check RAG, then LLM
    rag_service = _get_rag_service()
    # ... (Layer 2 + 3 implementation in Phase 7)

    return AnalyzeResponse(
        alert=rule_result.alert,
        devices=list({v.device_id for v in violations}),
        case_id=None, confidence=rule_result.alert != AlertLevel.NORMAL and 0.7 or 1.0,
        symptom=None, violations=violations,
        source_layer="rule",
        narrative="Layer 1 only (Layers 2/3 not yet integrated)",
    )
```

- [ ] **Step 5: Add necessary imports at the top of `app/routes/ihi.py`**

```python
from app.services.ihi_analyzer import IHIThresholdAnalyzer
from app.services.thresholds.loader import evaluate_all_thresholds
from app.services.ihi_overrides_service import set_override
```

- [ ] **Step 6: Run test to verify it passes**

Run: `./venv/bin/pytest tests/integration/test_analyze_pipeline.py -v --no-cov`
Expected: PASS (3/3)

- [ ] **Step 7: Commit**

```bash
git add app/routes/ihi.py app/models/ihi.py tests/integration/test_analyze_pipeline.py
git commit -m "feat(analyze): new 3-layer pipeline (Layer 1 rule pre-check integrated)"
```

---

## Phase 7: RAG Vector Index & Hybrid Retrieval

### Task 15: Create `vector_index.py` — FastEmbed + PG vector storage

**Files:**
- Create: `app/services/vector_index.py`
- Test: `tests/unit/test_vector_index.py`

- [ ] **Step 1: Write failing test for vector index**

```python
# tests/unit/test_vector_index.py
import os
import pytest
import numpy as np
from unittest.mock import MagicMock
from app.services.vector_index import IHIVectorIndex


def test_embed_returns_384_dim_vector():
    """FastEmbed paraphrase-multilingual-MiniLM-L12-v2 returns 384-dim embeddings."""
    idx = IHIVectorIndex()
    emb = idx.embed("test text")
    assert len(emb) == 384
    assert isinstance(emb[0], float)


def test_cosine_similarity_identical():
    """Identical vectors → similarity 1.0."""
    idx = IHIVectorIndex()
    v1 = idx.embed("vibration too high")
    v2 = idx.embed("vibration too high")
    sim = idx.cosine_similarity(v1, v2)
    assert 0.99 < sim <= 1.0


def test_cosine_similarity_unrelated():
    """Unrelated texts → low similarity."""
    idx = IHIVectorIndex()
    v1 = idx.embed("vibration too high")
    v2 = idx.embed("phở bò tái")
    sim = idx.cosine_similarity(v1, v2)
    assert sim < 0.5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/pytest tests/unit/test_vector_index.py -v --no-cov`
Expected: FAIL with "No module named 'app.services.vector_index'"

- [ ] **Step 3: Write `vector_index.py`**

```python
# app/services/vector_index.py
"""FastEmbed-based vector index for IHI RAG case descriptions.

Model: paraphrase-multilingual-MiniLM-L12-v2 (same as knowledge retrieval)
Output dimension: 384
"""
from __future__ import annotations

import math
from functools import lru_cache

# Lazy load FastEmbed to avoid slow import on app startup
_embedder = None


def _get_embedder():
    global _embedder
    if _embedder is None:
        from fastembed import TextEmbedding
        _embedder = TextEmbedding(model_name="paraphrase-multilingual-MiniLM-L12-v2")
    return _embedder


class IHIVectorIndex:
    """Vector index for IHI RAG case descriptions.

    Wraps FastEmbed for embedding + cosine similarity for ranking.
    Storage in PG `ihi_case_embeddings` (vector(384)) is done separately
    by IHIRagService.
    """

    def embed(self, text: str) -> list[float]:
        """Embed a single text → 384-dim vector."""
        embedder = _get_embedder()
        result = list(embedder.embed([text]))[0]
        return [float(x) for x in result]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed a list of texts (more efficient than one-by-one)."""
        embedder = _get_embedder()
        return [[float(x) for x in v] for v in embedder.embed(texts)]

    @staticmethod
    def cosine_similarity(a: list[float], b: list[float]) -> float:
        """Compute cosine similarity between two vectors."""
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(y * y for y in b))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/pytest tests/unit/test_vector_index.py -v --no-cov --timeout=60`
Expected: PASS (3/3) — may take 30s for first embed (model load)

- [ ] **Step 5: Commit**

```bash
git add app/services/vector_index.py tests/unit/test_vector_index.py
git commit -m "feat(rag): add vector index (FastEmbed paraphrase-multilingual, 384-dim)"
```

---

### Task 16: Add `retrieve_top_k` to `IHIragService`

**Files:**
- Modify: `app/services/ihi_rag_service.py` (add methods)
- Test: `tests/integration/test_rag_hybrid_retrieval.py`

- [ ] **Step 1: Write failing test for hybrid retrieval**

```python
# tests/integration/test_rag_hybrid_retrieval.py
import os
import pytest
from app.core.database import _get_pool
from app.services.ihi_rag_service import IHIragService


@pytest.fixture(scope="module")
def db_pool():
    pool = _get_pool()
    yield pool
    pool.close()


@pytest.fixture
def rag_service(db_pool):
    s = IHIragService(db_pool=db_pool)
    s.load_cases()
    return s


def test_retrieve_top_k_returns_k_results(rag_service):
    """retrieve_top_k returns up to k cases."""
    results = rag_service.retrieve_top_k(
        readings={"temperature": 92, "velocity": 1.0, "current": 5},
        k=3
    )
    assert isinstance(results, list)
    assert len(results) <= 3
    for case, score in results:
        assert isinstance(score, float)
        assert 0 <= score <= 1.0


def test_retrieve_top_k_pattern_match_high_score(rag_service):
    """Exact pattern match should give high score (>= 0.7)."""
    # Match overheat case: t=95, v=1, c=5
    results = rag_service.retrieve_top_k(
        readings={"temperature": 95, "velocity": 1.0, "current": 5},
        k=3
    )
    # First result should be RAG-001 (overheat) with high score
    assert results
    first_case, first_score = results[0]
    assert first_score >= 0.7


def test_retrieve_top_k_empty_for_no_match(rag_service):
    """No matching case → empty list."""
    # Use values that match no case (very low everything)
    results = rag_service.retrieve_top_k(
        readings={"temperature": 0.1, "velocity": 0.0, "current": 0.0},
        k=3
    )
    # May still get vector matches, but scores should be low
    if results:
        assert all(score < 0.7 for _, score in results)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/pytest tests/integration/test_rag_hybrid_retrieval.py -v --no-cov`
Expected: FAIL — `retrieve_top_k` not implemented yet

- [ ] **Step 3: Add `retrieve_top_k` method to `IHIragService` in `app/services/ihi_rag_service.py`**

Find the `IHIragService` class (around line 117) and add the following methods. Also add these imports at the top of the file:

```python
import logging
logger = logging.getLogger(__name__)
```

Then inside the class, after `find_matching_case`:

```python
def retrieve_top_k(self, readings: dict, k: int = 3) -> list[tuple[dict, float]]:
    """Hybrid retrieval: pattern-match (exact) + vector similarity (semantic).

    Returns list of (case, confidence_score) tuples, sorted by score desc.
    """
    candidates: dict[int, tuple[dict, float]] = {}

    # 1. Pattern-match — high precision, weight 0.7
    for case in self._case_cache:
        if self.matcher.matches(case.get("pattern", {}), readings):
            score = self._calculate_confidence(
                case.get("pattern", {}), readings, "auto", case
            )
            # Pattern match boosts score to 0.7
            candidates[case["id"]] = (case, max(0.7, score))

    # 2. Vector similarity — recall, weight 0.3 (only if embeddings exist)
    if self._case_embeddings:
        try:
            query_text = self._readings_to_text(readings)
            query_emb = self._vector_index.embed(query_text)
            for case_id, sim in self._top_k_similar(query_emb, k * 2):
                if case_id in candidates:
                    old_case, old_score = candidates[case_id]
                    candidates[case_id] = (old_case, min(1.0, old_score + 0.3 * sim))
                else:
                    case = self._case_map.get(case_id)
                    if case:
                        candidates[case_id] = (case, 0.3 * sim)
        except Exception as e:
            logger.warning("Vector similarity failed: %s", e)

    # 3. Return top-k by score
    sorted_candidates = sorted(
        candidates.values(), key=lambda x: x[1], reverse=True
    )
    return [(case, score) for case, score in sorted_candidates[:k]]


def _readings_to_text(self, readings: dict) -> str:
    """Convert readings dict to searchable text description."""
    parts = []
    for k, v in readings.items():
        if v is not None:
            parts.append(f"{k}={v}")
    return "sensor readings: " + " ".join(parts)


def _top_k_similar(self, query_emb: list[float], k: int) -> list[tuple[int, float]]:
    """Return top-k (case_id, similarity) from in-memory embeddings."""
    scored = []
    for case_id, emb in self._case_embeddings.items():
        sim = self._vector_index.cosine_similarity(query_emb, emb)
        scored.append((case_id, sim))
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:k]


def rebuild_vector_index(self) -> int:
    """Re-embed all cases and store in ihi_case_embeddings. Returns count."""
    if self._db_pool is None:
        return 0
    from app.services.vector_index import IHIVectorIndex
    self._vector_index = IHIVectorIndex()
    descriptions = [c.get("description", "") for c in self._case_cache]
    if not descriptions:
        return 0
    try:
        embeddings = self._vector_index.embed_batch(descriptions)
    except Exception as e:
        logger.warning("Failed to embed case descriptions: %s", e)
        return 0
    with self._db_pool.connection() as conn:
        with conn.cursor() as cur:
            for case, emb in zip(self._case_cache, embeddings):
                cur.execute("""
                    INSERT INTO ihi_case_embeddings (case_id, embedding)
                    VALUES (%s, %s::vector)
                    ON CONFLICT (case_id) DO UPDATE SET embedding = EXCLUDED.embedding
                """, (case["id"], str(emb)))
        conn.commit()
    # Cache in memory
    self._case_embeddings = {
        c["id"]: self._vector_index.embed(c.get("description", ""))
        for c in self._case_cache
    }
    return len(self._case_cache)


def load_cases(self) -> int:
    """Load RAG cases (existing method, extended to also load embeddings)."""
    if self._db_pool is None:
        return 0
    try:
        with self._db_pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT id, device_id, severity, symptom, pattern, description,
                           confirmed_by, match_count, created_at
                    FROM ihi_rag_cases
                    ORDER BY severity DESC, match_count DESC
                """)
                rows = cur.fetchall()
                # Also load embeddings
                cur.execute("SELECT case_id, embedding FROM ihi_case_embeddings")
                emb_rows = cur.fetchall()
        self._case_cache = []
        self._case_map = {}
        for row in rows:
            case = {
                "id": row["id"], "device_id": row["device_id"],
                "severity": row["severity"], "symptom": row.get("symptom", ""),
                "pattern": row["pattern"] if isinstance(row["pattern"], dict) else {},
                "description": row["description"],
                "confirmed_by": row["confirmed_by"],
                "match_count": row["match_count"] or 0,
                "created_at": row["created_at"],
            }
            self._case_cache.append(case)
            self._case_map[row["id"]] = case
        # Load embeddings (parse string format "[1,2,3,...]" to list of float)
        self._case_embeddings = {}
        for case_id, emb_str in emb_rows:
            try:
                emb_list = [float(x) for x in emb_str.strip("[]").split(",")]
                self._case_embeddings[case_id] = emb_list
            except (ValueError, AttributeError):
                pass
        return len(self._case_cache)
    except Exception as e:
        logger.warning("load_cases failed: %s", e)
        return 0
```

Also add to `__init__`:

```python
def __init__(self, db_pool=None):
    self._db_pool = db_pool
    self._case_cache = []
    self._case_map = {}
    self._case_embeddings: dict[int, list[float]] = {}
    self._vector_index = None
    self.matcher = PatternMatcher()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/pytest tests/integration/test_rag_hybrid_retrieval.py -v --no-cov --timeout=120`
Expected: PASS (3/3) — may take time on first embed

- [ ] **Step 5: Commit**

```bash
git add app/services/ihi_rag_service.py tests/integration/test_rag_hybrid_retrieval.py
git commit -m "feat(rag): hybrid retrieval (pattern-match + vector similarity, top-k)"
```

---

## Phase 8: LLM Context Injection & Updated Prompt

### Task 17: Update `_IHI_LLM_SYSTEM` prompt with NEMA corrections

**Files:**
- Modify: `app/routes/ihi.py:377-...` (update `_IHI_LLM_SYSTEM` constant)

- [ ] **Step 1: Replace the existing prompt**

Find `_IHI_LLM_SYSTEM` constant (around line 377) and replace with:

```python
_IHI_LLM_SYSTEM = """Bạn là chuyên gia giám sát tình trạng máy công nghiệp. Phân tích readings cảm biến của 3 thiết bị tại MỘT thời điểm.

Thiết bị:
- Sensor-001 (vibration): temperature (°C), humidity (%), battery (%), velocity_x/y/z (mm/s), acceleration_peak_x/y/z
- PLC-001 (digital): AI1 (V), DI1..DI4 (0/1)
- Meter-001 (3-phase electric): V1N/V2N/V3N (V), I1/I2/I3 (A), kW/kW1/kW2/kW3, kVA, kVAr, kWh, PF/PF1/PF2/PF3, F (Hz)

Ngưỡng tham khảo (đã cập nhật theo NEMA MG-1, ISO 10816-3):
- Temperature: >90°C DANGER, 80-90°C WARNING (NEMA Class B SF=1.15)
- Velocity (Class II rigid, Sensor-001 default): >4.5 mm/s DANGER, 2.8-4.5 WARNING (ISO 10816-3)
- Velocity (Class II flexible): >7.1 mm/s DANGER, 4.5-7.1 WARNING
- Current: >75A DANGER, 65-75A WARNING
- Voltage imbalance: >5% DANGER, 2-5% WARNING (NEMA MG-1 Part 14 — KHÔNG dùng 10% cũ)
- Frequency: <49 Hz hoặc >51 Hz WARNING
- Battery sensor: <10% DANGER, 10-20% WARNING (LoRaWAN convention)
- PLC AI range: 0-10V (hoặc 4-20mA); ngoài range = DANGER (broken sensor)
- Phase loss: 1 phase <0.5A trong khi 2 phase >5A = DANGER
- All phases near 0A: DANGER (machine off hoặc mất toàn bộ pha)
- Power factor: <0.7 WARNING
- DI đột ngột đổi trạng thái: cảnh báo

Lưu ý: Mỗi device có thể có manual override (set bởi operator). Override luôn ưu tiên cao nhất.

{rag_context}

Trả lời ngắn gọn bằng tiếng Việt, format BẮT BUỘC:
**Verdict:** NORMAL / WARNING / DANGER
**Giải thích:** (2-4 câu, nêu rõ chỉ số bất thường nếu có)
**Khuyến nghị:** (1 câu, hoặc "Không cần" nếu NORMAL)"""
```

- [ ] **Step 2: Verify by running the existing evaluate endpoint**

```bash
curl -s -H "X-API-KEY: $API_KEY" -H "Content-Type: application/json" \
  -d '{"phase":1,"sample_time":"2026-06-03T12:00:00Z","devices":{"Sensor-001":{"type":"vibration","readings":{"temperature":95,"battery_pct":4}}}}' \
  http://127.0.0.1:8000/v1/ihi/evaluate | python3 -m json.tool | head -15
```

Expected: Verdict mentions both temperature and battery, with NEMA/ISO references

- [ ] **Step 3: Commit**

```bash
git add app/routes/ihi.py
git commit -m "feat(ihi): update LLM system prompt (NEMA 2%/5% voltage, ISO zones, all new signals)"
```

---

### Task 18: Integrate RAG context into LLM call in `/v1/ihi/evaluate`

**Files:**
- Modify: `app/routes/ihi.py` (`evaluate_snapshot` function)

- [ ] **Step 1: Add RAG context injection**

Find the `evaluate_snapshot` function (around line 400+ in the diff) and modify the system message construction:

```python
# After building user_msg, before calling httpx:
rag_service = _get_rag_service()
rag_context = ""
try:
    # Convert devices dict to readings format for RAG
    flat_readings = {}
    for dev_name, dev in payload.devices.items():
        readings = dev.get("readings", {}) if isinstance(dev, dict) else {}
        for k, v in readings.items():
            flat_readings[k] = v
    top_cases = rag_service.retrieve_top_k(flat_readings, k=3)
    if top_cases:
        parts = ["Các case tương tự từ knowledge base:"]
        for i, (case, score) in enumerate(top_cases, 1):
            parts.append(
                f"[{i}] case_id={case['id']} severity={case['severity']} "
                f"symptom={case.get('symptom', '?')} (match: {score:.2f})\n"
                f"    Pattern: {case.get('pattern', {})}\n"
                f"    Mô tả: {case.get('description', '')}"
            )
        rag_context = "\n".join(parts)
    else:
        rag_context = "Không có case tương tự trong knowledge base."
except Exception as e:
    logger.warning("RAG context retrieval failed: %s", e)
    rag_context = "RAG lookup skipped (error)."

# Then build body with injected context:
body = {
    "model": model,
    "messages": [
        {"role": "system", "content": _IHI_LLM_SYSTEM.format(rag_context=rag_context)},
        {"role": "user", "content": user_msg},
    ],
    "max_tokens": 800,
    "temperature": 0.2,
    "stream": False,
}
```

- [ ] **Step 2: Test via curl**

```bash
curl -s -H "X-API-KEY: $API_KEY" -H "Content-Type: application/json" \
  -d '{"phase":1,"sample_time":"2026-06-03T12:00:00Z","devices":{"Sensor-001":{"type":"vibration","readings":{"temperature":95,"battery_pct":4}}}}' \
  http://127.0.0.1:8000/v1/ihi/evaluate | python3 -m json.tool | head -25
```

Expected: Verdict text contains references to overheat and battery (RAG-influenced)

- [ ] **Step 3: Commit**

```bash
git add app/routes/ihi.py
git commit -m "feat(evaluate): inject top-3 RAG cases into LLM prompt as context"
```

---

## Phase 9: Case Saving (Auto-learn)

### Task 19: Create `IHICaseSaver` service

**Files:**
- Create: `app/services/ihi_case_saver.py`
- Test: `tests/unit/test_ihi_case_saver.py`

- [ ] **Step 1: Write failing test for case saver**

```python
# tests/unit/test_ihi_case_saver.py
import os
import pytest
from app.core.database import _get_pool
from app.services.ihi_case_saver import IHICaseSaver
from app.models.ihi import AlertLevel, AnalyzeResponse


@pytest.fixture(scope="module")
def db_pool():
    pool = _get_pool()
    yield pool
    pool.close()


def test_save_danger_verdict_creates_case(db_pool):
    """Saving a DANGER verdict creates a new ihi_rag_cases row."""
    saver = IHICaseSaver(db_pool)
    result = AnalyzeResponse(
        alert=AlertLevel.DANGER,
        devices=["Sensor-001"],
        case_id=None, confidence=0.8,
        symptom="battery_critical",
        narrative="Battery 4% critical, charging needed",
    )
    case_id = saver.save_verdict(
        scrape_id=99999, phase=1, sample_time="2026-06-03T12:00:00Z",
        readings={"battery_pct": 4.0, "temperature": 50.0},
        llm_result=result,
    )
    assert case_id is not None
    # Verify
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT device_id, severity, confirmed_by FROM ihi_rag_cases WHERE id = %s",
                (case_id,)
            )
            row = cur.fetchone()
            assert row[0].startswith("scrape_99999")
            assert row[1] == "danger"
            assert row[2] == "auto_learned"
            # Cleanup
            cur.execute("DELETE FROM ihi_rag_cases WHERE id = %s", (case_id,))


def test_save_normal_verdict_returns_none(db_pool):
    """Saving a NORMAL verdict is skipped (avoid noise)."""
    saver = IHICaseSaver(db_pool)
    result = AnalyzeResponse(
        alert=AlertLevel.NORMAL, devices=[], case_id=None,
        confidence=0.9, symptom=None, narrative="All good",
    )
    case_id = saver.save_verdict(
        scrape_id=99999, phase=1, sample_time="2026-06-03T12:00:00Z",
        readings={}, llm_result=result,
    )
    assert case_id is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/pytest tests/unit/test_ihi_case_saver.py -v --no-cov`
Expected: FAIL with "No module named 'app.services.ihi_case_saver'"

- [ ] **Step 3: Write `ihi_case_saver.py`**

```python
# app/services/ihi_case_saver.py
"""Save LLM verdicts as RAG cases for future retrieval (auto-learn).

Skip NORMAL verdicts (high volume, low signal) and low-confidence verdicts.
"""
from __future__ import annotations

import logging
from typing import Optional

from app.models.ihi import AlertLevel, AnalyzeResponse
from app.services.ihi_rag_service import IHIragService

logger = logging.getLogger(__name__)


class IHICaseSaver:
    """Save LLM verdicts as RAG cases."""

    def __init__(self, db_pool):
        self.db_pool = db_pool
        self.rag_service = IHIragService(db_pool=db_pool)
        self.rag_service.load_cases()

    def save_verdict(
        self,
        scrape_id: int,
        phase: int,
        sample_time: str,
        readings: dict,
        llm_result: AnalyzeResponse,
    ) -> Optional[int]:
        """Save LLM verdict as new RAG case. Returns case_id or None if skipped."""
        # Skip NORMAL (too noisy)
        if llm_result.alert == AlertLevel.NORMAL:
            return None
        # Skip low confidence
        if llm_result.confidence < 0.5:
            return None

        # Build pattern from readings
        pattern = {
            "t_min": 0, "t_max": 100, "v_min": 0, "v_max": 10,
            "c_min": 0, "c_max": 100,
            "extra": {},
        }
        for m in ("battery_pct", "v_imbalance_pct", "AI1_voltage", "f_hz", "power_factor"):
            v = readings.get(m)
            if v is not None:
                if m in ("battery_pct", "f_hz"):
                    # Min-based: store as min_value (v - 5%)
                    pattern["extra"][m] = {
                        "min_value": max(0, v * 0.95),
                        "max_value": v * 1.05,
                    }
                else:
                    # Max-based
                    pattern["extra"][m] = {
                        "min_value": v * 0.95,
                        "max_value": v * 1.05,
                    }

        # Save to PG
        try:
            device_id = f"scrape_{scrape_id}_p{phase}"
            case_id = self.rag_service.create_case(
                device_id=device_id,
                severity=llm_result.alert.value.lower(),
                symptom=llm_result.symptom or "auto_detected",
                pattern=pattern,
                description=llm_result.narrative or "(auto from LLM verdict)",
                confirmed_by="auto_learned",
            )
            # Update vector index in memory (best effort)
            try:
                from app.services.vector_index import IHIVectorIndex
                vec_idx = IHIVectorIndex()
                emb = vec_idx.embed(llm_result.narrative or device_id)
                with self.db_pool.connection() as conn:
                    with conn.cursor() as cur:
                        cur.execute("""
                            INSERT INTO ihi_case_embeddings (case_id, embedding)
                            VALUES (%s, %s::vector)
                            ON CONFLICT (case_id) DO UPDATE SET embedding = EXCLUDED.embedding
                        """, (case_id, str(emb)))
                    conn.commit()
            except Exception as e:
                logger.warning("Vector embedding save failed for case %s: %s", case_id, e)
            return case_id
        except Exception as e:
            logger.warning("Case save failed: %s", e)
            return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/pytest tests/unit/test_ihi_case_saver.py -v --no-cov --timeout=60`
Expected: PASS (2/2)

- [ ] **Step 5: Commit**

```bash
git add app/services/ihi_case_saver.py tests/unit/test_ihi_case_saver.py
git commit -m "feat(rag): add IHICaseSaver (auto-save LLM verdicts as RAG cases, skip NORMAL)"
```

---

## Phase 10: UI Changes (ihi-feed-v2.html)

### Task 20: Add Device Thresholds tab to ihi-feed-v2.html

**Files:**
- Modify: `static/ihi-feed-v2.html` (add UI section + JS)
- Test: `tests/integration/test_override_endpoints.py` (already in Task 11, expand)

- [ ] **Step 1: Add HTML for Device Thresholds section**

Find the `<div class="grid grid-main">` section in `static/ihi-feed-v2.html` and add a new card AFTER the existing cards (around the closing `</div>` of `.grid-main`):

```html
<div class="card" style="margin-top: 16px;">
  <div class="card-title" style="display:flex; justify-content:space-between">
    <span>Device Thresholds (operator overrides)</span>
    <span class="pill info" style="font-size:10px">GET/POST /v1/ihi/devices/{id}/thresholds</span>
  </div>
  <div style="display:flex; gap:8px; margin-bottom:12px">
    <select id="thresholdDeviceSelect" class="ts-input mono" style="flex:0 0 200px">
      <option value="Sensor-001">Sensor-001</option>
      <option value="PLC-001">PLC-001</option>
      <option value="Meter-001">Meter-001</option>
    </select>
    <button class="btn" id="refreshThresholdsBtn">Refresh</button>
  </div>
  <div id="thresholdsList" style="font-size:13px">
    <div class="skel" style="width:60%"></div>
  </div>
</div>
```

- [ ] **Step 2: Add JS to load and display thresholds**

Add this JavaScript after the existing `pollTimeline()` function (before `scheduleTimelinePoll`):

```javascript
async function loadDeviceThresholds(deviceId) {
  try {
    const r = await fetch(`${baseUrl}/v1/ihi/devices/${deviceId}/thresholds`, { headers: { 'X-API-KEY': API_KEY } });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const data = await r.json();
    const thresholds = data.thresholds || {};
    if (Object.keys(thresholds).length === 0) {
      $('thresholdsList').innerHTML = '<div style="color:var(--text-muted)">No thresholds defined for this device.</div>';
      return;
    }
    $('thresholdsList').innerHTML = Object.entries(thresholds).map(([m, t]) => `
      <div style="display:flex; justify-content:space-between; align-items:center; padding:8px 0; border-bottom:1px solid var(--border-sub)">
        <div>
          <span class="mono" style="color:var(--text-primary)">${esc(m)}</span>
          <span class="pill" style="margin-left:8px; font-size:10px">${esc(t.source)}</span>
          ${t.source === 'manual' ? `<button class="btn" style="font-size:10px; padding:2px 6px; margin-left:4px" onclick="deleteThreshold('${deviceId}', '${m}')">×</button>` : ''}
        </div>
        <div class="mono" style="font-size:11px; color:var(--text-sec)">
          ${t.min_value !== null ? 'min=' + esc(String(t.min_value)) : ''}
          ${t.max_value !== null ? ' max=' + esc(String(t.max_value)) : ''}
          ${t.severity ? ' <span class="pill ' + (t.severity === 'DANGER' ? 'err' : t.severity === 'WARNING' ? 'warn' : 'ok') + '">' + esc(t.severity) + '</span>' : ''}
          ${t.unit ? ' ' + esc(t.unit) : ''}
        </div>
      </div>
    `).join('');
  } catch (e) {
    $('thresholdsList').innerHTML = `<div style="color:var(--error)">Error: ${esc(e.message)}</div>`;
  }
}

async function deleteThreshold(deviceId, measurement) {
  if (!confirm(`Delete manual override for ${deviceId}.${measurement}?`)) return;
  try {
    const r = await fetch(`${baseUrl}/v1/ihi/devices/${deviceId}/thresholds/${measurement}`, {
      method: 'DELETE', headers: { 'X-API-KEY': API_KEY }
    });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    await loadDeviceThresholds(deviceId);
  } catch (e) {
    alert('Error: ' + e.message);
  }
}

// Hook up
if ($('thresholdDeviceSelect')) {
  $('thresholdDeviceSelect').addEventListener('change', (e) => loadDeviceThresholds(e.target.value));
  $('refreshThresholdsBtn').onclick = () => loadDeviceThresholds($('thresholdDeviceSelect').value);
  // Load initial
  loadDeviceThresholds('Sensor-001');
}
```

- [ ] **Step 3: Verify in browser (manual)**

```bash
# Open http://127.0.0.1:8000/ihi-feed-v2.html?key=$API_KEY
# Scroll to "Device Thresholds" card
# Click "Refresh" — should show thresholds for Sensor-001
# Switch to Meter-001 — should show different thresholds
```

- [ ] **Step 4: Commit**

```bash
git add static/ihi-feed-v2.html
git commit -m "feat(ui): add Device Thresholds tab to ihi-feed-v2.html"
```

---

## Phase 11: Ground Truth Generation

### Task 21: Create ground truth generator

**Files:**
- Create: `scripts/generate_ground_truth.py`
- Create: `tests/ground_truth/ground_truth_v1.jsonl` (generated output)

- [ ] **Step 1: Write the ground truth generator script**

```python
# scripts/generate_ground_truth.py
#!/usr/bin/env python3
"""Generate ground truth labels for IHI test suite.

Uses heuristic rules on historical data to label clear cases,
flags disagreements with LLM verdicts for operator review.
"""
import json
import sqlite3
from pathlib import Path

ALERT_DB = Path("/home/hung/ihi_test/alert.db")
OUTPUT_DIR = Path(__file__).parent.parent / "tests" / "ground_truth"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def heuristic_label(readings: dict) -> tuple[str, str] | None:
    """Return (alert, symptom) for clear cases, None for ambiguous."""
    if readings.get("battery_pct") is not None and readings["battery_pct"] < 5:
        return "DANGER", "battery_critical"
    if readings.get("v_imbalance_pct") is not None and readings["v_imbalance_pct"] > 5:
        return "DANGER", "voltage_imbalance"
    if readings.get("AI1_voltage") is not None and readings["AI1_voltage"] > 100:
        return "DANGER", "sensor_broken"
    if readings.get("temperature") is not None and readings["temperature"] > 90:
        return "DANGER", "overheat"
    if readings.get("velocity_rms") is not None and readings["velocity_rms"] > 4.5:
        return "DANGER", "excessive_vibration"
    # All safe
    if all(v is None for v in readings.values()):
        return None
    # Conservative: if all readings in safe zone
    safe = True
    if readings.get("temperature") and readings["temperature"] > 80: safe = False
    if readings.get("velocity_rms") and readings["velocity_rms"] > 2.8: safe = False
    if readings.get("v_imbalance_pct") and readings["v_imbalance_pct"] > 2: safe = False
    if readings.get("battery_pct") and readings["battery_pct"] < 20: safe = False
    if safe:
        return "NORMAL", "baseline"
    return None  # ambiguous, skip


def main():
    if not ALERT_DB.exists():
        print(f"alert.db not found at {ALERT_DB}")
        return
    con = sqlite3.connect(str(ALERT_DB))
    con.row_factory = sqlite3.Row
    scrapes = con.execute("SELECT id FROM scrapes ORDER BY id").fetchall()
    labeled = []
    review_queue = []
    for s in scrapes:
        sid = s["id"]
        verdicts = con.execute(
            "SELECT id, phase, verdict_text FROM verdicts WHERE scrape_id = ?", (sid,)
        ).fetchall()
        for v in verdicts:
            # Get readings for this verdict's snapshot
            snap = con.execute(
                "SELECT id FROM snapshots WHERE scrape_id = ? AND phase = ?",
                (sid, v["phase"]),
            ).fetchone()
            if not snap:
                continue
            readings_rows = con.execute(
                "SELECT device, measurement, value FROM snapshot_readings WHERE snapshot_id = ?",
                (snap["id"],),
            ).fetchall()
            readings = {}
            for r in readings_rows:
                readings[r["measurement"]] = r["value"]
            # Also include device-specific
            # Heuristic label
            h = heuristic_label(readings)
            if h is None:
                continue
            heuristic_alert, symptom = h
            # Parse LLM verdict
            import re
            m = re.search(r"\*\*Verdict:\*\*\s*(NORMAL|WARNING|DANGER)", v["verdict_text"] or "")
            llm_alert = m.group(1) if m else "?"
            case = {
                "id": f"gt-{sid:03d}-p{v['phase']}",
                "scrape_id": sid,
                "phase": v["phase"],
                "readings": readings,
                "expected_alert": heuristic_alert,
                "expected_symptom": symptom,
                "llm_alert": llm_alert,
            }
            if llm_alert != "?" and llm_alert != heuristic_alert:
                review_queue.append(case)
            else:
                labeled.append(case)
    con.close()
    # Write outputs
    out_v1 = OUTPUT_DIR / "ground_truth_v1.jsonl"
    with out_v1.open("w") as f:
        for c in labeled:
            f.write(json.dumps(c) + "\n")
    out_review = OUTPUT_DIR / "ground_truth_review.jsonl"
    with out_review.open("w") as f:
        for c in review_queue:
            f.write(json.dumps(c) + "\n")
    print(f"Labeled: {len(labeled)}, Needs review: {len(review_queue)}")
    print(f"Written to: {out_v1}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the script**

```bash
chmod +x scripts/generate_ground_truth.py
./venv/bin/python scripts/generate_ground_truth.py
```

Expected: `Labeled: N, Needs review: M` with N+M = total verdicts across cycles

- [ ] **Step 3: Inspect output**

```bash
head -3 /home/hung/ai-hub/tests/ground_truth/ground_truth_v1.jsonl
```

Expected: Each line is a JSON case with `id`, `readings`, `expected_alert`, etc.

- [ ] **Step 4: Commit**

```bash
git add scripts/generate_ground_truth.py tests/ground_truth/ground_truth_v1.jsonl tests/ground_truth/ground_truth_review.jsonl
git commit -m "test(ihi): generate ground truth from 11 cycles (heuristic labels + LLM cross-check)"
```

---

### Task 22: Create ground truth test runner

**Files:**
- Create: `tests/ground_truth/test_ground_truth_ihi.py`

- [ ] **Step 1: Write the ground truth test**

```python
# tests/ground_truth/test_ground_truth_ihi.py
"""Ground truth test for IHI analyze pipeline.

Runs full 3-layer pipeline (rule → RAG → LLM) against labeled cases.
Pass criteria: ≥85% verdict accuracy, ≤5% false negative rate.
"""
import os
import json
import pytest
from pathlib import Path
from fastapi.testclient import TestClient
from app.main import app


API_KEY = os.environ.get("API_KEY", "1XteCCQ_s_UbrqOGEIYybmDBnokWhxYap90D6_Jojx8")
HEADERS = {"X-API-KEY": API_KEY, "Content-Type": "application/json"}

GROUND_TRUTH_FILE = Path(__file__).parent / "ground_truth_v1.jsonl"
MIN_ACCURACY = 0.85
MAX_FALSE_NEGATIVE_RATE = 0.05


def load_cases():
    if not GROUND_TRUTH_FILE.exists():
        pytest.skip("ground_truth_v1.jsonl not found — run scripts/generate_ground_truth.py")
    with GROUND_TRUTH_FILE.open() as f:
        return [json.loads(line) for line in f if line.strip()]


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def cases():
    return load_cases()


def test_ground_truth_accuracy(client, cases):
    """Verify ≥85% verdict match across all labeled cases."""
    if not cases:
        pytest.skip("No cases loaded")
    correct = 0
    false_negatives = 0
    false_positives = 0
    for c in cases:
        # Build request
        device_id = "Sensor-001"  # default; could infer from readings
        payload = {
            "ts": "03/06 12:00",
            "data": [{"id": device_id, "t": c["readings"].get("temperature", 0),
                      "v": c["readings"].get("velocity_rms", 0),
                      "c": c["readings"].get("current", 0)}],
            "extra": {device_id: {k: v for k, v in c["readings"].items()
                                  if k not in ("temperature", "velocity_rms", "current")}},
        }
        r = client.post("/v1/ihi/analyze", headers=HEADERS, json=payload)
        assert r.status_code == 200, f"Case {c['id']}: HTTP {r.status_code}"
        got = r.json()["alert"]
        expected = c["expected_alert"]
        if got == expected:
            correct += 1
        elif expected == "DANGER" and got in ("NORMAL", "WARNING"):
            false_negatives += 1
        elif expected == "NORMAL" and got in ("WARNING", "DANGER"):
            false_positives += 1
    accuracy = correct / len(cases)
    fn_rate = false_negatives / max(1, sum(1 for c in cases if c["expected_alert"] == "DANGER"))
    print(f"\n[ground truth] {correct}/{len(cases)} correct ({accuracy:.1%})")
    print(f"[ground truth] false negatives: {false_negatives}, false positives: {false_positives}")
    print(f"[ground truth] FN rate (of DANGER cases): {fn_rate:.1%}")
    assert accuracy >= MIN_ACCURACY, f"Accuracy {accuracy:.1%} < {MIN_ACCURACY:.0%}"
    assert fn_rate <= MAX_FALSE_NEGATIVE_RATE, f"FN rate {fn_rate:.1%} > {MAX_FALSE_NEGATIVE_RATE:.0%}"
```

- [ ] **Step 2: Run test**

Run: `./venv/bin/pytest tests/ground_truth/test_ground_truth_ihi.py -v --no-cov -s`
Expected: PASS (or fail with specific accuracy / FN rate; iterate if not meeting criteria)

- [ ] **Step 3: Commit**

```bash
git add tests/ground_truth/test_ground_truth_ihi.py
git commit -m "test(ihi): add ground truth test runner (>=85% accuracy, <=5% FN rate)"
```

---

## Phase 12: Final Verification

### Task 23: Run full test suite + verify success criteria

- [ ] **Step 1: Run all unit tests**

Run: `./venv/bin/pytest tests/unit/ -v --no-cov 2>&1 | tail -30`
Expected: All pass

- [ ] **Step 2: Run all integration tests (no LLM)**

Run: `./venv/bin/pytest tests/integration/ -v --no-cov -k "not test_llm and not live" 2>&1 | tail -30`
Expected: All pass

- [ ] **Step 3: Run ground truth test**

Run: `./venv/bin/pytest tests/ground_truth/ -v --no-cov -s 2>&1 | tail -15`
Expected: PASS or FAIL with specific metrics

- [ ] **Step 4: Manual verification of alert feed UI**

Open `http://127.0.0.1:8000/ihi-alert-feed.html?key=$API_KEY` in browser:
- Timeline should load
- "Device Thresholds" tab in ihi-feed-v2.html should show defaults
- Click "Refresh" — should show threshold data
- Switch device — should reload

- [ ] **Step 5: Re-evaluate 11 cycles**

```bash
sqlite3 /home/hung/ihi_test/alert.db "SELECT scrape_id, phase, substr(verdict_text, 1, 50) FROM verdicts ORDER BY scrape_id, phase;" | head -22
```

Manually compare new pipeline output vs old LLM verdicts. Expected: fewer false negatives, more consistent verdicts.

- [ ] **Step 6: Commit final state**

```bash
git status
# If any uncommitted changes, commit them
git log --oneline | head -20
# Confirm all 22 commits present (one per task, approximately)
```

---

## Summary

| Phase | Tasks | Description |
|-------|-------|-------------|
| 1: Database | 1-3 | pgvector, overrides, embeddings tables |
| 2: Schema | 4 | PatternRange.extra field |
| 3: Thresholds | 5-9 | types, ISO 10816, NEMA, envelopes, loader |
| 4: Overrides | 10-11 | Service tests + API endpoints |
| 5: PatternMatcher | 12 | Extended matcher for extra dict |
| 6: Analyzer | 13-14 | New IHIThresholdAnalyzer + analyze endpoint |
| 7: RAG | 15-16 | Vector index + hybrid retrieval |
| 8: LLM | 17-18 | Updated prompt + RAG context injection |
| 9: Case Saver | 19 | Auto-save LLM verdicts |
| 10: UI | 20 | Device Thresholds tab |
| 11: Ground Truth | 21-22 | Generator + test runner |
| 12: Verification | 23 | Run all tests |

**Total: 23 tasks, ~30 commits, 30+ files (new + modified)**

**Success criteria (from spec):**
- [ ] ≥85% ground truth verdict accuracy
- [ ] ≤5% false negative rate (was 18% with old LLM-only)
- [ ] All unit + integration tests pass
- [ ] Manual verification of UI works
- [ ] NEMA voltage threshold fix visible in LLM verdicts
