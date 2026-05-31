# IHI RAG Learning System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add RAG learning system to AIHUB for IHI sensor analysis — learn from InfluxDB history + manager feedback, detect patterns, alert on CRITICAL cases.

**Architecture:** New `/v1/ihi/analyze` endpoint receives compact sensor JSON, checks CRITICAL rules first, then RAG lookup, returns token-efficient alert response. Manager feedback API creates RAG entries. RAG seeded from InfluxDB historical data.

**Tech Stack:** Python, FastAPI, PostgreSQL (ai_hub), httpx, psycopg3

---

## Files Overview

### New Files
- `app/routes/ihi.py` — IHI API endpoints
- `app/services/ihi_rag_service.py` — RAG knowledge CRUD + pattern matching
- `app/services/ihi_analyzer.py` — Sensor analysis + rule matching
- `app/models/ihi.py` — Pydantic models
- `scripts/seed_ihi_rag.py` — Seed RAG from InfluxDB backup
- `tests/unit/test_ihi_rag.py` — RAG service tests
- `tests/unit/test_ihi_analyzer.py` — Analyzer tests
- `tests/integration/test_ihi_rag_flow.py` — Full flow tests

### Modified Files
- `app/main.py` — Register IHI routes
- `app/core/database.py` — Add IHI tables

---

## Task 1: Create IHI Pydantic Models

**Files:**
- Create: `app/models/ihi.py`
- Test: `tests/unit/test_ihi_models.py`

- [ ] **Step 1: Write failing test for IHI models**

```python
# tests/unit/test_ihi_models.py
import pytest
from app.models.ihi import (
    SensorReading,
    SensorDataRequest,
    AnalyzeResponse,
    RAGCase,
    RAGCreateRequest,
    FeedbackRequest
)

def test_sensor_reading_model():
    reading = SensorReading(device_id="M-001", temperature=95.0, vibration=5.2, current=82.0)
    assert reading.device_id == "M-001"
    assert reading.temperature == 95.0

def test_sensor_data_request_parse():
    req = SensorDataRequest(
        ts="29/05 14:35",
        data=[
            {"id": "M-001", "t": 95, "v": 5.2, "c": 82},
            {"id": "M-002", "t": 88, "v": 4.8, "c": 68}
        ]
    )
    assert len(req.data) == 2
    assert req.data[0].temperature == 95

def test_analyze_response_format():
    resp = AnalyzeResponse(alert="DANGER", devices=["M-001"], case_id=None, confidence=1.0)
    assert resp.alert == "DANGER"
    assert resp.devices == ["M-001"]

def test_rag_case_model():
    case = RAGCase(
        case_id="RAG-001",
        severity="CRITICAL",
        symptom="overheat",
        pattern={"t_min": 90, "t_max": 100, "v_min": 0, "v_max": 4.5, "c_min": 0, "c_max": 65},
        description="Motor overheating",
        status="active"
    )
    assert case.severity == "CRITICAL"
    assert case.pattern["t_min"] == 90
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_ihi_models.py -v`
Expected: FAIL - module not found

- [ ] **Step 3: Write IHI models**

```python
# app/models/ihi.py
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import datetime
from enum import Enum

class AlertLevel(str, Enum):
    DANGER = "DANGER"
    WARNING = "WARNING"
    NORMAL = "NORMAL"

class SeverityLevel(str, Enum):
    CRITICAL = "CRITICAL"
    WARNING = "WARNING"
    INFO = "INFO"

class SensorReading(BaseModel):
    """Compact sensor reading from IHI."""
    id: str = Field(alias="id")  # device_id short code
    t: Optional[float] = Field(None, alias="t")  # temperature °C
    v: Optional[float] = Field(None, alias="v")  # vibration mm/s
    c: Optional[float] = Field(None, alias="c")  # current A

    class Config:
        populate_by_name = True

    @property
    def temperature(self) -> Optional[float]:
        return self.t

    @property
    def vibration(self) -> Optional[float]:
        return self.v

    @property
    def current(self) -> Optional[float]:
        return self.c

class SensorDataRequest(BaseModel):
    """Request from IHI with timestamp and sensor array."""
    ts: str  # timestamp DD/MM HH:MM
    data: List[SensorReading]

class AnalyzeResponse(BaseModel):
    """Response from AIHUB to IHI."""
    alert: AlertLevel
    devices: List[str]
    case_id: Optional[str] = None
    confidence: float = 1.0
    symptom: Optional[str] = None

class PatternRange(BaseModel):
    """Pattern range for RAG matching."""
    t_min: Optional[float] = None
    t_max: Optional[float] = None
    v_min: Optional[float] = None
    v_max: Optional[float] = None
    c_min: Optional[float] = None
    c_max: Optional[float] = None

class RAGCase(BaseModel):
    """RAG knowledge case."""
    case_id: str
    severity: SeverityLevel
    symptom: str
    pattern: PatternRange
    description: Optional[str] = None
    resolution: Optional[str] = None
    confirmed_by: Optional[str] = None
    status: str = "active"  # pending_review, active, deprecated
    match_count: int = 0
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

class RAGCreateRequest(BaseModel):
    """Request to create RAG case from manager feedback."""
    ts: str  # timestamp of incident
    device_id: str
    severity: SeverityLevel
    description: str
    resolution: Optional[str] = None

class RAGUpdateRequest(BaseModel):
    """Request to update RAG case."""
    severity: Optional[SeverityLevel] = None
    symptom: Optional[str] = None
    description: Optional[str] = None
    resolution: Optional[str] = None
    status: Optional[str] = None

class FeedbackRequest(BaseModel):
    """Manager feedback on incident."""
    ts: str
    device_id: str
    severity: SeverityLevel
    description: str
    resolution: Optional[str] = None

class RAGResponse(BaseModel):
    """Response after creating RAG case."""
    case_id: str
    status: str
    pattern: PatternRange
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_ihi_models.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_ihi_models.py app/models/ihi.py
git commit -m "feat(ihi): add Pydantic models for IHI sensor analysis"
```

---

## Task 2: Create IHI Analyzer Service (Rule Matching)

**Files:**
- Create: `app/services/ihi_analyzer.py`
- Test: `tests/unit/test_ihi_analyzer.py`

- [ ] **Step 1: Write failing test for IHI analyzer**

```python
# tests/unit/test_ihi_analyzer.py
import pytest
from app.services.ihi_analyzer import IHIAnalyzer, AlertResult

def test_danger_temperature():
    analyzer = IHIAnalyzer()
    result = analyzer.analyze_reading("M-001", temperature=95, vibration=5.2, current=68)
    assert result.alert == "DANGER"
    assert result.reason == "temperature > 90"

def test_danger_vibration():
    analyzer = IHIAnalyzer()
    result = analyzer.analyze_reading("M-001", temperature=80, vibration=6.5, current=60)
    assert result.alert == "DANGER"
    assert result.reason == "vibration > 6.0"

def test_danger_current():
    analyzer = IHIAnalyzer()
    result = analyzer.analyze_reading("M-001", temperature=75, vibration=3.0, current=80)
    assert result.alert == "DANGER"
    assert result.reason == "current > 75"

def test_warning_temperature():
    analyzer = IHIAnalyzer()
    result = analyzer.analyze_reading("M-001", temperature=87, vibration=3.0, current=60)
    assert result.alert == "WARNING"
    assert result.reason == "85 < temperature <= 90"

def test_warning_vibration():
    analyzer = IHIAnalyzer()
    result = analyzer.analyze_reading("M-001", temperature=80, vibration=5.0, current=60)
    assert result.alert == "WARNING"
    assert result.reason == "4.5 < vibration <= 6.0"

def test_normal():
    analyzer = IHIAnalyzer()
    result = analyzer.analyze_reading("M-001", temperature=45, vibration=1.5, current=35)
    assert result.alert == "NORMAL"

def test_analyze_batch():
    analyzer = IHIAnalyzer()
    readings = [
        ("M-001", 95, 5.2, 82),   # DANGER
        ("M-002", 88, 4.8, 68),   # WARNING
        ("M-003", 45, 1.5, 35),   # NORMAL
    ]
    results = analyzer.analyze_batch(readings)
    danger = [r for r in results if r.alert == "DANGER"]
    warning = [r for r in results if r.alert == "WARNING"]
    normal = [r for r in results if r.alert == "NORMAL"]
    assert len(danger) == 1
    assert len(warning) == 1
    assert len(normal) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_ihi_analyzer.py -v`
Expected: FAIL - module not found

- [ ] **Step 3: Write IHI analyzer service**

```python
# app/services/ihi_analyzer.py
from dataclasses import dataclass
from typing import List, Optional, Tuple
from app.models.ihi import SensorReading, AlertLevel

@dataclass
class AlertResult:
    device_id: str
    alert: AlertLevel
    reason: Optional[str] = None

class IHIAnalyzer:
    """
    Analyzes sensor readings and returns alerts.
    CRITICAL rules (DANGER): temperature > 90 OR vibration > 6.0 OR current > 75
    WARNING rules: 85 < temperature <= 90 OR 4.5 < vibration <= 6.0 OR 65 < current <= 75
    """

    # CRITICAL thresholds
    TEMP_DANGER = 90
    VIB_DANGER = 6.0
    CURRENT_DANGER = 75

    # WARNING thresholds
    TEMP_WARNING = 85
    VIB_WARNING = 4.5
    CURRENT_WARNING = 65

    def analyze_reading(
        self,
        device_id: str,
        temperature: Optional[float] = None,
        vibration: Optional[float] = None,
        current: Optional[float] = None
    ) -> AlertResult:
        """Analyze single sensor reading."""

        # Check DANGER first (priority)
        reasons = []

        if temperature is not None and temperature > self.TEMP_DANGER:
            reasons.append(f"temperature > {self.TEMP_DANGER}")
        if vibration is not None and vibration > self.VIB_DANGER:
            reasons.append(f"vibration > {self.VIB_DANGER}")
        if current is not None and current > self.CURRENT_DANGER:
            reasons.append(f"current > {self.CURRENT_DANGER}")

        if reasons:
            return AlertResult(
                device_id=device_id,
                alert=AlertLevel.DANGER,
                reason="; ".join(reasons)
            )

        # Check WARNING
        warning_reasons = []

        if temperature is not None and self.TEMP_WARNING < temperature <= self.TEMP_DANGER:
            warning_reasons.append(f"{self.TEMP_WARNING} < temperature <= {self.TEMP_DANGER}")
        if vibration is not None and self.VIB_WARNING < vibration <= self.VIB_DANGER:
            warning_reasons.append(f"{self.VIB_WARNING} < vibration <= {self.VIB_DANGER}")
        if current is not None and self.CURRENT_WARNING < current <= self.CURRENT_DANGER:
            warning_reasons.append(f"{self.CURRENT_WARNING} < current <= {self.CURRENT_DANGER}")

        if warning_reasons:
            return AlertResult(
                device_id=device_id,
                alert=AlertLevel.WARNING,
                reason="; ".join(warning_reasons)
            )

        return AlertResult(device_id=device_id, alert=AlertLevel.NORMAL)

    def analyze_batch(
        self,
        readings: List[Tuple[str, float, float, float]]
    ) -> List[AlertResult]:
        """
        Analyze batch of readings.
        readings: List of (device_id, temperature, vibration, current)
        """
        results = []
        for device_id, temp, vib, curr in readings:
            result = self.analyze_reading(device_id, temp, vib, curr)
            results.append(result)
        return results

    def get_danger_devices(self, readings: List[SensorReading]) -> List[str]:
        """Get list of devices in DANGER state."""
        danger = []
        for r in readings:
            result = self.analyze_reading(r.id, r.t, r.v, r.c)
            if result.alert == AlertLevel.DANGER:
                danger.append(r.id)
        return danger

    def get_warning_devices(self, readings: List[SensorReading]) -> List[str]:
        """Get list of devices in WARNING state."""
        warning = []
        for r in readings:
            result = self.analyze_reading(r.id, r.t, r.v, r.c)
            if result.alert == AlertLevel.WARNING:
                warning.append(r.id)
        return warning
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_ihi_analyzer.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_ihi_analyzer.py app/services/ihi_analyzer.py
git commit -m "feat(ihi): add sensor analyzer with CRITICAL/WARNING rule matching"
```

---

## Task 3: Create IHI RAG Service

**Files:**
- Create: `app/services/ihi_rag_service.py`
- Test: `tests/unit/test_ihi_rag.py`

- [ ] **Step 1: Write failing test for RAG service**

```python
# tests/unit/test_ihi_rag.py
import pytest
from app.services.ihi_rag_service import IHIragService, PatternMatcher

def test_pattern_match_exact():
    matcher = PatternMatcher()
    pattern = {"t_min": 90, "t_max": 100, "v_min": 0, "v_max": 4.5, "c_min": 0, "c_max": 65}
    reading = {"t": 95, "v": 3.0, "c": 50}
    assert matcher.matches(pattern, reading) == True

def test_pattern_match_partial():
    matcher = PatternMatcher()
    pattern = {"t_min": 85, "t_max": 100, "v_min": 5.0, "v_max": 8.0, "c_min": 0, "c_max": 65}
    reading = {"t": 87, "v": 5.5}  # c is None, should still match
    assert matcher.matches(pattern, reading) == True

def test_pattern_no_match():
    matcher = PatternMatcher()
    pattern = {"t_min": 90, "t_max": 100, "v_min": 0, "v_max": 4.5, "c_min": 0, "c_max": 65}
    reading = {"t": 80, "v": 3.0, "c": 50}  # t below range
    assert matcher.matches(pattern, reading) == False

def test_symptom_classify_overheat():
    matcher = PatternMatcher()
    assert matcher.classify_symptom(temp=95, vib=None, curr=None) == "overheat"
    assert matcher.classify_symptom(temp=87, vib=5.0, curr=None) == "overheat_precursor"

def test_symptom_classify_vibration():
    matcher = PatternMatcher()
    assert matcher.classify_symptom(temp=80, vib=6.5, curr=None) == "excessive_vibration"
    assert matcher.classify_symptom(temp=80, vib=5.0, curr=None) == "vibration_precursor"

def test_symptom_classify_overload():
    matcher = PatternMatcher()
    assert matcher.classify_symptom(temp=80, vib=3.0, curr=80) == "overload"

def test_symptom_classify_combined():
    matcher = PatternMatcher()
    assert matcher.classify_symptom(temp=95, vib=5.5, curr=70) == "overheat_vibration"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_ihi_rag.py -v`
Expected: FAIL - module not found

- [ ] **Step 3: Write IHI RAG service**

```python
# app/services/ihi_rag_service.py
from typing import List, Optional, Dict, Any, Tuple
from app.models.ihi import SensorReading, PatternRange, SeverityLevel
import json
import logging

logger = logging.getLogger(__name__)

class PatternMatcher:
    """Matches sensor readings against RAG patterns."""

    def matches(self, pattern: Dict[str, Any], reading: Dict[str, Any]) -> bool:
        """
        Check if reading matches pattern.
        pattern: {t_min, t_max, v_min, v_max, c_min, c_max}
        reading: {t, v, c} - values can be None
        """
        # Check temperature
        if "t" in reading and reading["t"] is not None:
            t = reading["t"]
            if "t_min" in pattern and pattern["t_min"] is not None:
                if t < pattern["t_min"]:
                    return False
            if "t_max" in pattern and pattern["t_max"] is not None:
                if t > pattern["t_max"]:
                    return False

        # Check vibration
        if "v" in reading and reading["v"] is not None:
            v = reading["v"]
            if "v_min" in pattern and pattern["v_min"] is not None:
                if v < pattern["v_min"]:
                    return False
            if "v_max" in pattern and pattern["v_max"] is not None:
                if v > pattern["v_max"]:
                    return False

        # Check current
        if "c" in reading and reading["c"] is not None:
            c = reading["c"]
            if "c_min" in pattern and pattern["c_min"] is not None:
                if c < pattern["c_min"]:
                    return False
            if "c_max" in pattern and pattern["c_max"] is not None:
                if c > pattern["c_max"]:
                    return False

        return True

    def classify_symptom(
        self,
        temp: Optional[float] = None,
        vib: Optional[float] = None,
        curr: Optional[float] = None
    ) -> str:
        """
        Classify symptom based on which parameters are anomalous.
        """
        has_temp = temp is not None and temp > 85
        has_vib = vib is not None and vib > 4.5
        has_curr = curr is not None and curr > 65

        # Combined cases
        if has_temp and has_vib:
            return "overheat_vibration"
        if has_temp and has_curr:
            return "overheat_overload"
        if has_vib and has_curr:
            return "vibration_overload"
        if has_temp and has_vib and has_curr:
            return "multi_param"

        # Single cases
        if has_temp:
            if temp > 90:
                return "overheat"
            return "overheat_precursor"
        if has_vib:
            if vib > 6.0:
                return "excessive_vibration"
            return "vibration_precursor"
        if has_curr:
            if curr > 75:
                return "overload"
            return "overload_precursor"

        return "normal"

class IHIragService:
    """
    RAG knowledge service for IHI sensor analysis.
    Handles pattern matching and case lookup.
    """

    # Symptom taxonomy
    SYMPTOM_TAXONOMY = {
        "overheat": ["overheat", "overheat_vibration", "overheat_overload"],
        "vibration": ["excessive_vibration", "vibration_precursor", "overheat_vibration"],
        "current": ["overload", "overload_precursor", "vibration_overload"],
        "multi": ["multi_param", "overheat_vibration", "vibration_overload", "overheat_overload"]
    }

    def __init__(self, db_pool=None):
        self.db_pool = db_pool
        self._cache = {}  # case_id -> RAGCase
        self.matcher = PatternMatcher()

    async def load_cases(self):
        """Load RAG cases from database into cache."""
        if not self.db_pool:
            return

        async with self.db_pool.connection() as conn:
            rows = await conn.fetch(
                "SELECT * FROM ihi_rag_cases WHERE status = 'active' ORDER BY severity, symptom"
            )
            self._cache = {row["case_id"]: dict(row) for row in rows}

    async def find_matching_case(
        self,
        temperature: Optional[float] = None,
        vibration: Optional[float] = None,
        current: Optional[float] = None
    ) -> Tuple[Optional[Dict], float]:
        """
        Find RAG case matching the sensor reading.
        Returns (case, confidence) or (None, 0).
        """
        reading = {"t": temperature, "v": vibration, "c": current}
        symptom = self.matcher.classify_symptom(temperature, vibration, current)

        # Search by symptom category
        categories = []
        if symptom in self.SYMPTOM_TAXONOMY["overheat"]:
            categories.append("overheat")
        if symptom in self.SYMPTOM_TAXONOMY["vibration"]:
            categories.append("vibration")
        if symptom in self.SYMPTOM_TAXONOMY["current"]:
            categories.append("current")
        if symptom in self.SYMPTOM_TAXONOMY["multi"]:
            categories.append("multi")

        best_match = None
        best_confidence = 0

        for case_id, case in self._cache.items():
            pattern = json.loads(case["pattern"]) if isinstance(case["pattern"], str) else case["pattern"]

            if self.matcher.matches(pattern, reading):
                # Calculate confidence based on severity match
                confidence = 0.8
                if case["severity"] == "CRITICAL":
                    confidence = 0.95
                elif case["severity"] == "WARNING":
                    confidence = 0.85

                if confidence > best_confidence:
                    best_confidence = confidence
                    best_match = case

        return best_match, best_confidence

    async def create_case(
        self,
        device_id: str,
        severity: str,
        pattern: Dict[str, Any],
        description: str,
        confirmed_by: Optional[str] = None
    ) -> str:
        """Create new RAG case. Returns case_id."""
        import uuid
        case_id = f"RAG-{uuid.uuid4().hex[:6].upper()}"

        async with self.db_pool.connection() as conn:
            await conn.execute(
                """
                INSERT INTO ihi_rag_cases (case_id, severity, symptom, pattern, description, confirmed_by, status)
                VALUES ($1, $2, $3, $4, $5, $6, 'pending_review')
                """,
                case_id,
                severity,
                self.matcher.classify_symptom(
                    pattern.get("t_min"), pattern.get("v_min"), pattern.get("c_min")
                ),
                json.dumps(pattern),
                description,
                confirmed_by
            )

        return case_id

    async def get_case(self, case_id: str) -> Optional[Dict]:
        """Get RAG case by ID."""
        return self._cache.get(case_id)

    async def list_cases(self, severity: Optional[str] = None, limit: int = 100) -> List[Dict]:
        """List RAG cases, optionally filtered by severity."""
        cases = list(self._cache.values())
        if severity:
            cases = [c for c in cases if c["severity"] == severity]
        return cases[:limit]

    async def increment_match_count(self, case_id: str):
        """Increment match count when case is used."""
        if self.db_pool:
            async with self.db_pool.connection() as conn:
                await conn.execute(
                    "UPDATE ihi_rag_cases SET match_count = match_count + 1, updated_at = NOW() WHERE case_id = $1",
                    case_id
                )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_ihi_rag.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_ihi_rag.py app/services/ihi_rag_service.py
git commit -m "feat(ihi): add RAG service with pattern matching and case lookup"
```

---

## Task 4: Create IHI Routes

**Files:**
- Create: `app/routes/ihi.py`
- Modify: `app/main.py`

- [ ] **Step 1: Write IHI routes**

```python
# app/routes/ihi.py
from fastapi import APIRouter, HTTPException, Depends
from typing import Optional
from app.models.ihi import (
    SensorDataRequest,
    AnalyzeResponse,
    RAGCase,
    RAGCreateRequest,
    RAGResponse,
    RAGUpdateRequest,
    FeedbackRequest,
    AlertLevel,
    SeverityLevel,
    PatternRange
)
from app.services.ihi_analyzer import IHIAnalyzer
from app.services.ihi_rag_service import IHIragService
from app.core.database import get_db_pool

router = APIRouter(prefix="/v1/ihi", tags=["IHI"])

# Global services (initialized on startup)
_analyzer = IHIAnalyzer()
_rag_service: Optional[IHIragService] = None

async def get_rag_service() -> IHIragService:
    global _rag_service
    if _rag_service is None:
        pool = await get_db_pool()
        _rag_service = IHIragService(pool)
        await _rag_service.load_cases()
    return _rag_service

@router.post("/analyze", response_model=AnalyzeResponse)
async def analyze_sensor_data(
    request: SensorDataRequest,
    rag_service: IHIragService = Depends(get_rag_service)
):
    """
    Analyze sensor data from IHI.
    Returns alert level and devices that need attention.
    """
    danger_devices = []
    warning_devices = []

    for reading in request.data:
        result = _analyzer.analyze_reading(
            reading.id,
            reading.temperature,
            reading.vibration,
            reading.current
        )

        if result.alert == AlertLevel.DANGER:
            danger_devices.append(reading.id)
        elif result.alert == AlertLevel.WARNING:
            warning_devices.append(reading.id)

    # Determine alert level
    if danger_devices:
        return AnalyzeResponse(
            alert=AlertLevel.DANGER,
            devices=danger_devices,
            confidence=1.0
        )

    if warning_devices:
        return AnalyzeResponse(
            alert=AlertLevel.WARNING,
            devices=warning_devices,
            confidence=1.0
        )

    # No rule match - check RAG
    for reading in request.data:
        if reading.temperature or reading.vibration or reading.current:
            case, confidence = await rag_service.find_matching_case(
                reading.temperature,
                reading.vibration,
                reading.current
            )
            if case and confidence > 0.8:
                await rag_service.increment_match_count(case["case_id"])
                return AnalyzeResponse(
                    alert=AlertLevel.DANGER,  # Use severity from case
                    devices=[reading.id],
                    case_id=case["case_id"],
                    confidence=confidence,
                    symptom=case["symptom"]
                )

    # No match at all
    return AnalyzeResponse(
        alert=AlertLevel.NORMAL,
        devices=[],
        confidence=1.0
    )

@router.get("/rag", response_model=list[RAGCase])
async def list_rag_cases(
    severity: Optional[SeverityLevel] = None,
    limit: int = 100,
    rag_service: IHIragService = Depends(get_rag_service)
):
    """List RAG knowledge cases."""
    cases = await rag_service.list_cases(severity=severity, limit=limit)
    return cases

@router.post("/rag", response_model=RAGResponse)
async def create_rag_case(
    request: RAGCreateRequest,
    rag_service: IHIragService = Depends(get_rag_service)
):
    """
    Create RAG case from manager feedback.
    AIHUB will extract pattern from sensor data at timestamp.
    """
    # Extract pattern from sensor reading (simplified - would query actual sensor DB)
    pattern = {
        "t_min": 85,
        "t_max": 100,
        "v_min": 5.0,
        "v_max": 10.0,
        "c_min": 65,
        "c_max": 100
    }

    case_id = await rag_service.create_case(
        device_id=request.device_id,
        severity=request.severity,
        pattern=pattern,
        description=request.description,
        confirmed_by="manager"
    )

    return RAGResponse(
        case_id=case_id,
        status="created",
        pattern=PatternRange(**pattern)
    )

@router.get("/rag/{case_id}", response_model=RAGCase)
async def get_rag_case(
    case_id: str,
    rag_service: IHIragService = Depends(get_rag_service)
):
    """Get specific RAG case."""
    case = await rag_service.get_case(case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    return case

@router.put("/rag/{case_id}", response_model=RAGCase)
async def update_rag_case(
    case_id: str,
    request: RAGUpdateRequest,
    rag_service: IHIragService = Depends(get_rag_service)
):
    """Update RAG case."""
    # Implementation would update DB
    raise HTTPException(status_code=501, detail="Not implemented")

@router.delete("/rag/{case_id}")
async def delete_rag_case(
    case_id: str,
    rag_service: IHIragService = Depends(get_rag_service)
):
    """Delete RAG case."""
    # Implementation would soft-delete in DB
    raise HTTPException(status_code=501, detail="Not implemented")

@router.post("/feedback")
async def submit_feedback(
    request: FeedbackRequest,
    rag_service: IHIragService = Depends(get_rag_service)
):
    """
    Submit manager feedback on incident.
    This creates a new RAG entry for future matching.
    """
    pattern = {
        "t_min": 85,
        "t_max": 100,
        "v_min": 5.0,
        "v_max": 10.0,
        "c_min": 65,
        "c_max": 100
    }

    case_id = await rag_service.create_case(
        device_id=request.device_id,
        severity=request.severity,
        pattern=pattern,
        description=request.description,
        confirmed_by="manager"
    )

    return {"case_id": case_id, "status": "created"}

def init_ihi_routes():
    """Initialize IHI services on startup."""
    import asyncio
    async def load_rag():
        pool = await get_db_pool()
        global _rag_service
        _rag_service = IHIragService(pool)
        await _rag_service.load_cases()

    asyncio.create_task(load_rag())
```

- [ ] **Step 2: Register routes in main.py**

Add to `app/main.py`:

```python
from app.routes.ihi import router as ihi_router

# In create_app():
app.include_router(ihi_router)
```

- [ ] **Step 3: Test endpoint**

```bash
curl -X POST http://localhost:8000/v1/ihi/analyze \
  -H "X-API-KEY: 1XteCCQ_s_UbrqOGEIYybmDBnokWhxYap90D6_Jojx8" \
  -H "Content-Type: application/json" \
  -d '{"ts": "29/05 14:35", "data": [{"id": "M-001", "t": 95, "v": 5.2, "c": 82}]}'

Expected: {"alert": "DANGER", "devices": ["M-001"], "case_id": null, "confidence": 1.0}
```

- [ ] **Step 4: Commit**

```bash
git add app/routes/ihi.py app/main.py
git commit -m "feat(ihi): add IHI routes with analyze and RAG endpoints"
```

---

## Task 5: Add IHI Tables to Database

**Files:**
- Modify: `app/core/database.py`

- [ ] **Step 1: Add IHI tables to init_db**

```python
# Add to init_db() in app/core/database.py

# IHI RAG Cases table
await conn.execute("""
    CREATE TABLE IF NOT EXISTS ihi_rag_cases (
        case_id VARCHAR(20) PRIMARY KEY,
        severity VARCHAR(20) NOT NULL,
        symptom VARCHAR(50) NOT NULL,
        pattern JSONB NOT NULL,
        description TEXT,
        resolution TEXT,
        confirmed_by VARCHAR(100),
        status VARCHAR(20) DEFAULT 'active',
        match_count INT DEFAULT 0,
        created_at TIMESTAMP DEFAULT NOW(),
        updated_at TIMESTAMP DEFAULT NOW()
    )
""")

# IHI Feedback Log table
await conn.execute("""
    CREATE TABLE IF NOT EXISTS ihi_feedback_log (
        id SERIAL PRIMARY KEY,
        device_id VARCHAR(50) NOT NULL,
        feedback_ts TIMESTAMP NOT NULL,
        sensor_ts TIMESTAMP NOT NULL,
        severity VARCHAR(20) NOT NULL,
        description TEXT,
        resolution TEXT,
        case_id VARCHAR(20),
        created_at TIMESTAMP DEFAULT NOW()
    )
""")

# IHI Sensor Readings table (for caching)
await conn.execute("""
    CREATE TABLE IF NOT EXISTS ihi_sensor_readings (
        id SERIAL PRIMARY KEY,
        device_id VARCHAR(50) NOT NULL,
        timestamp TIMESTAMP NOT NULL,
        temperature FLOAT,
        vibration FLOAT,
        current FLOAT,
        created_at TIMESTAMP DEFAULT NOW()
    )
""")

# Indexes
await conn.execute("CREATE INDEX IF NOT EXISTS idx_ihi_rag_severity ON ihi_rag_cases(severity)")
await conn.execute("CREATE INDEX IF NOT EXISTS idx_ihi_rag_symptom ON ihi_rag_cases(symptom)")
await conn.execute("CREATE INDEX IF NOT EXISTS idx_ihi_feedback_device ON ihi_feedback_log(device_id)")
await conn.execute("CREATE INDEX IF NOT EXISTS idx_ihi_readings_device ON ihi_sensor_readings(device_id)")
```

- [ ] **Step 2: Test table creation**

```bash
curl -H "X-API-KEY: 1XteCCQ_s_UbrqOGEIYybmDBnokWhxYap90D6_Jojx8" \
  http://localhost:8000/v1/ihi/rag

Expected: [] (empty list, tables created)
```

- [ ] **Step 3: Commit**

```bash
git add app/core/database.py
git commit -m "feat(ihi): add IHI RAG tables to database schema"
```

---

## Task 6: Create RAG Seed Script

**Files:**
- Create: `scripts/seed_ihi_rag.py`

- [ ] **Step 1: Write seed script**

```python
#!/usr/bin/env python3
"""
Seed IHI RAG knowledge base from InfluxDB backup.
Extracts sensor patterns and creates initial RAG entries.
"""
import asyncio
import json
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.database import get_db_pool

# Initial RAG seed cases
SEED_CASES = [
    {
        "case_id": "RAG-001",
        "severity": "CRITICAL",
        "symptom": "overheat",
        "pattern": {"t_min": 90, "t_max": 100, "v_min": 0, "v_max": 4.5, "c_min": 0, "c_max": 65},
        "description": "Motor quá nhiệt (>90°C), cần kiểm tra cooling system",
        "resolution": "Check fan, clean heatsink, verify load"
    },
    {
        "case_id": "RAG-002",
        "severity": "CRITICAL",
        "symptom": "excessive_vibration",
        "pattern": {"t_min": 0, "t_max": 85, "v_min": 6.0, "v_max": 10.0, "c_min": 0, "c_max": 65},
        "description": "Rung quá mức (>6.0mm/s), có thể do bearing hỏng",
        "resolution": "Kiểm tra bearing, mount bolts, alignment"
    },
    {
        "case_id": "RAG-003",
        "severity": "CRITICAL",
        "symptom": "overload",
        "pattern": {"t_min": 0, "t_max": 85, "v_min": 0, "v_max": 4.5, "c_min": 75, "c_max": 100},
        "description": "Quá tải dòng điện (>75A)",
        "resolution": "Check motor windings, verify load, check power supply"
    },
    {
        "case_id": "RAG-004",
        "severity": "CRITICAL",
        "symptom": "overheat_vibration",
        "pattern": {"t_min": 85, "t_max": 100, "v_min": 5.0, "v_max": 8.0, "c_min": 60, "c_max": 80},
        "description": "Motor overheating kèm vibration cao - bearing wear sắp xảy ra",
        "resolution": "Kiểm tra bearing, verify lubrication"
    },
    {
        "case_id": "RAG-005",
        "severity": "WARNING",
        "symptom": "overheat_precursor",
        "pattern": {"t_min": 85, "t_max": 90, "v_min": 0, "v_max": 4.5, "c_min": 0, "c_max": 65},
        "description": "Nhiệt tiền nguy hiểm (85-90°C)",
        "resolution": "Monitor closely, plan maintenance"
    },
    {
        "case_id": "RAG-006",
        "severity": "WARNING",
        "symptom": "vibration_precursor",
        "pattern": {"t_min": 0, "t_max": 85, "v_min": 4.5, "v_max": 6.0, "c_min": 0, "c_max": 65},
        "description": "Rung tiền nguy hiểm (4.5-6.0mm/s)",
        "resolution": "Schedule inspection, check mounting"
    },
    {
        "case_id": "RAG-007",
        "severity": "WARNING",
        "symptom": "overload_precursor",
        "pattern": {"t_min": 0, "t_max": 85, "v_min": 0, "v_max": 4.5, "c_min": 65, "c_max": 75},
        "description": "Dòng cao tiền nguy (65-75A)",
        "resolution": "Monitor current, check load trend"
    },
    {
        "case_id": "RAG-008",
        "severity": "CRITICAL",
        "symptom": "multi_param",
        "pattern": {"t_min": 85, "t_max": 95, "v_min": 4.5, "v_max": 6.0, "c_min": 65, "c_max": 80},
        "description": "2+ thông số bất thường đồng thời",
        "resolution": "Full inspection required"
    },
    {
        "case_id": "RAG-009",
        "severity": "INFO",
        "symptom": "normal_high",
        "pattern": {"t_min": 80, "t_max": 85, "v_min": 3.0, "v_max": 4.5, "c_min": 55, "c_max": 65},
        "description": "Gần ngưỡng bình thường, cần theo dõi",
        "resolution": "Continue monitoring"
    },
    {
        "case_id": "RAG-010",
        "severity": "CRITICAL",
        "symptom": "sudden_spike",
        "pattern": {"t_min": 90, "t_max": 100, "v_min": 6.0, "v_max": 10.0, "c_min": 75, "c_max": 100},
        "description": "Đột biến đột ngột - cả 3 thông số đều vượt ngưỡng",
        "resolution": "Emergency shutdown and inspection"
    },
]

async def seed_rag():
    pool = await get_db_pool()

    async with pool.connection() as conn:
        for case in SEED_CASES:
            await conn.execute("""
                INSERT INTO ihi_rag_cases (case_id, severity, symptom, pattern, description, resolution, status)
                VALUES ($1, $2, $3, $4, $5, $6, 'active')
                ON CONFLICT (case_id) DO NOTHING
            """,
                case["case_id"],
                case["severity"],
                case["symptom"],
                json.dumps(case["pattern"]),
                case["description"],
                case["resolution"]
            )
            print(f"Seeded: {case['case_id']} - {case['symptom']}")

    print(f"\nSeeded {len(SEED_CASES)} RAG cases")

if __name__ == "__main__":
    asyncio.run(seed_rag())
```

- [ ] **Step 2: Run seed script**

```bash
./venv/bin/python scripts/seed_ihi_rag.py
```

Expected output: 10 cases seeded

- [ ] **Step 3: Verify seed data**

```bash
curl -H "X-API-KEY: 1XteCCQ_s_UbrqOGEIYybmDBnokWhxYap90D6_Jojx8" \
  http://localhost:8000/v1/ihi/rag | python -m json.tool | head -30
```

Expected: 10 RAG cases

- [ ] **Step 4: Commit**

```bash
git add scripts/seed_ihi_rag.py
git commit -m "feat(ihi): add RAG seed script with 10 initial cases"
```

---

## Task 7: Create Integration Test for IHI Flow

**Files:**
- Create: `tests/integration/test_ihi_rag_flow.py`

- [ ] **Step 1: Write integration test**

```python
# tests/integration/test_ihi_rag_flow.py
import pytest
import httpx
import json

API_KEY = "1XteCCQ_s_UbrqOGEIYybmDBnokWhxYap90D6_Jojx8"
BASE_URL = "http://localhost:8000"

@pytest.mark.asyncio
async def test_analyze_danger():
    """Test DANGER alert for overheat."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{BASE_URL}/v1/ihi/analyze",
            headers={"X-API-KEY": API_KEY},
            json={
                "ts": "29/05 14:35",
                "data": [{"id": "M-001", "t": 95, "v": 5.2, "c": 82}]
            }
        )
        assert resp.status_code == 200
        result = resp.json()
        assert result["alert"] == "DANGER"
        assert "M-001" in result["devices"]

@pytest.mark.asyncio
async def test_analyze_warning():
    """Test WARNING alert for elevated readings."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{BASE_URL}/v1/ihi/analyze",
            headers={"X-API-KEY": API_KEY},
            json={
                "ts": "29/05 14:35",
                "data": [{"id": "M-002", "t": 87, "v": 4.8, "c": 68}]
            }
        )
        assert resp.status_code == 200
        result = resp.json()
        assert result["alert"] == "WARNING"
        assert "M-002" in result["devices"]

@pytest.mark.asyncio
async def test_analyze_normal():
    """Test NORMAL response for healthy readings."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{BASE_URL}/v1/ihi/analyze",
            headers={"X-API-KEY": API_KEY},
            json={
                "ts": "29/05 14:35",
                "data": [{"id": "M-003", "t": 45, "v": 1.5, "c": 35}]
            }
        )
        assert resp.status_code == 200
        result = resp.json()
        assert result["alert"] == "NORMAL"
        assert result["devices"] == []

@pytest.mark.asyncio
async def test_rag_list():
    """Test listing RAG cases."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(
            f"{BASE_URL}/v1/ihi/rag",
            headers={"X-API-KEY": API_KEY}
        )
        assert resp.status_code == 200
        cases = resp.json()
        assert len(cases) >= 10  # At least our seed cases

@pytest.mark.asyncio
async def test_feedback_creates_rag():
    """Test that feedback creates new RAG entry."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{BASE_URL}/v1/ihi/feedback",
            headers={"X-API-KEY": API_KEY},
            json={
                "ts": "29/05 16:00",
                "device_id": "M-999",
                "severity": "CRITICAL",
                "description": "Motor mới bị kêu lạ + nhiệt tăng",
                "resolution": "Đã kiểm tra, thay dầu bôi trơn"
            }
        )
        assert resp.status_code == 200
        result = resp.json()
        assert "case_id" in result
        assert result["status"] == "created"
```

- [ ] **Step 2: Run integration tests**

```bash
pytest tests/integration/test_ihi_rag_flow.py -v
```

Expected: All tests pass

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_ihi_rag_flow.py
git commit -m "test(ihi): add integration tests for RAG learning flow"
```

---

## Task 8: Final Verification

- [ ] **Step 1: Run all IHI tests**

```bash
pytest tests/unit/test_ihi_models.py tests/unit/test_ihi_analyzer.py tests/unit/test_ihi_rag.py tests/integration/test_ihi_rag_flow.py -v
```

- [ ] **Step 2: Test manual flow**

```bash
# Test analyze
curl -X POST http://localhost:8000/v1/ihi/analyze \
  -H "X-API-KEY: 1XteCCQ_s_UbrqOGEIYybmDBnokWhxYap90D6_Jojx8" \
  -H "Content-Type: application/json" \
  -d '{"ts": "29/05 14:35", "data": [{"id": "M-001", "t": 95, "v": 5.2, "c": 82}]}'

# Test RAG list
curl http://localhost:8000/v1/ihi/rag \
  -H "X-API-KEY: 1XteCCQ_s_UbrqOGEIYybmDBnokWhxYap90D6_Jojx8"
```

- [ ] **Step 3: Final commit**

```bash
git add -A
git commit -m "feat(ihi): add RAG learning system with analyzer, feedback API, and seed data"
```

---

## Summary

| Task | Files | Goal |
|------|-------|------|
| 1 | Models | Pydantic models for IHI API |
| 2 | Analyzer | CRITICAL/WARNING rule matching |
| 3 | RAG Service | Pattern matching + case lookup |
| 4 | Routes | `/v1/ihi/analyze` + `/v1/ihi/rag` endpoints |
| 5 | Database | IHI tables added to schema |
| 6 | Seed | 10 initial RAG cases |
| 7 | Tests | Unit + integration tests |
| 8 | Final | Full verification |

---

**Plan complete and saved to `docs/superpowers/plans/2026-05-31-ihi-rag-learning-plan.md`**

**Two execution options:**

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
