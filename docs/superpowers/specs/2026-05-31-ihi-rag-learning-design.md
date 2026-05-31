# IHI RAG Learning System — Design Spec

**Date:** 2026-05-31  
**Project:** AIHUB + IHI Integration  
**Status:** Draft

---

## 1. Overview

AIHUB serves as central AI hub. IHI project sends sensor JSON data via API. AIHUB analyzes and responds with alerts. RAG system learns from historical InfluxDB data + manager feedback.

## 2. Architecture

```
IHI → POST /v1/ihi/analyze → AIHUB
                               ↓
                         Check in order:
                         1. CRITICAL rules (override)
                         2. RAG lookup (pattern match)
                         3. NORMAL (no alert)
                               ↓
                         Response: {alert, devices, case_id, confidence}
```

## 3. Sensor JSON Format

**IHI → AIHUB (compact)**

```json
{
  "ts": "29/05 14:35",
  "data": [
    {"id": "M-001", "t": 95, "v": 5.2, "c": 82},
    {"id": "M-002", "t": 88, "v": 4.8, "c": 68}
  ]
}
```

**Field mapping:**
- `ts` — timestamp (DD/MM HH:MM)
- `id` — device_id (short code)
- `t` — temperature (°C)
- `v` — vibration (mm/s)
- `c` — current (A)

**AIHUB → IHI response**

```json
{
  "alert": "DANGER",
  "devices": ["M-001"],
  "case_id": null,
  "confidence": 1.0
}
```

Or for RAG-matched case:

```json
{
  "alert": "CRITICAL",
  "devices": ["M-001"],
  "case_id": "RAG-042",
  "confidence": 0.92,
  "symptom": "overheat_vibration"
}
```

## 4. Alert Levels

| Level | Trigger | Action |
|-------|---------|--------|
| **DANGER** | CRITICAL rule match | Alert immediately |
| **WARNING** | WARNING rule match | Alert immediately |
| **NORMAL** | No match | No alert |

## 5. Detection Rules

### 5.1 CRITICAL Rules (Priority 1)

```python
DANGER if:
  - temperature > 90  # overheat
  - vibration > 6.0  # excessive_vibration
  - current > 75     # overload
```

### 5.2 WARNING Rules (Priority 2)

```python
WARNING if:
  - 85 < temperature <= 90
  - 4.5 < vibration <= 6.0
  - 65 < current <= 75
```

### 5.3 RAG Lookup (Priority 3)

If no rule match, lookup RAG knowledge base for pattern match.

## 6. RAG Knowledge Structure

```json
{
  "case_id": "RAG-001",
  "severity": "CRITICAL",
  "symptom": "overheat_vibration",
  "pattern": {
    "t_min": 85,
    "t_max": 100,
    "v_min": 5.0,
    "v_max": 8.0,
    "c_min": 70,
    "c_max": 90
  },
  "description": "Motor overheating kèm vibration cao — bearing wear sắp xảy ra",
  "resolution": "Kiểm tra bearing, verify lubrication",
  "confirmed_by": "manager@factory1",
  "created_at": "29/05/2026",
  "match_count": 5
}
```

**Organization:** severity → symptom (2-level lookup for fast retrieval)

### 6.1 Symptom Taxonomy

| Symptom | Pattern |
|---------|---------|
| `overheat` | temp > threshold |
| `excessive_vibration` | vibration > threshold |
| `overload` | current > threshold |
| `overheat_vibration` | temp + vibration combo |
| `multi_param` | 2+ parameters anomalous |

## 7. RAG Seed from InfluxDB

### 7.1 Process

1. Extract sensor data from InfluxDB backup (shard 13 = Feb 2026 sample)
2. Normalize to compact format
3. Cluster patterns using rule-based thresholds
4. Generate initial RAG entries
5. Mark as `status: pending_review`
6. Manager confirms before activation

### 7.2 Initial Seed Rules

```python
# Extract patterns from historical data
if temp > 90 OR vibration > 6.0 OR current > 75:
  severity = CRITICAL
  symptom = classify_symptom(temp, vibration, current)
  create_rag_entry()
```

### 7.3 Test Data

Create 10+ seed cases covering:
- Motor overheat
- Excessive vibration
- Current overload
- Combined overheat + vibration
- Warning level precursors

## 8. Manager Feedback API

### 8.1 Endpoint

**POST /v1/ihi/rag**

```json
{
  "ts": "29/05 14:35",
  "device_id": "M-001",
  "severity": "CRITICAL",
  "description": "Máy CNC-001 bị rung bất thường + nhiệt tăng nhanh",
  "resolution": "Đã thay bearing, để test 24h"
}
```

### 8.2 AIHUB Processing

1. Receive feedback
2. Query sensor data at timestamp (from IHI or cache)
3. Extract pattern (temp/vibration/current values)
4. Create RAG entry with pattern
5. Return `case_id` for confirmation

### 8.3 Response

```json
{
  "case_id": "RAG-042",
  "status": "created",
  "pattern": {
    "t_range": [85, 95],
    "v_range": [5.0, 6.5],
    "c_range": [70, 80]
  }
}
```

## 9. Database Schema

### 9.1 Table: ihi_sensor_readings

```sql
CREATE TABLE ihi_sensor_readings (
  id SERIAL PRIMARY KEY,
  device_id VARCHAR(50) NOT NULL,
  timestamp TIMESTAMP NOT NULL,
  temperature FLOAT,
  vibration FLOAT,
  current FLOAT,
  created_at TIMESTAMP DEFAULT NOW()
);
```

### 9.2 Table: ihi_rag_cases

```sql
CREATE TABLE ihi_rag_cases (
  case_id VARCHAR(20) PRIMARY KEY,
  severity VARCHAR(20) NOT NULL,  -- CRITICAL, WARNING, INFO
  symptom VARCHAR(50) NOT NULL,
  pattern JSONB NOT NULL,
  description TEXT,
  resolution TEXT,
  confirmed_by VARCHAR(100),
  status VARCHAR(20) DEFAULT 'active',  -- pending_review, active, deprecated
  match_count INT DEFAULT 0,
  created_at TIMESTAMP DEFAULT NOW(),
  updated_at TIMESTAMP DEFAULT NOW()
);
```

### 9.3 Table: ihi_feedback_log

```sql
CREATE TABLE ihi_feedback_log (
  id SERIAL PRIMARY KEY,
  device_id VARCHAR(50) NOT NULL,
  feedback_ts TIMESTAMP NOT NULL,
  sensor_ts TIMESTAMP NOT NULL,
  severity VARCHAR(20) NOT NULL,
  description TEXT,
  resolution TEXT,
  case_id VARCHAR(20),
  created_at TIMESTAMP DEFAULT NOW()
);
```

## 10. API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/v1/ihi/analyze` | Analyze sensor data |
| GET | `/v1/ihi/rag` | List RAG cases |
| POST | `/v1/ihi/rag` | Create RAG case from feedback |
| GET | `/v1/ihi/rag/{case_id}` | Get specific case |
| PUT | `/v1/ihi/rag/{case_id}` | Update RAG case |
| DELETE | `/v1/ihi/rag/{case_id}` | Delete RAG case |
| POST | `/v1/ihi/seed` | Trigger RAG seed from InfluxDB |
| GET | `/v1/ihi/history` | Get sensor history for device |

## 11. Files to Create

```
app/
├── routes/
│   └── ihi.py                 # New: IHI API routes
├── services/
│   └── ihi_rag_service.py     # New: RAG knowledge service
│   └── ihi_analyzer.py        # New: Sensor analysis + rule matching
├── models/
│   └── ihi.py                 # New: Pydantic models
prompts/
└── ihi_rag.md                 # Updated: RAG-aware prompt
scripts/
└── seed_ihi_rag.py            # New: Seed RAG from InfluxDB
tests/
├── unit/
│   └── test_ihi_rag.py        # New
│   └── test_ihi_analyzer.py   # New
└── integration/
    └── test_ihi_rag_flow.py   # New
```

## 12. RAG Seed Test Cases

| case_id | severity | symptom | t_range | v_range | c_range | description |
|---------|----------|---------|---------|---------|---------|-------------|
| RAG-001 | CRITICAL | overheat | 90-100 | 0-4.5 | 0-65 | Motor quá nhiệt |
| RAG-002 | CRITICAL | excessive_vibration | 0-85 | 6.0-10.0 | 0-65 | Rung quá mức |
| RAG-003 | CRITICAL | overload | 0-85 | 0-4.5 | 75-100 | Quá tải dòng điện |
| RAG-004 | CRITICAL | overheat_vibration | 85-100 | 5.0-8.0 | 60-80 | Overheat + vibration |
| RAG-005 | WARNING | overheat_precursor | 85-90 | 0-4.5 | 0-65 | Nhiệt tiền nguy hiểm |
| RAG-006 | WARNING | vibration_precursor | 0-85 | 4.5-6.0 | 0-65 | Rung tiền nguy hiểm |
| RAG-007 | WARNING | overload_precursor | 0-85 | 0-4.5 | 65-75 | Dòng cao tiền nguy |
| RAG-008 | CRITICAL | multi_param | 85-95 | 4.5-6.0 | 65-80 | 2+ thông số bất thường |
| RAG-009 | INFO | normal_high | 80-85 | 3.0-4.5 | 55-65 | Gần ngưỡng bình thường |
| RAG-010 | CRITICAL | sudden_spike | 90-100 | 6.0-10.0 | 75-100 | Đột biến đột ngột |

## 13. Acceptance Criteria

- [ ] POST /v1/ihi/analyze returns correct alert for DANGER/WARNING rules
- [ ] RAG lookup returns matched case when no rule match but pattern matches
- [ ] POST /v1/ihi/rag creates new RAG entry from manager feedback
- [ ] RAG seed script successfully imports 10+ test cases from InfluxDB
- [ ] Manager can view, update, delete RAG cases via API
- [ ] Response time < 500ms for /analyze endpoint
- [ ] Token usage optimized (compact JSON, no unnecessary text)

---

**Next:** Implementation plan (writing-plans skill)
