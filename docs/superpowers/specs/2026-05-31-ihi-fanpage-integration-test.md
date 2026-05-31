# IHI & Fanpage Integration Test Spec

Date: 2026-05-31
Status: approved

## Overview

Two independent integration test scripts to verify AIHub behavior under IHI RAG re-recognition and Fanpage continuous load scenarios.

---

## Test 1: IHI RAG Re-Recognition (`test_ihi_rag_recognition.py`)

### Purpose

Verify AIHub correctly escalates sensor readings from NORMAL → WARNING after a manager creates a new RAG case that matches previously-normal readings.

### Test Sequence

**Step 1 — Baseline: Normal readings return NORMAL**
- Send 10 sensors (7 boundary, 3 clearly normal)
- Boundary: temp=[86-89], vib=[4.2-4.4], current=[61-64]
- Normal: temp=45, vib=1.5, current=35
- Expected: `alert=NORMAL`

**Step 2 — Manager creates RAG case**
- `POST /v1/ihi/rag` with:
  - `case_id`: "BOUNDARY-WARN-01"
  - `severity`: WARNING
  - `symptom`: "overheat_precursor"
  - `pattern`: `{"t_min": 85, "t_max": 90, "v_min": 4.0, "v_max": 4.5, "c_min": 60, "c_max": 65}`
  - `description`: "Motor running hot — boundary temperature with elevated vibration"

**Step 3 — Re-analysis: same readings now return WARNING**
- Send same 10 sensors again
- Expected: boundary sensors (temp 86-89, vib 4.2-4.4, current 61-64) return `alert=WARNING`
- Expected: normal sensors (temp=45, vib=1.5, current=35) return `alert=NORMAL`

### Expected Behavior

AIHub `/v1/ihi/analyze` flow:
1. Rule-based `IHIAnalyzer` checks thresholds
   - WARNING: 85 < temp ≤ 90, OR 4.5 < vib ≤ 6.0, OR 65 < current ≤ 75
   - Boundary readings (temp 86-89, vib 4.2-4.4, current 61-64) are JUST below WARNING thresholds
2. Since boundary readings are NORMAL per rules, `IHIragService.find_matching_case()` is called
3. After RAG case created, `PatternMatcher.matches()` returns True for boundary readings
4. `confidence` score calculated; if > threshold, alert upgraded to WARNING

### Acceptance Criteria

- [x] Step 1: all 10 sensors return NORMAL alert
- [x] Step 2: RAG case created successfully (201/200 status)
- [x] Step 3: boundary sensors return WARNING, normal sensors return NORMAL
- [x] `case_id` returned in Step 3 response
- [x] `confidence` > 0.5 in Step 3 response

---

## Test 2: Fanpage Continuous Chat (`test_fanpage_continuous.py`)

### Purpose

Verify AIHub handles rapid multi-turn conversations with consistent quality and no errors over 100 consecutive calls (20 cycles × 5 messages).

### Conversation Cycle (repeated 20x)

Each cycle:
1. `user_id`: "fanpage-user-001", `project_id`: "fanpage"
2. Message 1: "Xin chào"
3. Message 2: "Cho tôi hỏi về sản phẩm A"
4. Message 3: "Giá bao nhiêu?"
5. Message 4: "Cảm ơn"
6. Message 5: "Sản phẩm B có gì khác?"

### Configuration

- `stream: false`
- Same `user_id` throughout (session continuity test)
- Rapid fire: no delay between calls (load test)
- API key: use existing test key

### Metrics Collected

Per call:
- `latency_ms`: time from request to response
- `response_length`: chars in `content` field
- `error`: status code if != 200
- `cycle`: which cycle number (1-20)

### Quality Checks

- All 100 calls return HTTP 200
- Response `content` is not empty
- No repeated/duplicate content across consecutive cycles
- Latency p95 < 10s per call

### Acceptance Criteria

- [x] 100 total calls, all return 200
- [x] No empty responses
- [x] Latency p95 < 10s
- [x] At least 3 distinct response patterns across cycles (not same message repeated)

---

## Shared Test Configuration

- Base URL: `http://localhost:8000`
- API key: `"1XteCCQ_s_UbrqOGEIYybmDBnokWhxYap90D6_Jojx8"` (existing test key)
- Timeout: 120s per call for IHI, 60s per call for Fanpage
- Output: colored pass/fail table to stdout

## Files

- `scripts/test_ihi_rag_recognition.py`
- `scripts/test_fanpage_continuous.py`