"""Multi-user short-query load test — 50 users, 5 turns each, measure throughput & errors."""

import asyncio
import time
from collections import Counter
import httpx

BASE_URL = "http://localhost:8000"
import os
API_KEY = os.getenv("AIHUB_API_KEY", "1XteCCQ_s_UbrqOGEIYybmDBnokWhxYap90D6_Jojx8")
PROJECT = "test"
TENANT = "default"

SHORT_QUERIES = [
    "xin chao",
    "cam on",
    "ok",
    "the nao",
    "bao nhieu",
    "ai day",
    "tot",
    "dung roi",
    "hieu",
    "cu",
]


async def single_request(client: httpx.AsyncClient, user_id: int, turn: int) -> dict:
    query = SHORT_QUERIES[(user_id * 3 + turn) % len(SHORT_QUERIES)]
    payload = {
        "project_id": PROJECT,
        "tenant_id": TENANT,
        "user_name": f"loaduser_{user_id}",
        "user_message": query,
        "model_mode": "lite",
    }
    headers = {"X-API-KEY": API_KEY, "Content-Type": "application/json"}
    t0 = time.perf_counter()
    try:
        r = await client.post(f"{BASE_URL}/v1/chat", json=payload, headers=headers, timeout=30.0)
        latency = (time.perf_counter() - t0) * 1000
        return {"status": r.status_code, "latency_ms": latency, "user": user_id, "turn": turn}
    except Exception as e:
        latency = (time.perf_counter() - t0) * 1000
        return {"status": 0, "latency_ms": latency, "user": user_id, "turn": turn, "error": str(e)}


async def run_load_test(num_users: int = 50, turns: int = 5, concurrent: bool = False) -> None:
    """concurrent=False → sequential (fair comparison to yesterday)."""
    async with httpx.AsyncClient() as client:
        total = num_users * turns
        print(f"[START] {num_users} users × {turns} turns = {total} requests  (concurrent={concurrent})")
        t0 = time.perf_counter()
        if concurrent:
            tasks = [single_request(client, u, t) for u in range(num_users) for t in range(turns)]
            results = await asyncio.gather(*tasks)
        else:
            results = []
            for u in range(num_users):
                for t in range(turns):
                    results.append(await single_request(client, u, t))
        wall_ms = (time.perf_counter() - t0) * 1000
        print(f"[DONE]  wall time: {wall_ms:.0f}ms  |  throughput: {total/wall_ms*1000:.1f} req/s")
        statuses = Counter(r["status"] for r in results)
        print(f"[STATUS] {dict(statuses)}")
        latencies = [r["latency_ms"] for r in results]
        latencies.sort()
        print(f"[LATENCY] min={min(latencies):.0f}  p50={latencies[len(latencies)//2]:.0f}  p99={latencies[int(len(latencies)*0.99)]:.0f}  max={max(latencies):.0f}")
        errors = [r for r in results if r.get("error")]
        if errors:
            print(f"[ERRORS] {len(errors)} failures: {errors[:3]}")


if __name__ == "__main__":
    import sys
    concurrent = "--concurrent" in sys.argv
    num_users = 30
    turns = 5
    for arg in sys.argv[1:]:
        if arg.isdigit():
            num_users = int(arg)
    asyncio.run(run_load_test(num_users=num_users, turns=turns, concurrent=concurrent))