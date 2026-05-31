# Multi-Project Load Test Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Create comprehensive 1-hour load test script for AI Hub with Fanpage (2 tenants × 5 users), Vehix (5 users), IHI (30-40 machines/min), verifying per-project context optimization, accuracy, and isolation.

**Architecture:** Single Python script orchestrating concurrent httpx AsyncClient requests to all 3 projects with phased load (warmup → light → medium → heavy → sustained), metrics collection, and real-time reporting.

**Tech Stack:** Python, httpx, asyncio, json, time, dataclasses

---

## File Structure

- Create: `scripts/test_multi_project_load.py` - Main orchestrator
- Create: `scripts/generate_vehix_data.py` - Vehicle/fleet test data generator
- Create: `scripts/generate_ihi_data.py` - IHI sensor data generator
- Modify: `.env` - Add VEHIX_PROJECT_ID if needed

---

### Task 1: Create Multi-Project Load Test Orchestrator

**Files:**
- Create: `scripts/test_multi_project_load.py`

- [ ] **Step 1: Write skeleton with phased load design**

```python
#!/usr/bin/env python3
"""
Multi-Project Load Test: Fanpage + Vehix + IHI
Tests concurrent load over 1 hour with phased intensity.

Usage: python scripts/test_multi_project_load.py
"""
import asyncio
import httpx
import json
import time
import random
from dataclasses import dataclass, field
from datetime import datetime
from typing import List

API_KEY = "1XteCCQ_s_UbrqOGEIYybmDBnokWhxYap90D6_Jojx8"
BASE_URL = "http://localhost:8000"

PHASES = [
    ("Warmup", 0, 2),
    ("Light 50%", 2, 15),
    ("Medium 75%", 15, 30),
    ("Heavy 100%", 30, 45),
    ("Sustained Max", 45, 60),
]

@dataclass
class PhaseResult:
    name: str
    duration_s: float
    total_requests: int
    fanpage_ok: int
    fanpage_fail: int
    vehix_ok: int
    vehix_fail: int
    ihi_ok: int
    ihi_fail: int
    avg_latency: float
    p95_latency: float
    errors: List[str] = field(default_factory=list)

async def run_phase(phase_name: str, duration_min: int, intensity: float):
    """Run a test phase."""
    start = time.time()
    end_time = start + (duration_min * 60)
    results = []
    
    async with httpx.AsyncClient(timeout=60.0) as client:
        while time.time() < end_time:
            tasks = []
            # Fanpage: 2 tenants × 5 users at intensity
            for _ in range(int(10 * intensity)):
                tasks.append(call_fanpage(client))
            # Vehix: 5 users at intensity
            for _ in range(int(5 * intensity)):
                tasks.append(call_vehix(client))
            # IHI: every 60s
            if len(results) % 60 == 0:
                tasks.append(call_ihi(client))
            
            batch_results = await asyncio.gather(*tasks, return_exceptions=True)
            results.extend([r for r in batch_results if not isinstance(r, Exception)])
            await asyncio.sleep(1.0)
    
    return PhaseResult(...)
```

- [ ] **Step 2: Run to verify skeleton works**

Run: `timeout 5 python scripts/test_multi_project_load.py` (will timeout - expected)
Expected: Script starts, runs warmup phase, then interrupted

- [ ] **Step 3: Implement Fanpage test data and calls**

```python
FANPAGE_TENANTS = ["tenant_fashion", "tenant_electronics"]
FANPAGE_PRODUCTS = [
    "tư vấn mua laptop Dell XPS 15",
    "so sánh iPhone 16 Pro vs Samsung S25 Ultra",
    "hỏi chính sách đổi trả điện thoại 30 ngày",
    "cách đặt hàng online qua website",
    "xem trạng thái đơn hàng #12345",
    "khiếu nại giao hàng trễ 3 ngày",
    "hướng dẫn thanh toán QR banking",
    "khuyến mãi laptop gaming giảm 30%",
    "hỏi về bảo hành điện thoại 12 tháng",
    "địa chỉ cửa hàng HCM",
]

async def call_fanpage(client: httpx.AsyncClient) -> dict:
    """Simulate Fanpage user query."""
    tenant = random.choice(FANPAGE_TENANTS)
    user = f"user_{random.randint(1, 100):02d}"
    message = random.choice(FANPAGE_PRODUCTS)
    
    try:
        resp = await client.post(
            f"{BASE_URL}/v1/chat",
            headers={"X-API-KEY": API_KEY},
            json={
                "project_id": "fanpage",
                "tenant_id": tenant,
                "user_name": user,
                "user_message": message,
                "model_mode": "lite",
                "stream": False
            },
            timeout=30.0
        )
        result = resp.json()
        return {"project": "fanpage", "status": resp.status_code, "latency": result.get("latency_ms", 0)}
    except Exception as e:
        return {"project": "fanpage", "error": str(e)[:50]}
```

- [ ] **Step 4: Implement Vehix test data and calls**

```python
VEHIX_CONTRACTS = [
    {"id": "VHX-2024-001", "plate": "51A-12345"},
    {"id": "VHX-2024-002", "plate": "51B-67890"},
    {"id": "VHX-2024-003", "plate": "59H-11111"},
]

async def call_vehix(client: httpx.AsyncClient) -> dict:
    """Simulate Vehix fleet query."""
    contract = random.choice(VEHIX_CONTRACTS)
    queries = [
        f"Tình trạng hợp đồng {contract['id']}",
        f"Xe {contract['plate']} đang ở đâu?",
        f"Danh sách xe đang thuê",
        f"Hợp đồng sắp hết hạn trong 7 ngày",
        f"Cập nhật trạng thái xe {contract['plate']} thành MAINTENANCE",
    ]
    message = random.choice(queries)
    
    try:
        resp = await client.post(
            f"{BASE_URL}/v1/chat",
            headers={"X-API-KEY": API_KEY},
            json={
                "project_id": "vehix",
                "tenant_id": "vehix-fleet",
                "user_name": f"operator_{random.randint(1, 20)}",
                "user_message": message,
                "model_mode": "normal",
                "stream": False
            },
            timeout=30.0
        )
        result = resp.json()
        return {"project": "vehix", "status": resp.status_code, "latency": result.get("latency_ms", 0)}
    except Exception as e:
        return {"project": "vehix", "error": str(e)[:50]}
```

- [ ] **Step 5: Implement IHI test data and calls**

```python
async def generate_ihi_data(num_machines=35):
    """Generate IHI sensor data for machining line."""
    data = []
    for i in range(num_machines):
        machine_id = f"M{i+1:03d}"
        # Simulate realistic machining line data
        power = round(random.uniform(0, 50), 1)  # kW
        vibration = round(random.uniform(0.1, 5.0), 2)  # mm/s
        temp = round(random.uniform(25, 95), 1)  # Celsius
        efficiency = round(random.uniform(0.5, 0.95), 3)
        data.append(f"{machine_id}:T{temp}V{vibration}P{power}E{efficiency}")
    return ",".join(data)

async def call_ihi(client: httpx.AsyncClient) -> dict:
    """Simulate IHI sensor analysis."""
    sensor_string = await generate_ihi_data(35)
    prompt = f"""Phân tích dây chuyền máy móc. Trả JSON:
Data: [{sensor_string}]
Format: {{"idle_machines":N,"warning":[],"critical":[],"avg_efficiency":X.XX}}
JSON:"""
    
    try:
        resp = await client.post(
            f"{BASE_URL}/v1/chat",
            headers={"X-API-KEY": API_KEY},
            json={
                "project_id": "ihi",
                "tenant_id": "ihi-plant-1",
                "user_name": "sensor-monitor",
                "user_message": prompt,
                "model_mode": "normal",
                "stream": False
            },
            timeout=90.0
        )
        result = resp.json()
        content = result.get("content", "")
        # Validate JSON response
        try:
            parsed = json.loads(content)
            return {"project": "ihi", "status": resp.status_code, "latency": result.get("latency_ms", 0), "parsed": True}
        except:
            return {"project": "ihi", "status": resp.status_code, "latency": result.get("latency_ms", 0), "parsed": False}
    except Exception as e:
        return {"project": "ihi", "error": str(e)[:50]}
```

- [ ] **Step 6: Run test for 5 minutes to verify**

Run: `timeout 310 python scripts/test_multi_project_load.py` (5min test)
Expected: Warmup + Light phase complete with metrics output

- [ ] **Step 7: Add real-time reporting**

```python
def print_phase_summary(result: PhaseResult):
    print(f"\n{'='*60}")
    print(f"PHASE: {result.name}")
    print(f"{'='*60}")
    print(f"Duration: {result.duration_s:.0f}s | Requests: {result.total_requests}")
    print(f"Fanpage: {result.fanpage_ok}/{result.total_requests} OK ({result.fanpage_ok/result.total_requests*100:.0f}%)")
    print(f"Vehix: {result.vehix_ok}/{result.total_requests} OK ({result.vehix_ok/result.total_requests*100:.0f}%)")
    print(f"IHI: {result.ihi_ok}/{result.total_requests} OK ({result.ihi_ok/result.total_requests*100:.0f}%)")
    print(f"Avg Latency: {result.avg_latency:.0f}ms | P95: {result.p95_latency:.0f}ms")
    if result.errors:
        print(f"Errors: {len(result.errors)}")
```

- [ ] **Step 8: Commit**

```bash
git add scripts/test_multi_project_load.py
git commit -m "feat(test): add multi-project load test orchestrator"
```

---

### Task 2: Generate Vehix Test Data

**Files:**
- Create: `scripts/generate_vehix_data.py`

- [ ] **Step 1: Write vehicle/fleet data generator**

```python
#!/usr/bin/env python3
"""Generate realistic Vehix test data for load testing."""
import json
import random
from datetime import datetime, timedelta

VEHICLES = [
    {"plate": "51A-12345", "brand": "Toyota", "model": "Camry", "year": 2022},
    {"plate": "51B-67890", "brand": "Honda", "model": "CR-V", "year": 2023},
    # ... 50 vehicles
]

CONTRACT_STATUSES = ["ACTIVE", "IDLE", "MAINTENANCE", "EXPIRED"]

def generate_fleet_data(output_file="fleet_test_data.json"):
    """Generate fleet test data."""
    contracts = []
    for i in range(100):
        vehicle = random.choice(VEHICLES)
        contracts.append({
            "contract_id": f"VHX-2024-{i+1:04d}",
            "vehicle": vehicle,
            "customer": f"Customer {i+1}",
            "phone": f"0{random.randint(900000000, 999999999)}",
            "status": random.choice(CONTRACT_STATUSES),
            "start_date": (datetime.now() - timedelta(days=random.randint(1, 365)).isoformat(),
            "end_date": (datetime.now() + timedelta(days=random.randint(1, 90)).isoformat(),
        })
    
    with open(output_file, "w") as f:
        json.dump(contracts, f, indent=2)
    print(f"Generated {len(contracts)} contracts to {output_file}")

if __name__ == "__main__":
    generate_fleet_data()
```

- [ ] **Step 2: Run and commit**

```bash
python scripts/generate_vehix_data.py
git add scripts/generate_vehix_data.py scripts/fleet_test_data.json
git commit -m "feat(test): add Vehix fleet test data generator"
```

---

### Task 3: Generate IHI Sensor Data

**Files:**
- Create: `scripts/generate_ihi_data.py`

- [ ] **Step 1: Write IHI sensor patterns generator**

```python
#!/usr/bin/env python3
"""Generate IHI sensor patterns for load testing."""
import json
import random

PATTERNS = {
    "normal": {"power_range": (5, 30), "vibration_range": (0.1, 2.5), "temp_range": (30, 60)},
    "idle": {"power_range": (0, 2), "vibration_range": (0, 0.3), "temp_range": (25, 35)},
    "warning": {"power_range": (30, 45), "vibration_range": (2.5, 4.5), "temp_range": (60, 85)},
    "critical": {"power_range": (45, 60), "vibration_range": (4.5, 8.0), "temp_range": (85, 100)},
}

def generate_sensor_reading(machine_id: str, pattern: str = "normal") -> dict:
    """Generate single sensor reading."""
    p = PATTERNS.get(pattern, PATTERNS["normal"])
    return {
        "machine_id": machine_id,
        "timestamp": "2026-05-30T12:00:00Z",
        "power_kW": round(random.uniform(*p["power_range"]), 1),
        "vibration_mm_s": round(random.uniform(*p["vibration_range"]), 2),
        "temperature_c": round(random.uniform(*p["temp_range"]), 1),
        "efficiency": round(random.uniform(0.6, 0.95), 3),
    }

def generate_compressed_line_data(num_machines: int = 35) -> str:
    """Generate compressed sensor data string."""
    readings = []
    for i in range(num_machines):
        mid = f"M{i+1:03d}"
        pattern = random.choices(["normal", "idle", "warning", "critical"], weights=[70, 15, 10, 5])[0]
        r = generate_sensor_reading(mid, pattern)
        readings.append(f'{mid}:T{r["temperature_c"]}V{r["vibration_mm_s"]}P{r["power_kW"]}E{r["efficiency"]}')
    return ",".join(readings)
```

- [ ] **Step 2: Run and commit**

```bash
python scripts/generate_ihi_data.py
git add scripts/generate_ihi_data.py
git commit -m "feat(test): add IHI sensor data generator"
```

---

### Task 4: Final Integration Test

**Files:**
- Modify: `scripts/test_multi_project_load.py` (add signal handling, graceful shutdown)

- [ ] **Step 1: Add graceful shutdown handling**

```python
import signal
import sys

shutdown_requested = False

def signal_handler(sig, frame):
    global shutdown_requested
    print("\nShutdown requested...")
    shutdown_requested = True

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

async def main():
    results = []
    for phase_name, start_min, end_min in PHASES:
        if shutdown_requested:
            break
        duration = end_min - start_min
        result = await run_phase(phase_name, duration, intensity=PHASE_INTENSITY.get(phase_name, 1.0))
        results.append(result)
        print_phase_summary(result)
    
    # Final summary
    print_final_summary(results)
```

- [ ] **Step 2: Run full 5-minute integration test**

Run: `timeout 310 python scripts/test_multi_project_load.py`
Expected: All phases complete with metrics

- [ ] **Step 3: Commit**

```bash
git add scripts/test_multi_project_load.py
git commit -m "feat(test): add graceful shutdown to multi-project load test"
```

---

## Summary

| Task | Files | Goal |
|------|-------|------|
| 1 | test_multi_project_load.py | Main orchestrator |
| 2 | generate_vehix_data.py | Fleet test data |
| 3 | generate_ihi_data.py | Sensor patterns |
| 4 | Integration | Full 1-hour test |

---

**Plan complete and saved to `docs/superpowers/plans/2026-05-30-multi-project-load-test.md`**

**Two execution options:**

**1. Subagent-Driven (recommended)** - dispatch per task

**2. Inline Execution** - execute in this session

Which approach?