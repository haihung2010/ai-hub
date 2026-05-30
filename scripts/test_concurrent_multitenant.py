"""
Multi-tenant concurrent test:
- 10 fanpage users (casual chat, lite mode)
- 1 IHI sensor check every 30s

Run: python scripts/test_concurrent_multitenant.py
"""
import asyncio
import httpx
import json
import random
import time
import os
from datetime import datetime
from typing import List

API_KEY = os.getenv("AIHUB_API_KEY", "1XteCCQ_s_UbrqOGEIYybmDBnokWhxYap90D6_Jojx8")
BASE_URL = "http://localhost:8000"

# Fanpage products for casual chat
FANPAGE_PRODUCTS = [
    "tư vấn mua laptop cho sinh viên",
    "so sánh iPhone và Samsung",
    "hỏi về chính sách đổi trả",
    "cách đặt hàng online",
    "xem trạng thái đơn hàng #12345",
    "hỏi về khuyến mãi cuối năm",
    "hướng dẫn thanh toán",
    "laptop cho lập trình viên",
    "điện thoại chụp ảnh đẹp",
    "mua quạt cho phòng trọ",
]

FANPAGE_TENANTS = ["tenant_a", "tenant_b", "tenant_c", "tenant_d", "tenant_e"]
FANPAGE_USERS = [f"user_{i:02d}" for i in range(1, 11)]


def generate_sensor_data(num_devices=45) -> List[dict]:
    """Generate sensor data matching IHI expected format."""
    data = []
    for i in range(num_devices):
        device_id = f"Motor-{i+1:03d}"
        # 20% abnormal
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


async def call_fanpage(client: httpx.AsyncClient, user_name: str, tenant_id: str, message: str) -> dict:
    """Call fanpage chatbot."""
    start = time.time()
    try:
        resp = await client.post(
            f"{BASE_URL}/v1/chat",
            headers={"X-API-KEY": API_KEY},
            json={
                "project_id": "fanpage",
                "tenant_id": tenant_id,
                "user_name": user_name,
                "user_message": message,
                "model_mode": "lite",
                "stream": False
            },
            timeout=30.0
        )
        elapsed = time.time() - start
        result = resp.json()
        return {
            "type": "fanpage",
            "user": user_name,
            "tenant": tenant_id,
            "status": resp.status_code,
            "latency_ms": result.get("latency_ms", 0),
            "elapsed": round(elapsed * 1000, 1),
            "content_preview": result.get("content", "")[:100] if resp.status_code == 200 else result.get("detail", "")[:100]
        }
    except Exception as e:
        return {"type": "fanpage", "user": user_name, "tenant": tenant_id, "status": 0, "error": str(e)}


async def call_ihi_sensor_check(client: httpx.AsyncClient, sensor_data: List[dict]) -> dict:
    """Call IHI sensor check."""
    data_str = json.dumps(sensor_data[:45], indent=2)
    prompt = f"""Phân tích {len(sensor_data)} cảm biến. Chỉ trả JSON thuần với danh sách thiết bị bất thường (temp>90°C, vibration>4.5mm/s, current>75A).

{data_str}

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
            timeout=60.0
        )
        elapsed = time.time() - start
        result = resp.json()
        return {
            "type": "ihi",
            "status": resp.status_code,
            "latency_ms": result.get("latency_ms", 0),
            "elapsed": round(elapsed * 1000, 1),
            "content_preview": result.get("content", "")[:150] if resp.status_code == 200 else result.get("detail", "")[:100]
        }
    except Exception as e:
        return {"type": "ihi", "status": 0, "error": str(e)}


async def run_concurrent_test():
    print("=" * 70)
    print("MULTI-TENANT CONCURRENT TEST")
    print("=" * 70)
    print(f"Fanpage: 10 users (5 tenants x 2 users)")
    print(f"IHI: sensor check every 30s")
    print(f"Duration: ~2 minutes")
    print("=" * 70)

    results = []
    sensor_data = generate_sensor_data(45)
    start_time = time.time()

    async with httpx.AsyncClient() as client:
        # Round 1: fanpage users + first IHI call
        print(f"\n[{time.time() - start_time:.1f}s] Round 1: 10 fanpage calls + IHI sensor check")
        fanpage_tasks = [
            call_fanpage(client, FANPAGE_USERS[i], FANPAGE_TENANTS[i % 5], random.choice(FANPAGE_PRODUCTS))
            for i in range(10)
        ]
        ihi_task = call_ihi_sensor_check(client, sensor_data)

        all_tasks = fanpage_tasks + [ihi_task]
        results_r1 = await asyncio.gather(*all_tasks)
        results.extend(results_r1)

        fanpage_ok = sum(1 for r in results_r1[:10] if r.get("status") == 200)
        ihi_ok = results_r1[10].get("status") == 200
        print(f"  Fanpage: {fanpage_ok}/10 OK, IHI: {'OK' if ihi_ok else 'FAIL'}")

        # Round 2: fanpage users (30s interval)
        await asyncio.sleep(30)
        print(f"\n[{time.time() - start_time:.1f}s] Round 2: 10 fanpage calls")
        fanpage_tasks = [
            call_fanpage(client, FANPAGE_USERS[i], FANPAGE_TENANTS[i % 5], random.choice(FANPAGE_PRODUCTS))
            for i in range(10)
        ]
        results_r2 = await asyncio.gather(*fanpage_tasks)
        results.extend(results_r2)
        fanpage_ok = sum(1 for r in results_r2 if r.get("status") == 200)
        print(f"  Fanpage: {fanpage_ok}/10 OK")

        # Round 3: IHI sensor check (30s interval)
        await asyncio.sleep(30)
        print(f"\n[{time.time() - start_time:.1f}s] Round 3: IHI sensor check")
        sensor_data = generate_sensor_data(45)
        result_ihi = await call_ihi_sensor_check(client, sensor_data)
        results.append(result_ihi)
        ihi_ok = result_ihi.get("status") == 200
        print(f"  IHI: {'OK' if ihi_ok else 'FAIL'} - latency={result_ihi.get('latency_ms', 0)}ms")

        # Round 4: fanpage users
        await asyncio.sleep(30)
        print(f"\n[{time.time() - start_time:.1f}s] Round 4: 10 fanpage calls")
        fanpage_tasks = [
            call_fanpage(client, FANPAGE_USERS[i], FANPAGE_TENANTS[i % 5], random.choice(FANPAGE_PRODUCTS))
            for i in range(10)
        ]
        results_r4 = await asyncio.gather(*fanpage_tasks)
        results.extend(results_r4)
        fanpage_ok = sum(1 for r in results_r4 if r.get("status") == 200)
        print(f"  Fanpage: {fanpage_ok}/10 OK")

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)

    total_duration = time.time() - start_time
    fanpage_results = [r for r in results if r.get("type") == "fanpage"]
    ihi_results = [r for r in results if r.get("type") == "ihi"]

    fanpage_ok = sum(1 for r in fanpage_results if r.get("status") == 200)
    ihi_ok = sum(1 for r in ihi_results if r.get("status") == 200)

    print(f"Duration: {total_duration:.1f}s")
    print(f"Total requests: {len(results)} ({len(fanpage_results)} fanpage + {len(ihi_results)} IHI)")
    print(f"")
    print(f"Fanpage:")
    print(f"  Success: {fanpage_ok}/{len(fanpage_results)}")
    if fanpage_ok > 0:
        avg_latency = sum(r.get("latency_ms", 0) for r in fanpage_results if r.get("status") == 200) / fanpage_ok
        print(f"  Avg latency: {avg_latency:.0f}ms")
    print(f"")
    print(f"IHI:")
    print(f"  Success: {ihi_ok}/{len(ihi_results)}")
    if ihi_ok > 0:
        avg_latency = sum(r.get("latency_ms", 0) for r in ihi_results if r.get("status") == 200) / ihi_ok
        print(f"  Avg latency: {avg_latency:.0f}ms")

    if fanpage_ok == len(fanpage_results) and ihi_ok == len(ihi_results):
        print(f"\n✅ ALL TESTS PASSED")
    else:
        failed = [r for r in results if r.get("status") != 200]
        for f in failed[:5]:
            print(f"\n❌ FAILED: {f}")


if __name__ == "__main__":
    asyncio.run(run_concurrent_test())