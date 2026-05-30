"""
IHI Load Test with Real Sensor Data
Reads sensor data from influx backup, splits into batches, tests E2B responses

Run: python scripts/test_ihi_real_sensor.py
"""
import asyncio
import httpx
import json
import random
import time
import os
import gzip
import io
from datetime import datetime
from typing import List

API_KEY = os.getenv("AIHUB_API_KEY", "1XteCCQ_s_UbrqOGEIYybmDBnokWhxYap90D6_Jojx8")
BASE_URL = "http://localhost:8000"


def load_mock_sensor_data(num_devices=45) -> List[dict]:
    """Generate realistic sensor data."""
    data = []
    for i in range(num_devices):
        device_id = f"Motor-{i+1:03d}"
        is_abnormal = random.random() < 0.2
        if is_abnormal:
            temp = random.choice([92, 95, 88, 91, 93])
            vibration = random.choice([5.2, 6.1, 4.8, 7.0, 5.8])
            current = random.choice([78, 82, 76, 85, 80])
        else:
            temp = round(random.uniform(30, 70), 1)
            vibration = round(random.uniform(0.3, 4.0), 2)
            current = round(random.uniform(20, 60), 1)

        data.append({
            "device_id": device_id,
            "timestamp": datetime.now().isoformat(),
            "temperature_c": temp,
            "vibration_mm_s": vibration,
            "current_a": current,
            "power_kw": round(random.uniform(5, 30), 2),
            "rpm": random.randint(1400, 1800),
        })
    return data


async def test_ihi_batch(client: httpx.AsyncClient, sensor_data: List[dict], batch_id: int) -> dict:
    """Test IHI with a batch of sensor data."""
    data_str = json.dumps(sensor_data, indent=2)

    # Prompt for equipment health analysis
    prompt = f"""Bạn là chuyên gia bảo trì thiết bị công nghiệp. Phân tích dữ liệu cảm biến và trả về JSON thuần.

Dữ liệu cảm biến ({len(sensor_data)} thiết bị):
{data_str}

Quy tắc phát hiện bất thường:
- Nguy hiểm: temperature > 90°C HOẶC current > 75A HOẶC vibration > 6.0mm/s
- Cảnh báo: temperature > 85°C HOẶC current > 65A HOẶC vibration > 4.5mm/s
- Bình thường: không thỏa điều kiện trên

Trả về JSON format:
{{
  "summary": {{"total": N, "abnormal": N, "warning": N, "normal": N}},
  "danger": [{{"device_id": "Motor-XXX", "reason": "...", "values": {{...}}}}],
  "warning": [{{"device_id": "Motor-XXX", "reason": "...", "values": {{...}}}}],
  "normal_count": N
}}

JSON:"""

    start = time.time()
    try:
        resp = await client.post(
            f"{BASE_URL}/v1/chat",
            headers={"X-API-KEY": API_KEY},
            json={
                "project_id": "ihi",
                "tenant_id": "ihi-tenant",
                "user_name": "sensor-system",
                "user_message": prompt,
                "model_mode": "normal",
                "stream": False
            },
            timeout=90.0
        )
        elapsed = time.time() - start
        result = resp.json()

        if resp.status_code == 200:
            content = result.get("content", "")
            # Try to parse JSON
            try:
                parsed = json.loads(content)
                abnormal_count = len(parsed.get("danger", [])) + len(parsed.get("warning", []))
            except:
                abnormal_count = -1

            return {
                "batch_id": batch_id,
                "status": 200,
                "devices": len(sensor_data),
                "latency_ms": result.get("latency_ms", 0),
                "elapsed_ms": round(elapsed * 1000, 1),
                "abnormal_count": abnormal_count,
                "content_preview": content[:200]
            }
        else:
            return {
                "batch_id": batch_id,
                "status": resp.status_code,
                "error": result.get("detail", "")[:100]
            }
    except Exception as e:
        return {"batch_id": batch_id, "status": 0, "error": str(e)}


async def run_ihi_load_test():
    print("=" * 70)
    print("IHI SENSOR DATA LOAD TEST")
    print("=" * 70)

    # Generate sensor data batches
    all_sensor_data = load_mock_sensor_data(45)

    # Split into 3 batches
    batch_size = 15
    batches = [all_sensor_data[i:i+batch_size] for i in range(0, len(all_sensor_data), batch_size)]

    print(f"Total devices: {len(all_sensor_data)}")
    print(f"Batch size: {batch_size}")
    print(f"Number of batches: {len(batches)}")
    print("=" * 70)

    results = []
    start_time = time.time()

    async with httpx.AsyncClient() as client:
        # Batch 1
        print(f"\n[{time.time() - start_time:.1f}s] Batch 1: {len(batches[0])} devices")
        result1 = await test_ihi_batch(client, batches[0], 1)
        results.append(result1)
        print(f"  Status: {result1.get('status')}, Latency: {result1.get('latency_ms', 0)}ms, Abnormal: {result1.get('abnormal_count', 'N/A')}")

        # Batch 2
        print(f"\n[{time.time() - start_time:.1f}s] Batch 2: {len(batches[1])} devices")
        result2 = await test_ihi_batch(client, batches[1], 2)
        results.append(result2)
        print(f"  Status: {result2.get('status')}, Latency: {result2.get('latency_ms', 0)}ms, Abnormal: {result2.get('abnormal_count', 'N/A')}")

        # Batch 3
        print(f"\n[{time.time() - start_time:.1f}s] Batch 3: {len(batches[2])} devices")
        result3 = await test_ihi_batch(client, batches[2], 3)
        results.append(result3)
        print(f"  Status: {result3.get('status')}, Latency: {result3.get('latency_ms', 0)}ms, Abnormal: {result3.get('abnormal_count', 'N/A')}")

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)

    total_duration = time.time() - start_time
    successful = [r for r in results if r.get("status") == 200]

    print(f"Duration: {total_duration:.1f}s")
    print(f"Total batches: {len(results)}")
    print(f"Successful: {len(successful)}/{len(results)}")

    if successful:
        avg_latency = sum(r.get("latency_ms", 0) for r in successful) / len(successful)
        total_devices = sum(r.get("devices", 0) for r in successful)
        print(f"Avg latency: {avg_latency:.0f}ms")
        print(f"Total devices processed: {total_devices}")

        # Check if responses are valid JSON
        for r in successful:
            if r.get("abnormal_count", -1) >= 0:
                print(f"  Batch {r.get('batch_id')}: {r.get('abnormal_count')} abnormal detected")

    if len(successful) == len(results):
        print(f"\n✅ ALL BATCHES PASSED")
    else:
        failed = [r for r in results if r.get("status") != 200]
        for f in failed:
            print(f"\n❌ Batch {f.get('batch_id')} FAILED: {f}")


if __name__ == "__main__":
    asyncio.run(run_ihi_load_test())