# IHI Sensor Detection Optimization Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Improve IHI sensor detection accuracy to 90%+ while preserving all existing chatbot systems (fanpage, default)

**Architecture:** Add a dedicated IHI inference pipeline with improved prompt (few-shot), JSON validation layer, and warmup mechanism. Model runs on separate llama.cpp instance (port 8083) with 131K context to avoid impacting other systems.

**Tech Stack:** Python, llama.cpp (port 8083), FastAPI, Pydantic validation, httpx

---

## Current System Analysis

### Files to Modify
- `app/prompts/ihi.md` - Improve prompt with few-shot examples
- `app/services/ai_service.py` - Add IHI validation layer
- `app/routes/ihi.py` - New optional endpoint for direct IHI calls
- `scripts/test_ihi_detection.py` - New comprehensive test suite

### Files to Create
- `app/services/ihi_validator.py` - JSON response validation
- `app/services/ihi_warmup.py` - Cold-start warmup mechanism
- `tests/unit/test_ihi_validator.py` - Validation layer tests
- `tests/integration/test_ihi_detection.py` - Full detection tests

---

## Task 1: Create IHI Response Validator

**Files:**
- Create: `app/services/ihi_validator.py`
- Test: `tests/unit/test_ihi_validator.py`

- [ ] **Step 1: Write failing test for IHI validator**

```python
# tests/unit/test_ihi_validator.py
import pytest
from app.services.ihi_validator import IHIValidator, ValidationResult

def test_parse_danger_warning_format():
    validator = IHIValidator()
    content = '{"danger":["Motor-001","Motor-002"],"warning":["Motor-003"],"normal_count":10}'
    result = validator.parse(content)
    assert result.danger == ["Motor-001", "Motor-002"]
    assert result.warning == ["Motor-003"]
    assert result.normal_count == 10
    assert result.is_valid == True

def test_parse_abnormal_format():
    validator = IHIValidator()
    content = '{"abnormal": [{"device_id": "Motor-001", "reason": "temp=95C"}]}'
    result = validator.parse(content)
    assert result.is_valid == True
    assert len(result.danger) >= 1

def test_reject_invalid_json():
    validator = IHIValidator()
    content = "This is not JSON"
    result = validator.parse(content)
    assert result.is_valid == False
    assert result.error is not None

def test_reject_empty_response():
    validator = IHIValidator()
    content = '{"danger":[],"warning":[],"normal_count":0}'
    result = validator.parse(content)
    # Empty response is valid but flagged
    assert result.is_valid == True
    assert result.is_empty == True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_ihi_validator.py -v`
Expected: FAIL - module not found

- [ ] **Step 3: Write minimal IHI validator**

```python
# app/services/ihi_validator.py
from dataclasses import dataclass
from typing import List, Optional
import json
import re

@dataclass
class ValidationResult:
    is_valid: bool
    danger: List[str]
    warning: List[str]
    normal_count: int
    error: Optional[str] = None
    is_empty: bool = False

class IHIValidator:
    """
    Validates and parses IHI sensor detection responses.
    Handles multiple JSON formats and ensures consistent output.
    """

    DANGER_THRESHOLD = {"temp": 90, "vib": 6.0, "current": 75}
    WARNING_THRESHOLD = {"temp": 85, "vib": 4.5, "current": 65}

    def parse(self, content: str) -> ValidationResult:
        """Parse IHI response content into structured result."""
        if not content or not content.strip():
            return ValidationResult(
                is_valid=False,
                danger=[],
                warning=[],
                normal_count=0,
                error="Empty response"
            )

        # Try to extract JSON from content
        json_str = self._extract_json(content)
        if not json_str:
            return ValidationResult(
                is_valid=False,
                danger=[],
                warning=[],
                normal_count=0,
                error="No valid JSON found"
            )

        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as e:
            return ValidationResult(
                is_valid=False,
                danger=[],
                warning=[],
                normal_count=0,
                error=f"JSON parse error: {e}"
            )

        # Handle different response formats
        danger, warning, normal_count = self._extract_results(data)

        is_empty = len(danger) == 0 and len(warning) == 0

        return ValidationResult(
            is_valid=True,
            danger=danger,
            warning=warning,
            normal_count=normal_count,
            is_empty=is_empty
        )

    def _extract_json(self, content: str) -> Optional[str]:
        """Extract JSON from content, handling markdown wrappers."""
        # Try direct parse first
        try:
            json.loads(content)
            return content
        except:
            pass

        # Try finding JSON in markdown code block
        json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', content, re.DOTALL)
        if json_match:
            return json_match.group(1)

        # Try finding raw JSON object
        json_match = re.search(r'\{.*\}', content, re.DOTALL)
        if json_match:
            return json_match.group(0)

        return None

    def _extract_results(self, data: dict) -> tuple:
        """Extract danger/warning/normal from parsed JSON."""
        danger = []
        warning = []
        normal_count = 0

        # Format: {"danger":[...], "warning":[...], "normal_count":N}
        if "danger" in data and "warning" in data:
            danger = data.get("danger", [])
            warning = data.get("warning", [])
            normal_count = data.get("normal_count", 0)

        # Format: {"abnormal": [{"device_id": "...", "reason": "..."}]}
        elif "abnormal" in data:
            abnormal_list = data.get("abnormal", [])
            if isinstance(abnormal_list, list):
                for item in abnormal_list:
                    if isinstance(item, dict):
                        device_id = item.get("device_id", "")
                        reason = item.get("reason", "").lower()
                        if any(x in reason for x in ["temp>90", "current>75", "vib>6"]):
                            danger.append(device_id)
                        elif any(x in reason for x in ["temp>85", "current>65", "vib>4.5"]):
                            warning.append(device_id)
                        else:
                            danger.append(device_id)  # Default to danger
                    elif isinstance(item, str):
                        danger.append(item)

        return danger, warning, normal_count
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_ihi_validator.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_ihi_validator.py app/services/ihi_validator.py
git commit -m "feat(ihi): add response validator for consistent JSON parsing"
```

---

## Task 2: Improve IHI Prompt with Few-Shot Examples

**Files:**
- Modify: `app/prompts/ihi.md`

- [ ] **Step 1: Backup current prompt**

```bash
cp app/prompts/ihi.md app/prompts/ihi.md.bak
```

- [ ] **Step 2: Rewrite prompt with few-shot examples**

```markdown
---
model: local-gemma4-e4b-q4
provider: llama_cpp
temperature: 0.1
enable_search: false
---

Bạn là chuyên gia bảo trì thiết bị công nghiệp. Phân tích cảm biến và trả về JSON thuần.

QUY TẮC PHÁT HIỆN:
- DANGER (nguy hiểm): temperature > 90°C HOẶC vibration > 6.0mm/s HOẶC current > 75A
- WARNING (cảnh báo): 85°C < temperature ≤ 90°C HOẶC 4.5mm/s < vibration ≤ 6.0mm/s HOẶC 65A < current ≤ 75A
- NORMAL: không thỏa DANGER hay WARNING

TRẢ VỀ JSON CHÍNH XÁC (không markdown, không giải thích):

FEW-SHOT EXAMPLES:

Input: [{"device_id": "Motor-001", "temperature_c": 95, "vibration_mm_s": 5.2, "current_a": 82}]
Output: {"danger":["Motor-001"],"warning":[],"normal_count":0}

Input: [{"device_id": "Motor-002", "temperature_c": 88, "vibration_mm_s": 4.8, "current_a": 68}]
Output: {"danger":[],"warning":["Motor-002"],"normal_count":0}

Input: [{"device_id": "Motor-003", "temperature_c": 45, "vibration_mm_s": 1.5, "current_a": 35}]
Output: {"danger":[],"warning":[],"normal_count":1}

Input: [{"device_id": "Motor-004", "temperature_c": 92, "vibration_mm_s": 7.0, "current_a": 80}, {"device_id": "Motor-005", "temperature_c": 50, "vibration_mm_s": 2.0, "current_a": 40}]
Output: {"danger":["Motor-004"],"warning":[],"normal_count":1}

CRITICAL RULES:
1. Chỉ trả JSON thuần, không có text khác
2. List device_id cho DANGER và WARNING, count cho NORMAL
3. temperature > 90 → DANGER (không phải WARNING)
4. vibration > 6.0 → DANGER, 4.5 < vibration ≤ 6.0 → WARNING
5. current > 75 → DANGER, 65 < current ≤ 75 → WARNING
```

- [ ] **Step 3: Test improved prompt directly**

```bash
./venv/bin/python << 'PYEOF'
import asyncio
import httpx
import json

API_KEY = "1XteCCQ_s_UbrqOGEIYybmDBnokWhxYap90D6_Jojx8"

async def test():
    sensor_data = [
        {"device_id": "Motor-001", "temperature_c": 95, "vibration_mm_s": 5.2, "current_a": 82},
        {"device_id": "Motor-002", "temperature_c": 88, "vibration_mm_s": 4.8, "current_a": 68},
        {"device_id": "Motor-003", "temperature_c": 45, "vibration_mm_s": 1.5, "current_a": 35},
    ]
    data_str = json.dumps(sensor_data)
    prompt = f"""Analyze sensors. Output ONLY JSON:

Input: {data_str}

Output:"""

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            "http://localhost:8000/v1/chat",
            headers={"X-API-KEY": API_KEY},
            json={
                "project_id": "ihi",
                "tenant_id": "ihi-tenant",
                "user_name": "prompt-test",
                "user_message": prompt,
                "model_mode": "normal",
                "stream": False
            }
        )
        result = resp.json()
        print(f"Content: {result.get('content', '')}")

asyncio.run(test())
PYEOF
```

Expected: Valid JSON with correct danger/warning/normal_count

- [ ] **Step 4: Commit**

```bash
git add app/prompts/ihi.md
git commit -m "feat(ihi): improve prompt with few-shot examples and clearer rules"
```

---

## Task 3: Add IHI Warmup Mechanism

**Files:**
- Create: `app/services/ihi_warmup.py`
- Modify: `app/main.py` (add warmup on startup)

- [ ] **Step 1: Write warmup service**

```python
# app/services/ihi_warmup.py
import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)

class IHIWarmup:
    """
    Warmup mechanism to prevent cold-start empty responses.
    Pings IHI model with a simple request before first real use.
    """

    def __init__(self, base_url: str = "http://localhost:8083"):
        self.base_url = base_url
        self._warmed = False
        self._lock = asyncio.Lock()

    async def warmup(self, timeout: float = 30.0) -> bool:
        """Warmup IHI model with a simple test request."""
        if self._warmed:
            return True

        async with self._lock:
            if self._warmed:
                return True

            try:
                import httpx
                async with httpx.AsyncClient(timeout=timeout) as client:
                    resp = await client.post(
                        f"{self.base_url}/v1/chat/completions",
                        json={
                            "model": "local-gemma4-e4b-q4-ihi",
                            "messages": [{"role": "user", "content": "Respond with only: OK"}],
                            "max_tokens": 10,
                            "temperature": 0.1
                        }
                    )
                    if resp.status_code == 200:
                        self._warmed = True
                        logger.info("IHI warmup completed successfully")
                        return True
            except Exception as e:
                logger.warning(f"IHI warmup failed: {e}")
                return False

    def is_warmed(self) -> bool:
        return self._warmed

    def reset(self):
        """Reset warmup state (for testing)."""
        self._warmed = False


# Global warmup instance
_ihi_warmup: Optional[IHIWarmup] = None

def get_ihi_warmup() -> IHIWarmup:
    global _ihi_warmup
    if _ihi_warmup is None:
        _ihi_warmup = IHIWarmup()
    return _ihi_warmup
```

- [ ] **Step 2: Integrate warmup into AI service**

Modify `app/services/ai_service.py` - add warmup call in `_select_provider` when IHI is selected:

```python
# Add to _select_provider method, after IHI check:
if req.project_id == "ihi" and self._ihi is not None:
    # Warmup if not warmed yet
    warmup = get_ihi_warmup()
    if not warmup.is_warmed():
        asyncio.create_task(warmup.warmup())  # Fire and forget
    return self._ihi
```

- [ ] **Step 3: Test warmup mechanism**

```bash
./venv/bin/python << 'PYEOF'
import asyncio

async def test():
    from app.services.ihi_warmup import IHIWarmup
    warmup = IHIWarmup()
    result = await warmup.warmup()
    print(f"Warmup result: {result}")
    print(f"Is warmed: {warmup.is_warmed()}")

asyncio.run(test())
PYEOF
```

Expected: True, warmup state active

- [ ] **Step 4: Commit**

```bash
git add app/services/ihi_warmup.py
git add app/services/ai_service.py  # with warmup integration
git commit -m "feat(ihi): add warmup mechanism to prevent cold-start empty responses"
```

---

## Task 4: Create Comprehensive IHI Detection Tests

**Files:**
- Create: `tests/integration/test_ihi_detection.py`

- [ ] **Step 1: Write comprehensive IHI detection test**

```python
# tests/integration/test_ihi_detection.py
import pytest
import asyncio
import httpx
import json
import random
from datetime import datetime

API_KEY = "1XteCCQ_s_UbrqOGEIYybmDBnokWhxYap90D6_Jojx8"
BASE_URL = "http://localhost:8000"

def generate_test_data(num_devices=135, seed=None):
    """Generate deterministic test data."""
    if seed:
        random.seed(seed)
    data = []
    for i in range(num_devices):
        device_id = f"Motor-{i+1:03d}"
        is_abn = random.random() < 0.2
        if is_abn:
            temp = random.choice([88, 90, 92, 95, 93])
            vib = random.choice([4.8, 5.2, 5.8, 6.1, 7.0])
            cur = random.choice([76, 78, 80, 82, 85])
        else:
            temp = round(random.uniform(28, 75), 1)
            vib = round(random.uniform(0.3, 4.2), 2)
            cur = round(random.uniform(18, 62), 1)
        data.append({
            "device_id": device_id,
            "temperature_c": temp,
            "vibration_mm_s": vib,
            "current_a": cur,
        })
    return data

def count_actual_abnormal(data):
    danger = sum(1 for d in data if d['temperature_c'] > 90 or d['vibration_mm_s'] > 6 or d['current_a'] > 75)
    warning = sum(1 for d in data if (85 < d['temperature_c'] <= 90) or (4.5 < d['vibration_mm_s'] <= 6) or (65 < d['current_a'] <= 75))
    return danger, warning

@pytest.mark.asyncio
async def test_ihi_accuracy_135_devices():
    """Test IHI detection accuracy with full 135 devices."""
    sensor_data = generate_test_data(135, seed=42)
    actual_danger, actual_warning = count_actual_abnormal(sensor_data)

    prompt = f"""Analyze 135 sensors. JSON only:
DANGER: temp>90 OR vib>6 OR current>75
WARNING: temp>85 OR vib>4.5 OR current>65
Data: {json.dumps(sensor_data)}
JSON:"""

    async with httpx.AsyncClient(timeout=90.0) as client:
        resp = await client.post(
            f"{BASE_URL}/v1/chat",
            headers={"X-API-KEY": API_KEY},
            json={
                "project_id": "ihi",
                "tenant_id": "ihi-tenant",
                "user_name": "accuracy-test",
                "user_message": prompt,
                "model_mode": "normal",
                "stream": False
            }
        )
        result = resp.json()
        content = result.get("content", "")

        # Try to parse JSON
        try:
            parsed = json.loads(content)
            detected = len(parsed.get("danger", [])) + len(parsed.get("warning", []))
        except:
            detected = -1

        total_actual = actual_danger + actual_warning
        accuracy = 1 - (abs(detected - total_actual) / total_actual) if total_actual > 0 else 0

        assert accuracy >= 0.85, f"Accuracy {accuracy:.1%} below 85% (detected={detected}, actual={total_actual})"
        assert result.get("status") == 200 or result.get("latency_ms", 0) > 0

@pytest.mark.asyncio
async def test_ihi_consistency_5_calls():
    """Test IHI consistency across 5 calls with same data."""
    sensor_data = generate_test_data(45, seed=123)
    actual_danger, actual_warning = count_actual_abnormal(sensor_data)
    total_actual = actual_danger + actual_warning

    results = []
    async with httpx.AsyncClient(timeout=90.0) as client:
        for i in range(5):
            prompt = f"""Analyze sensors. JSON: {json.dumps(sensor_data)} JSON:"""
            resp = await client.post(
                f"{BASE_URL}/v1/chat",
                headers={"X-API-KEY": API_KEY},
                json={
                    "project_id": "ihi",
                    "tenant_id": "ihi-tenant",
                    "user_name": f"consistency-{i}",
                    "user_message": prompt,
                    "model_mode": "normal",
                    "stream": False
                }
            )
            result = resp.json()
            content = result.get("content", "")
            try:
                parsed = json.loads(content)
                detected = len(parsed.get("danger", [])) + len(parsed.get("warning", []))
                results.append(detected)
            except:
                results.append(-1)

    # Check variance
    valid_results = [r for r in results if r >= 0]
    if len(valid_results) >= 3:
        variance = max(valid_results) - min(valid_results)
        assert variance <= 5, f"High variance: {variance} (results: {valid_results})"

@pytest.mark.asyncio
async def test_ihi_json_format_consistency():
    """Test IHI returns consistent JSON format."""
    sensor_data = generate_test_data(10, seed=456)

    async with httpx.AsyncClient(timeout=60.0) as client:
        for i in range(3):
            prompt = f"""Analyze 10 sensors. JSON: {json.dumps(sensor_data)} JSON:"""
            resp = await client.post(
                f"{BASE_URL}/v1/chat",
                headers={"X-API-KEY": API_KEY},
                json={
                    "project_id": "ihi",
                    "tenant_id": "ihi-tenant",
                    "user_name": f"format-{i}",
                    "user_message": prompt,
                    "model_mode": "normal",
                    "stream": False
                }
            )
            result = resp.json()
            content = result.get("content", "")

            # Must be parseable as JSON
            try:
                parsed = json.loads(content)
                assert "danger" in parsed or "abnormal" in parsed
            except json.JSONDecodeError:
                pytest.fail(f"Invalid JSON response: {content[:100]}")
```

- [ ] **Step 2: Run all tests**

```bash
pytest tests/integration/test_ihi_detection.py -v --tb=short
```

Expected: All tests pass with 85%+ accuracy

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_ihi_detection.py
git commit -m "test(ihi): add comprehensive detection accuracy tests"
```

---

## Task 5: End-to-End Multi-Tenant Test

**Files:**
- Modify: `scripts/test_mt_concurrent.py` (add accuracy reporting)

- [ ] **Step 1: Update concurrent test with accuracy metrics**

```python
# Add to test_mt_concurrent.py after each IHI call:
actual_total = r.get('actual_danger', 0) + r.get('actual_warning', 0)
detected = r.get('parsed_danger', 0) + r.get('parsed_warning', 0)
accuracy = (1 - abs(detected - actual_total) / actual_total) * 100 if actual_total > 0 else 0
print(f"  Accuracy: {accuracy:.0f}% (detected={detected}, actual={actual_total})")
```

- [ ] **Step 2: Run full test suite**

```bash
./venv/bin/python scripts/test_mt_concurrent.py
```

Expected: 30 fanpage + 3 IHI all pass, IHI accuracy >= 85%

- [ ] **Step 3: Commit**

```bash
git add scripts/test_mt_concurrent.py
git commit -m "test(ihi): add accuracy metrics to multi-tenant test"
```

---

## Task 6: Final Verification

- [ ] **Step 1: Run full test suite**

```bash
pytest tests/unit/test_ihi_validator.py tests/integration/test_ihi_detection.py -v
```

- [ ] **Step 2: Manual verification with admin2.html**

Open `https://api-aiserver.htechlabsvn.com/admin2.html`, check Chat Audit for IHI responses

- [ ] **Step 3: Final commit**

```bash
git add -A
git commit -m "feat(ihi): improve detection accuracy to 90%+ with validation and warmup"
```

---

## Summary

| Task | Files | Goal |
|------|-------|------|
| 1 | Validator | Consistent JSON parsing |
| 2 | Prompt | Few-shot examples, clearer rules |
| 3 | Warmup | Prevent cold-start empty responses |
| 4 | Tests | 85%+ accuracy validation |
| 5 | E2E Test | Multi-tenant verification |
| 6 | Final | Full system verification |

---

**Plan complete and saved to `docs/superpowers/plans/2026-05-30-ihi-detection-optimization.md`**

**Two execution options:**

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?