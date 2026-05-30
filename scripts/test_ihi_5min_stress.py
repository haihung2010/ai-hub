"""
IHI Stress Test: 2 requests/minute for 5 minutes
Each request has different sensor data simulating real-time changes

Run: python scripts/test_ihi_5min_stress.py
"""
import asyncio
import httpx
import json
import random
import time
import os
from datetime import datetime
from typing import List

API_KEY = "1XteCCQ_s_UbrqOGEIYybmDBnokWhxYap90D6_Jojx8"
BASE_URL = "http://localhost:8000"

def generate_sensor_data(num_devices=45, time_offset_minutes=0) -> List[dict]:
    """
    Generate sensor data with time-based variations.
    Abnormal devices change over time to simulate real equipment degradation.
    """
    # Seed based on time to create different data each run
    random.seed(int(time.time() / 60) + time_offset_minutes)

    data = []
    for i in range(num_devices):
        device_id = f"Motor-{i+1:03d}"

        # Time-based abnormal pattern (some devices degrade over time)
        minute = time_offset_minutes
        is_abnormal = random.random() < (0.15 + 0.05 * (minute % 10))  # 15-20% abnormal, varies

        if is_abnormal:
            # Abnormal values that get progressively worse
            base_temp = random.choice([88, 90, 92, 95])
            base_vib = random.choice([4.8, 5.2, 5.8, 6.1, 7.0])
            base_current = random.choice([76, 78, 80, 82, 85])

            # Add some time-based degradation
            temp = base_temp + (minute % 5)
            vibration = round(base_vib + (minute % 3) * 0.2, 2)
            current = base_current + (minute % 4)
        else:
            temp = round(random.uniform(30, 75), 1)
            vibration = round(random.uniform(0.3, 4.0), 2)
            current = round(random.uniform(20, 60), 1)

        data.append({
            "device_id": device_id,
            "timestamp": datetime.now().isoformat(),
            "temperature_c": temp,
            "vibration_mm_s": vibration,
            "current_a": current,
            # Add calculated fields
            "rpm": random.randint(1400, 1800),
            "power_kw": round(random.uniform(5, 30), 2),
        })
    return data


async def call_ihi_check(client: httpx.AsyncClient, sensor_data: List[dict], call_num: int) -> dict:
    """Call IHI with sensor data."""
    data_str = json.dumps(sensor_data, indent=2)

    prompt = f"""Bạn là chuyên gia bảo trì thiết bị công nghiệp. Phân tích dữ liệu cảm biến và trả về JSON thuần.

THỜI GIAN: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

QUY TẮC PHÁT HIỆN:
- Nguy hiểm (DANGER): temperature > 90°C HOẶC vibration > 6.0mm/s HOẶC current > 75A
- Cảnh báo (WARNING): temperature > 85°C HOẶC vibration > 4.5mm/s HOẶC current > 65A
- Bình thường (NORMAL): không thỏa điều kiện trên

Dữ liệu cảm biến ({len(sensor_data)} thiết bị):
{data_str}

Trả về JSON format (không có text khác):
{{
  "timestamp": "{datetime.now().isoformat()}",
  "total_devices": {len(sensor_data)},
  "danger": [{{"device_id": "...", "temp": N, "vib": N, "current": N, "reason": "..."}}],
  "warning": [{{"device_id": "...", "temp": N, "vib": N, "current": N, "reason": "..."}}],
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
                "user_name": "sensor-monitor",
                "user_message": prompt,
                "model_mode": "normal",
                "stream": False
            },
            timeout=90.0
        )
        elapsed = time.time() - start
        result = resp.json()

        content = result.get("content", "")
        parsed = None
        try:
            parsed = json.loads(content)
        except:
            pass

        return {
            "call_num": call_num,
            "time": datetime.now().strftime("%H:%M:%S"),
            "status": resp.status_code,
            "latency_ms": result.get("latency_ms", 0),
            "elapsed_ms": round(elapsed * 1000),
            "devices": len(sensor_data),
            "danger_count": len(parsed.get("danger", [])) if parsed else -1,
            "warning_count": len(parsed.get("warning", [])) if parsed else -1,
            "normal_count": parsed.get("normal_count", -1) if parsed else -1,
            "content_preview": content[:150] if content else "EMPTY"
        }
    except Exception as e:
        return {"call_num": call_num, "time": datetime.now().strftime("%H:%M:%S"), "status": 0, "error": str(e)}


async def run_stress_test():
    print("=" * 70)
    print("IHI STRESS TEST: 2 req/min for 5 minutes")
    print(f"Start: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    results = []
    start_time = time.time()

    async with httpx.AsyncClient() as client:
        call_num = 0

        # Run for 5 minutes = 300 seconds = 10 calls (2 per minute)
        while time.time() - start_time < 300:
            call_num += 1

            # Generate data with time offset
            time_offset = call_num - 1  # minutes offset
            sensor_data = generate_sensor_data(45, time_offset)

            # Count actual abnormal in data for verification
            actual_danger = sum(1 for d in sensor_data if d['temperature_c'] > 90 or d['vibration_mm_s'] > 6.0 or d['current_a'] > 75)
            actual_warning = sum(1 for d in sensor_data if 85 < d['temperature_c'] <= 90 or 4.5 < d['vibration_mm_s'] <= 6.0 or 65 < d['current_a'] <= 75)

            print(f"\n[{time.time() - start_time:.0f}s] Call #{call_num} at {datetime.now().strftime('%H:%M:%S')}")
            print(f"  Data: {len(sensor_data)} devices | Actual danger={actual_danger}, warning={actual_warning}")

            result = await call_ihi_check(client, sensor_data, call_num)
            result["actual_danger"] = actual_danger
            result["actual_warning"] = actual_warning
            results.append(result)

            # Print result
            if result.get("status") == 200:
                print(f"  Response: danger={result.get('danger_count')}, warning={result.get('warning_count')}, normal={result.get('normal_count')}")
                print(f"  Latency: {result.get('latency_ms', 0):.0f}ms ({result.get('elapsed_ms')/1000:.1f}s)")

                # Check if model detected correctly
                detected_total = (result.get('danger_count', 0) or 0) + (result.get('warning_count', 0) or 0)
                actual_total = actual_danger + actual_warning
                if detected_total > 0:
                    diff = abs(detected_total - actual_total)
                    print(f"  Accuracy: detected={detected_total}, actual={actual_total}, diff={diff}")
            else:
                print(f"  ERROR: {result.get('error', result.get('content_preview', 'Unknown'))}")

            # Wait 30 seconds for next call (2 per minute)
            if call_num < 10:  # Don't wait after last call
                await asyncio.sleep(30)

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)

    duration = time.time() - start_time
    successful = [r for r in results if r.get("status") == 200]

    print(f"Duration: {duration:.1f}s")
    print(f"Total calls: {len(results)}")
    print(f"Successful: {len(successful)}/{len(results)}")

    if successful:
        avg_latency = sum(r.get("latency_ms", 0) for r in successful) / len(successful)
        print(f"Avg latency: {avg_latency:.0f}ms")

        # Accuracy analysis
        print("\nAccuracy Analysis:")
        for r in successful:
            detected = (r.get('danger_count') or 0) + (r.get('warning_count') or 0)
            actual = r.get('actual_danger', 0) + r.get('actual_warning', 0)
            diff = abs(detected - actual) if actual > 0 or detected > 0 else 0
            acc = "✓" if diff <= 2 else "✗"
            print(f"  [{acc}] Call {r.get('call_num')} @ {r.get('time')}: detected={detected}, actual={actual}, diff={diff}")

    if len(successful) == len(results):
        print(f"\n✅ ALL {len(results)} CALLS PASSED")
    else:
        print(f"\n⚠️ {len(successful)}/{len(results)} CALLS SUCCESSFUL")


if __name__ == "__main__":
    asyncio.run(run_stress_test())