"""
Multi-Tenant Concurrent Test: Fanpage Chatbot + IHI Sensor Monitoring
- 10 Fanpage users (casual chat) - concurrent burst
- IHI sensor check (135 devices) - every 30s
- Duration: 2 minutes

Run: python scripts/test_mt_concurrent.py
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

# Fanpage test data
FANPAGE_PRODUCTS = [
    "tư vấn mua laptop cho sinh viên kinh tế",
    "so sánh iPhone 16 Pro vs Samsung S25 Ultra",
    "hỏi chính sách đổi trả điện thoại trong 30 ngày",
    "cách đặt hàng online trên website",
    "xem trạng thái đơn hàng #123456",
    "khuyến mãi cuối tháng - giảm 30%",
    "hướng dẫn thanh toán qua QR code",
    "laptop cho lập trình viên Python/Java",
    "điện thoại chụp ảnh đẹp dưới 15 triệu",
    "mua quạt cây cho phòng 20m2",
]
FANPAGE_TENANTS = [f"tenant_{chr(65+i)}" for i in range(5)]  # tenant_A to tenant_E
FANPAGE_USERS = [f"user_{i:02d}" for i in range(1, 11)]

def gen_sensor_data(n=135):
    """Generate realistic sensor data for IHI."""
    data = []
    for i in range(n):
        device_id = f"Motor-{i+1:03d}" if i < 100 else f"Power-{i-99:03d}"
        is_abn = random.random() < 0.2
        if is_abn:
            temp = random.choice([88, 90, 92, 95, 93])
            vib = random.choice([4.8, 5.2, 5.8, 6.1, 7.0, 6.5])
            cur = random.choice([76, 78, 80, 82, 85, 77])
        else:
            temp = round(random.uniform(28, 75), 1)
            vib = round(random.uniform(0.3, 4.2), 2)
            cur = round(random.uniform(18, 62), 1)
        data.append({
            "device_id": device_id,
            "timestamp": datetime.now().isoformat(),
            "temperature_c": temp,
            "vibration_mm_s": vib,
            "current_a": cur,
        })
    return data

async def fanpage_call(client, user_name, tenant_id, message, call_id):
    """Single fanpage chatbot call."""
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
            "call_id": call_id,
            "user": user_name,
            "tenant": tenant_id,
            "status": resp.status_code,
            "latency_ms": result.get("latency_ms", 0),
            "elapsed_ms": round(elapsed * 1000),
            "content_preview": result.get("content", "")[:80] if resp.status_code == 200 else result.get("detail", "")[:80]
        }
    except Exception as e:
        return {"type": "fanpage", "call_id": call_id, "user": user_name, "tenant": tenant_id, "status": 0, "error": str(e)[:80]}

def parse_ihi_response(content: str) -> tuple:
    """Parse IHI response and extract danger/warning counts."""
    try:
        parsed = json.loads(content)
        # Handle different response formats
        if "abnormal" in parsed:
            abnormal = parsed.get("abnormal", [])
            if isinstance(abnormal, list):
                return len(abnormal), 0
        if "danger" in parsed and "warning" in parsed:
            return len(parsed.get("danger", [])), len(parsed.get("warning", []))
        if "abnormal" in parsed and isinstance(parsed.get("abnormal"), list):
            return len(parsed.get("abnormal", [])), 0
    except:
        pass
    return -1, -1

async def ihi_call(client, call_id):
    """Single IHI sensor check with 135 devices."""
    sensor_data = gen_sensor_data(135)
    data_str = json.dumps(sensor_data)

    # STRICTER PROMPT to force JSON format
    prompt = f"""PHÂN TÍCH CẢM BIẾN. TRẢ VỀ JSON THUẦN DUY NHẤT.

QUY TẮC (áp dụng cho TẤT CẢ thiết bị):
- DANGER: temperature > 90°C OR vibration > 6.0mm/s OR current > 75A
- WARNING: 85°C < temperature ≤ 90°C OR 4.5mm/s < vibration ≤ 6.0mm/s OR 65A < current ≤ 75A
- NORMAL: không thỏa DANGER hay WARNING

Dữ liệu cảm biến ({len(sensor_data)} thiết bị):
{data_str}

TRẢ VỀ ĐỊNH DẠNG JSON CHÍNH XÁC (không có text khác ngoài JSON):
{{"danger":["DEVICE_ID",...],"warning":["DEVICE_ID",...],"normal_count":N}}

JSON:"""

    actual_danger = sum(1 for d in sensor_data if d['temperature_c'] > 90 or d['vibration_mm_s'] > 6 or d['current_a'] > 75)
    actual_warning = sum(1 for d in sensor_data if (85 < d['temperature_c'] <= 90) or (4.5 < d['vibration_mm_s'] <= 6) or (65 < d['current_a'] <= 75))

    start = time.time()
    try:
        resp = await client.post(
            f"{BASE_URL}/v1/chat",
            headers={"X-API-KEY": API_KEY},
            json={
                "project_id": "ihi",
                "tenant_id": "ihi-tenant",
                "user_name": f"ihisensor-{call_id}",
                "user_message": prompt,
                "model_mode": "normal",
                "stream": False
            },
            timeout=90.0
        )
        elapsed = time.time() - start
        result = resp.json()

        content = result.get("content", "")
        parsed_danger, parsed_warning = parse_ihi_response(content)

        return {
            "type": "ihi",
            "call_id": call_id,
            "status": resp.status_code,
            "latency_ms": result.get("latency_ms", 0),
            "elapsed_ms": round(elapsed * 1000),
            "actual_danger": actual_danger,
            "actual_warning": actual_warning,
            "parsed_danger": parsed_danger,
            "parsed_warning": parsed_warning,
            "content_preview": content[:150]
        }
    except Exception as e:
        return {"type": "ihi", "call_id": call_id, "status": 0, "error": str(e)[:80]}

async def run_concurrent_test():
    print("=" * 70)
    print("MULTI-TENANT CONCURRENT TEST")
    print("Fanpage: 10 users (burst) | IHI: 135 devices (30s interval)")
    print(f"Start: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    all_results = []
    start_time = time.time()
    call_counter = [0]

    async def get_call_id():
        call_counter[0] += 1
        return call_counter[0]

    async with httpx.AsyncClient() as client:
        # Round 1: Fanpage burst (10 concurrent) + IHI call
        print(f"\n[0s] ROUND 1: 10 Fanpage + 1 IHI")
        fanpage_tasks = [
            fanpage_call(client, FANPAGE_USERS[i], FANPAGE_TENANTS[i % 5],
                        random.choice(FANPAGE_PRODUCTS), await get_call_id())
            for i in range(10)
        ]
        ihi_task = ihi_call(client, await get_call_id())

        results_r1 = await asyncio.gather(*fanpage_tasks, ihi_task)
        all_results.extend(results_r1)

        fp_ok = sum(1 for r in results_r1[:10] if r.get("status") == 200)
        ihi_ok = results_r1[10].get("status") == 200
        print(f"  Fanpage: {fp_ok}/10 OK | IHI: {'OK' if ihi_ok else 'FAIL'}")

        if ihi_ok:
            r = results_r1[10]
            print(f"  IHI: danger={r.get('parsed_danger')}, warning={r.get('parsed_warning')} (actual: {r.get('actual_danger')+r.get('actual_warning')})")

        # Round 2: Fanpage burst after 30s
        print(f"\n[30s] ROUND 2: 10 Fanpage")
        await asyncio.sleep(30)

        fanpage_tasks = [
            fanpage_call(client, FANPAGE_USERS[i], FANPAGE_TENANTS[i % 5],
                        random.choice(FANPAGE_PRODUCTS), await get_call_id())
            for i in range(10)
        ]
        results_r2 = await asyncio.gather(*fanpage_tasks)
        all_results.extend(results_r2)

        fp_ok = sum(1 for r in results_r2 if r.get("status") == 200)
        print(f"  Fanpage: {fp_ok}/10 OK")

        # Round 3: IHI call
        print(f"\n[60s] ROUND 3: 1 IHI")
        ihi_task = ihi_call(client, await get_call_id())
        result_ihi = await ihi_task
        all_results.append(result_ihi)

        if result_ihi.get("status") == 200:
            r = result_ihi
            print(f"  IHI: danger={r.get('parsed_danger')}, warning={r.get('parsed_warning')} (actual: {r.get('actual_danger')+r.get('actual_warning')}), latency={r.get('latency_ms',0):.0f}ms")
        else:
            print(f"  IHI: FAIL - {result_ihi.get('error', 'Unknown')}")

        # Round 4: Fanpage burst
        print(f"\n[90s] ROUND 4: 10 Fanpage")
        fanpage_tasks = [
            fanpage_call(client, FANPAGE_USERS[i], FANPAGE_TENANTS[i % 5],
                        random.choice(FANPAGE_PRODUCTS), await get_call_id())
            for i in range(10)
        ]
        results_r4 = await asyncio.gather(*fanpage_tasks)
        all_results.extend(results_r4)

        fp_ok = sum(1 for r in results_r4 if r.get("status") == 200)
        print(f"  Fanpage: {fp_ok}/10 OK")

        # Round 5: IHI call
        print(f"\n[120s] ROUND 5: 1 IHI")
        ihi_task = ihi_call(client, await get_call_id())
        result_ihi = await ihi_task
        all_results.append(result_ihi)

        if result_ihi.get("status") == 200:
            r = result_ihi
            print(f"  IHI: danger={r.get('parsed_danger')}, warning={r.get('parsed_warning')} (actual: {r.get('actual_danger')+r.get('actual_warning')}), latency={r.get('latency_ms',0):.0f}ms")
        else:
            print(f"  IHI: FAIL")

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)

    total_duration = time.time() - start_time
    fp_results = [r for r in all_results if r.get("type") == "fanpage"]
    ihi_results = [r for r in all_results if r.get("type") == "ihi"]

    fp_ok = sum(1 for r in fp_results if r.get("status") == 200)
    ihi_ok = sum(1 for r in ihi_results if r.get("status") == 200)

    print(f"Duration: {total_duration:.1f}s")
    print(f"Total requests: {len(all_results)} ({len(fp_results)} fanpage + {len(ihi_results)} ihi)")
    print()
    print(f"FANPAGE: {fp_ok}/{len(fp_results)} OK")
    if fp_ok > 0:
        avg_lat = sum(r.get("latency_ms", 0) for r in fp_results if r.get("status") == 200) / fp_ok
        print(f"  Avg latency: {avg_lat:.0f}ms")

    print()
    print(f"IHI: {ihi_ok}/{len(ihi_results)} OK")
    if ihi_ok > 0:
        ihi_ok_results = [r for r in ihi_results if r.get("status") == 200]
        avg_lat = sum(r.get("latency_ms", 0) for r in ihi_ok_results) / len(ihi_ok_results)
        print(f"  Avg latency: {avg_lat:.0f}ms")

        # Accuracy check
        print(f"  Detection accuracy:")
        for r in ihi_ok_results:
            detected = r.get('parsed_danger', 0) + r.get('parsed_warning', 0)
            actual = r.get('actual_danger', 0) + r.get('actual_warning', 0)
            diff = abs(detected - actual) if actual > 0 or detected > 0 else 0
            acc = "✓" if diff <= 5 else "✗"
            print(f"    Call {r.get('call_id')}: detected={detected}, actual={actual}, diff={diff} {acc}")

    print()
    if fp_ok == len(fp_results) and ihi_ok == len(ihi_results):
        print("✅ ALL TESTS PASSED")
    else:
        failed = [r for r in all_results if r.get("status") != 200]
        for f in failed[:5]:
            print(f"❌ {f.get('type')} Call {f.get('call_id')}: {f.get('error', 'Unknown')}")

if __name__ == "__main__":
    asyncio.run(run_concurrent_test())