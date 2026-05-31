#!/usr/bin/env python3
"""
Multi-Project Load Test: Fanpage + Vehix + IHI over 1 hour.
"""
import asyncio
import httpx
import json
import random
import time
from dataclasses import dataclass, field
from typing import List

API_KEY = "1XteCCQ_s_UbrqOGEIYybmDBnokWhxYap90D6_Jojx8"
BASE_URL = "http://localhost:8000"

PHASES = [
    ("Warmup", 2, 0.3),
    ("Light", 5, 0.5),
    ("Medium", 10, 0.75),
    ("Heavy", 10, 1.0),
]

@dataclass
class PhaseResult:
    name: str
    duration_s: int = 0
    fanpage_ok: int = 0
    fanpage_fail: int = 0
    vehix_ok: int = 0
    vehix_fail: int = 0
    ihi_ok: int = 0
    ihi_fail: int = 0
    latencies: list = field(default_factory=list)
    errors: list = field(default_factory=list)

FANPAGE_TENANTS = ["tenant_fashion", "tenant_electronics"]
FANPAGE_MSGS = [
    "tu van mua laptop cho sinh vien",
    "so sanh iPhone vs Samsung chi tiet",
    "hoi chinh sach doi tra 30 ngay",
    "cach dat hang online",
    "xem trang thai don hang #12345",
    "khieu nai giao hang tre 3 ngay",
    "huong dan thanh toan QR",
    "khuyen mai laptop gaming giam 30%",
    "bao hanh dien thoai 12 thang",
    "dia chi cua hang HCM",
]

VEHIX_PLATES = ["51A-12345", "51B-67890", "59H-11111", "50T-22222", "43K-33333"]
VEHIX_QUERIES = [
    lambda p: f"Tinh trang hop dong {p}-001",
    lambda p: f"Xe {p} dang o dau?",
    "Danh sach xe dang thue",
    "Hop dong sap het han trong 7 ngay",
    lambda p: f"Cap nhat trang thai xe {p} thanh MAINTENANCE",
]


def gen_ihi_data(n=35) -> str:
    """Generate IHI sensor string."""
    parts = []
    for i in range(n):
        mid = f"M{i+1:03d}"
        pattern = random.choices(["normal", "idle", "warning", "critical"], weights=[70, 15, 10, 5])[0]
        if pattern == "idle":
            t, v, p, e = 30, 0.2, 1.0, 0.05
        elif pattern == "warning":
            t, v, p, e = 75, 3.5, 38, 0.70
        elif pattern == "critical":
            t, v, p, e = 92, 6.5, 52, 0.40
        else:
            t, v, p, e = 45, 1.2, 15, 0.85
        parts.append(f"{mid}:T{t}V{v}P{p}E{e}")
    return ",".join(parts)


async def call_fanpage(client: httpx.AsyncClient) -> dict:
    try:
        resp = await client.post(
            f"{BASE_URL}/v1/chat",
            headers={"X-API-KEY": API_KEY},
            json={
                "project_id": "fanpage",
                "tenant_id": random.choice(FANPAGE_TENANTS),
                "user_name": f"user_{random.randint(1, 100):02d}",
                "user_message": random.choice(FANPAGE_MSGS),
                "model_mode": "lite",
                "stream": False,
            },
            timeout=30.0,
        )
        result = resp.json()
        return {"proj": "fanpage", "ok": resp.status_code == 200, "latency": result.get("latency_ms", 0)}
    except Exception as e:
        return {"proj": "fanpage", "ok": False, "err": str(e)[:50]}


async def call_vehix(client: httpx.AsyncClient) -> dict:
    plate = random.choice(VEHIX_PLATES)
    queries = [q(plate) if callable(q) else q for q in VEHIX_QUERIES]
    try:
        resp = await client.post(
            f"{BASE_URL}/v1/chat",
            headers={"X-API-KEY": API_KEY},
            json={
                "project_id": "vehix",
                "tenant_id": "vehix-fleet",
                "user_name": f"op_{random.randint(1, 20)}",
                "user_message": random.choice(queries),
                "model_mode": "normal",
                "stream": False,
            },
            timeout=30.0,
        )
        result = resp.json()
        return {"proj": "vehix", "ok": resp.status_code == 200, "latency": result.get("latency_ms", 0)}
    except Exception as e:
        return {"proj": "vehix", "ok": False, "err": str(e)[:50]}


ihi_counter = [0]

async def call_ihi(client: httpx.AsyncClient) -> dict:
    ihi_counter[0] += 1
    # Call IHI every 60 requests
    if ihi_counter[0] % 60 != 0:
        return None

    data_str = gen_ihi_data(35)
    prompt = f"Phan tich 35 sensor. Data: {data_str} JSON:"
    try:
        resp = await client.post(
            f"{BASE_URL}/v1/chat",
            headers={"X-API-KEY": API_KEY},
            json={
                "project_id": "ihi",
                "tenant_id": "ihi-plant",
                "user_name": "sensor_monitor",
                "user_message": prompt,
                "model_mode": "normal",
                "stream": False,
            },
            timeout=90.0,
        )
        result = resp.json()
        content = result.get("content", "")
        try:
            json.loads(content)
            parsed = True
        except:
            parsed = False
        return {"proj": "ihi", "ok": resp.status_code == 200 and parsed, "latency": result.get("latency_ms", 0)}
    except Exception as e:
        return {"proj": "ihi", "ok": False, "err": str(e)[:50]}


async def run_phase(name: str, duration_s: int, intensity: float) -> PhaseResult:
    result = PhaseResult(name=name, duration_s=duration_s)
    start = time.time()
    end_time = start + duration_s
    req_count = 0

    async with httpx.AsyncClient() as client:
        while time.time() < end_time:
            tasks = []
            for _ in range(int(10 * intensity)):
                tasks.append(call_fanpage(client))
            for _ in range(int(5 * intensity)):
                tasks.append(call_vehix(client))
            tasks.append(call_ihi(client))

            results = await asyncio.gather(*tasks)
            for r in results:
                if r is None:
                    continue
                lat = r.get("latency", 0)
                if lat:
                    result.latencies.append(lat)
                if r.get("ok"):
                    if r["proj"] == "fanpage":
                        result.fanpage_ok += 1
                    elif r["proj"] == "vehix":
                        result.vehix_ok += 1
                    elif r["proj"] == "ihi":
                        result.ihi_ok += 1
                else:
                    if r["proj"] == "fanpage":
                        result.fanpage_fail += 1
                    elif r["proj"] == "vehix":
                        result.vehix_fail += 1
                    elif r["proj"] == "ihi":
                        result.ihi_fail += 1
                    if "err" in r:
                        result.errors.append(r["err"])
                req_count += 1

            if req_count % 20 == 0:
                elapsed = time.time() - start
                print(f"  [{name}] {elapsed:.0f}s | fp={result.fanpage_ok+result.fanpage_fail} vx={result.vehix_ok+result.vehix_fail} ihi={result.ihi_ok+result.ihi_fail}")
            await asyncio.sleep(1.0)

    return result


async def main():
    print("=" * 60)
    print("MULTI-PROJECT LOAD TEST")
    print("Fanpage(2x5) | Vehix(5) | IHI(35/min)")
    print("=" * 60)

    all_results = []
    for phase_name, duration_min, intensity in PHASES:
        print(f"\n>>> Phase: {phase_name} ({duration_min}min) intensity={intensity}")
        r = await run_phase(phase_name, duration_min * 60, intensity)
        all_results.append(r)
        total = r.fanpage_ok + r.fanpage_fail + r.vehix_ok + r.vehix_fail + r.ihi_ok + r.ihi_fail
        avg_lat = sum(r.latencies) / len(r.latencies) if r.latencies else 0
        print(f"  Fanpage OK={r.fanpage_ok} Fail={r.fanpage_fail}")
        print(f"  Vehix OK={r.vehix_ok} Fail={r.vehix_fail}")
        print(f"  IHI OK={r.ihi_ok} Fail={r.ihi_fail}")
        print(f"  Avg latency: {avg_lat:.0f}ms | Errors: {len(r.errors)}")

    # Summary
    total_ok = sum(r.fanpage_ok + r.vehix_ok + r.ihi_ok for r in all_results)
    total_fail = sum(r.fanpage_fail + r.vehix_fail + r.ihi_fail for r in all_results)
    print(f"\n{'=' * 60}")
    print(f"TOTAL: {total_ok} OK | {total_fail} FAIL")
    print("Done!")


if __name__ == "__main__":
    asyncio.run(main())
