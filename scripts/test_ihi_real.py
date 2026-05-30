"""
Test IHI project with real-like sensor data.
Run: python scripts/test_ihi_real.py
"""
import asyncio
import httpx
import json
import random
import os
import time
from datetime import datetime

def generate_sensor_data(num_devices=45):
    """Generate sensor data matching pdm_optimization expected format."""
    data = []
    for i in range(num_devices):
        device_id = f"Motor-{i+1:03d}"
        is_abnormal = random.random() < 0.2
        if is_abnormal:
            temp = random.choice([92, 95, 88, 91])
            vibration = random.choice([5.2, 6.1, 4.8, 7.0])
            current = random.choice([78, 82, 76, 85])
        else:
            temp = round(random.uniform(35, 65), 1)
            vibration = round(random.uniform(0.5, 3.5), 2)
            current = round(random.uniform(25, 55), 1)
        data.append({
            "device_id": device_id,
            "timestamp": datetime.now().isoformat(),
            "temperature_c": temp,
            "vibration_mm_s": vibration,
            "current_a": current,
        })
    return data

async def test_ihi_real():
    print("=" * 60)
    print("IHI Real Sensor Data Test")
    print("=" * 60)

    sensor_data = generate_sensor_data(45)
    print(f"\nGenerated {len(sensor_data)} sensor readings")

    abnormal = [d for d in sensor_data if d["temperature_c"] > 90 or d["vibration_mm_s"] > 4.5 or d["current_a"] > 75]
    print(f"Abnormal devices: {len(abnormal)}")
    for d in abnormal[:5]:
        print(f"  {d['device_id']}: T={d['temperature_c']} V={d['vibration_mm_s']} I={d['current_a']}")

    data_str = json.dumps(sensor_data, indent=2)
    prompt = f"Analyze {len(sensor_data)} sensors. Output JSON with abnormal device_ids:\n{data_str}\n\nJSON:"

    api_key = os.getenv("AIHUB_API_KEY", "1XteCCQ_s_UbrqOGEIYybmDBnokWhxYap90D6_Jojx8")
    print("\nCalling AIHub...")
    start = time.time()

    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(
            "http://localhost:8000/v1/chat",
            headers={"X-API-KEY": api_key},
            json={
                "project_id": "ihi",
                "user_message": prompt,
                "model_mode": "normal",
                "stream": False
            }
        )

        elapsed = time.time() - start

        if response.status_code == 200:
            result = response.json()
            latency = result.get("latency_ms", 0)
            content = result.get("content", "")
            print(f"\nOK! Latency: {latency}ms ({elapsed:.2f}s total)")
            print(f"Content: {content[:500]}")
        else:
            print(f"Error {response.status_code}: {response.text[:200]}")

if __name__ == "__main__":
    asyncio.run(test_ihi_real())
