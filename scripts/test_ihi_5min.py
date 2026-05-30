"""
IHI Stress Test: 2 req/min for 5 minutes = 10 calls
Run: python scripts/test_ihi_5min.py
"""
import asyncio
import httpx
import json
import random
import time
from datetime import datetime

API_KEY = "1XteCCQ_s_UbrqOGEIYybmDBnokWhxYap90D6_Jojx8"

def gen_sensor_data(n=45):
    data = []
    for i in range(n):
        is_abn = random.random() < 0.2
        if is_abn:
            temp = random.choice([88, 90, 92, 95])
            vib = random.choice([4.8, 5.2, 5.8, 6.1, 7.0])
            cur = random.choice([76, 78, 80, 82, 85])
        else:
            temp = round(random.uniform(30, 70), 1)
            vib = round(random.uniform(0.3, 4.0), 2)
            cur = round(random.uniform(20, 60), 1)
        data.append({"device_id": f"Motor-{i+1:03d}", "temperature_c": temp, "vibration_mm_s": vib, "current_a": cur})
    return data

def count_actual(data):
    danger = sum(1 for d in data if d['temperature_c'] > 90 or d['vibration_mm_s'] > 6 or d['current_a'] > 75)
    warning = sum(1 for d in data if (85 < d['temperature_c'] <= 90) or (4.5 < d['vibration_mm_s'] <= 6) or (65 < d['current_a'] <= 75))
    return danger, warning

async def call_ihi(client, data, call_num):
    ds = json.dumps(data)
    prompt = f"""Analyze {len(data)} sensors. Return JSON only:
Rules: DANGER=temp>90 OR vib>6 OR current>75. WARNING=temp>85 OR vib>4.5 OR current>65.
Data: {ds}
Output: {{"danger":[...],"warning":[...],"normal":N}}
JSON:"""

    start = time.time()
    resp = await client.post(
        "http://localhost:8000/v1/chat",
        headers={"X-API-KEY": API_KEY},
        json={
            "project_id": "ihi",
            "tenant_id": "ihi-tenant",
            "user_name": f"sensor-{call_num}",
            "user_message": prompt,
            "model_mode": "normal",
            "stream": False
        },
        timeout=60.0
    )
    elapsed = time.time() - start
    result = resp.json()

    actual_danger, actual_warning = count_actual(data)

    return {
        "call": call_num,
        "time": datetime.now().strftime("%H:%M:%S"),
        "status": resp.status_code,
        "latency": result.get("latency_ms", 0),
        "elapsed": round(elapsed, 2),
        "actual_danger": actual_danger,
        "actual_warning": actual_warning,
        "content": result.get("content", "")[:200]
    }

async def main():
    print("=" * 60)
    print("IHI STRESS TEST: 2 req/min for 5 minutes")
    print("=" * 60)
    print(f"Start: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    async with httpx.AsyncClient() as client:
        for call_num in range(1, 11):
            data = gen_sensor_data(45)
            result = await call_ihi(client, data, call_num)

            print(f"\n[{result['time']}] Call #{result['call']}")
            print(f"  Actual: danger={result['actual_danger']}, warning={result['actual_warning']}")
            print(f"  Status: {result['status']}, Latency: {result['latency']:.0f}ms ({result['elapsed']}s)")
            print(f"  Content: {result['content']}")

            if call_num < 10:
                await asyncio.sleep(30)

    print("\n" + "=" * 60)
    print("TEST COMPLETE")
    print("=" * 60)

asyncio.run(main())