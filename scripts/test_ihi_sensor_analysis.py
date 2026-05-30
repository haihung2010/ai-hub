"""
Test AIHub ihi project với simulated sensor data.
Chạy: python scripts/test_ihi_sensor_analysis.py
"""
import asyncio
import json
import random
from datetime import datetime

BATCH_SIZE = 8   # chia nho: 8 devices/batch de khong vuot context window


def generate_sensor_data(num_devices=45):
    devices = []
    for i in range(num_devices):
        device_id = f"Motor-{i+1:03d}"
        is_abnormal = random.random() < 0.2  # 20% abnormal

        if is_abnormal:
            temp = random.choice([92, 95, 88, 91])
            vibration = random.choice([5.2, 6.1, 4.8, 7.0])
            current = random.choice([78, 82, 76, 85])
        else:
            temp = random.uniform(35, 65)
            vibration = random.uniform(0.5, 3.5)
            current = random.uniform(25, 55)

        devices.append({
            "device_id": device_id,
            "timestamp": datetime.now().isoformat(),
            "temperature_c": round(temp, 1),
            "vibration_mm_s": round(vibration, 2),
            "current_a": round(current, 1),
            "power_kw": round(random.uniform(5, 25), 2),
            "voltage_v": round(random.uniform(380, 420), 1),
            "frequency_hz": round(random.uniform(49.5, 50.5), 2),
            "power_factor": round(random.uniform(0.85, 0.95), 3)
        })

    return devices


async def call_aihub(client, prompt, api_key):
    response = await client.post(
        "http://localhost:8000/v1/chat",
        headers={"X-API-KEY": api_key},
        json={
            "project_id": "ihi",
            "user_message": prompt,
            "max_tokens": 200,
            "stream": False
        }
    )
    response.raise_for_status()
    return response.json()


def build_batch_prompt(devices):
    """Prompt nho gon, chi gui device_id + cac gia tri can thiet."""
    data_str = json.dumps(devices, indent=2, default=str)
    prompt = f"""Phan tich {len(devices)} thiet bi. Chi tra JSON:

DANH SACH:
{data_str}

NGUONG: nhiet do>90°C, do rung>4.5mm/s, dong dien>75A = nguy hiem.
LOI: Mechanical, Electrical, Hydraulic, Overheating, Vibration, Corrosion.
MUC DO: High, Medium, Low.

Chi tra JSON khong co markdown:
{{"equipment_health": [{{"device_id":"...","failure":"...","priority":"...","description":"..."}}]}}

Neu khong co loi: {{"equipment_health": []}}"""
    return prompt


async def test_ihi_analysis():
    print("=" * 60)
    print("IHI Sensor Analysis - batched by MiniMax context window")
    print("=" * 60)

    sensor_data = generate_sensor_data(45)
    print(f"\nGenerated {len(sensor_data)} devices")
    print(f"Batch size: {BATCH_SIZE} devices")

    # chi batch những device có vấn đề tiềm năng
    abnormal = [d for d in sensor_data
                if d["temperature_c"] > 70 or d["vibration_mm_s"] > 3.5 or d["current_a"] > 60]
    print(f"Pre-filtered (could be abnormal): {len(abnormal)}")

    # Neu nhieu abnormal, lay tat ca; neu it thi chi lay nhung cai can chu ý
    if len(abnormal) > 30:
        to_analyze = abnormal[:30]
    else:
        to_analyze = abnormal if abnormal else sensor_data[:20]

    print(f"Analyzing: {len(to_analyze)} devices in batches of {BATCH_SIZE}")

    import httpx
    import os
    import time
    from dotenv import load_dotenv

    load_dotenv("/home/hung/ai-hub/.env")
    api_key = os.getenv("AIHUB_API_KEY", "") or os.getenv("API_KEY", "")

    all_issues = []
    total_time = 0

    async with httpx.AsyncClient(timeout=120.0) as client:
        for i in range(0, len(to_analyze), BATCH_SIZE):
            batch = to_analyze[i:i + BATCH_SIZE]
            batch_num = i // BATCH_SIZE + 1
            total_batches = (len(to_analyze) + BATCH_SIZE - 1) // BATCH_SIZE
            print(f"\n--- Batch {batch_num}/{total_batches}: {len(batch)} devices ---")

            prompt = build_batch_prompt(batch)
            print(f"Prompt: {len(prompt)} chars (~{len(prompt)//4} tokens)")

            start = time.time()
            try:
                result = await call_aihub(client, prompt, api_key)
                elapsed = time.time() - start
                total_time += elapsed

                content = result.get("content", "")
                print(f"OK! {elapsed:.1f}s - {content[:100]}")

                # Parse JSON
                try:
                    parsed = json.loads(content)
                    issues = parsed.get("equipment_health", [])
                    for issue in issues:
                        all_issues.append(issue)
                        print(f"  [{issue.get('priority','?')}] {issue.get('device_id','?')}: {issue.get('failure','?')}")
                except json.JSONDecodeError:
                    print(f"  Parse error, raw: {content[:200]}")
            except Exception as e:
                print(f"  Error: {e}")

    print(f"\n{'='*60}")
    print(f"TOTAL: {len(all_issues)} issues from {len(to_analyze)} devices in {total_time:.1f}s")
    print(f"{'='*60}")
    for issue in all_issues:
        print(f"  [{issue.get('priority','?')}] {issue.get('device_id','?')}: {issue.get('failure','?')}")


if __name__ == "__main__":
    asyncio.run(test_ihi_analysis())
