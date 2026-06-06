#!/usr/bin/env python3
"""Quick stress test: 20 users × 10 messages (200 total) — local only.

NO MCP, NO MiniMax. Just plain chat. Run while iHi is active.
"""
from __future__ import annotations

import asyncio
import aiohttp
import random
import time
import statistics
from collections import defaultdict

API_URL = "http://localhost:8000/v1/chat"
API_KEY = "1XteCCQ_s_UbrqOGEIYybmDBnokWhxYap90D6_Jojx8"
CONCURRENCY = 20
MESSAGES_PER_USER = 10
TIMEOUT = 60

QUESTIONS = [
    "Xin chào, bạn kể chuyện vui đi",
    "Hôm nay bạn thế nào?",
    "Kể cho tôi nghe về Việt Nam",
    "Bạn thích ăn gì nhất?",
    "Mô tả một ngày đẹp trời",
    "Viết một bài thơ ngắn về mùa xuân",
    "Làm sao để học lập trình hiệu quả?",
    "Bạn có thể làm được gì?",
    "Tâm sự với tôi đi",
    "Thế giới quan của bạn là gì?",
]

results: list[dict] = []


async def send_message(session, user_id, msg_idx, sem):
    question = random.choice(QUESTIONS)
    payload = {
        "project_id": "test",
        "user_name": f"cafe20x10_{user_id}",
        "user_message": question,
        "max_tokens": 200,
    }
    headers = {"X-API-KEY": API_KEY, "Content-Type": "application/json"}

    t0 = time.perf_counter()
    status = "ok"
    reply_len = 0
    model = ""
    error = ""
    try:
        async with sem:
            async with session.post(
                API_URL, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=TIMEOUT)
            ) as r:
                if r.status != 200:
                    status = f"http_{r.status}"
                    error = await r.text()
                else:
                    data = await r.json()
                    reply_len = len(data.get("content", ""))
                    model = data.get("model", "")
    except asyncio.TimeoutError:
        status = "timeout"
    except Exception as e:
        status = f"err:{type(e).__name__}"

    elapsed_ms = (time.perf_counter() - t0) * 1000
    results.append({
        "user_id": user_id,
        "msg_idx": msg_idx,
        "status": status,
        "latency_ms": elapsed_ms,
        "reply_len": reply_len,
        "model": model,
    })


async def user_task(user_id):
    sem = asyncio.Semaphore(CONCURRENCY)
    async with aiohttp.ClientSession() as session:
        for i in range(MESSAGES_PER_USER):
            await send_message(session, user_id, i, sem)
            await asyncio.sleep(0.05)


async def main():
    print("=" * 70)
    print(f"AI Hub Local Stress Test (no MCP, no MiniMax) — iHi ACTIVE")
    print(f"  {CONCURRENCY} users × {MESSAGES_PER_USER} messages = {CONCURRENCY * MESSAGES_PER_USER} total")
    print(f"  Target: {API_URL}")
    print(f"  Started: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70, "\n")

    t0 = time.perf_counter()
    tasks = [asyncio.create_task(user_task(uid)) for uid in range(CONCURRENCY)]
    await asyncio.gather(*tasks)
    total_time = time.perf_counter() - t0

    by_status = defaultdict(int)
    latencies = []
    by_model = defaultdict(int)
    for r in results:
        by_status[r["status"]] += 1
        if r["status"] == "ok":
            latencies.append(r["latency_ms"])
        if r["model"]:
            by_model[r["model"]] += 1

    total = len(results)
    if latencies:
        ls = sorted(latencies)
        p50 = ls[len(ls) // 2]
        p95 = ls[int(len(ls) * 0.95)]
        p99 = ls[int(len(ls) * 0.99)]
        mean = statistics.mean(latencies)
        mn = min(latencies)
        mx = max(latencies)
    else:
        p50 = p95 = p99 = mean = mn = mx = 0

    print(f"\n{'=' * 70}")
    print(f"RESULTS  (total time {total_time:.1f}s, throughput {total / total_time:.2f} req/s)")
    print(f"{'=' * 70}")
    print(f"  Total:     {total}")
    print(f"  Success:   {by_status.get('ok', 0)} ({100 * by_status.get('ok', 0) / total:.1f}%)")
    if by_status.get('timeout', 0):
        print(f"  Timeouts:  {by_status.get('timeout', 0)}")
    if by_status.get('err:ClientError', 0):
        print(f"  ClientErr: {by_status.get('err:ClientError', 0)}")
    for s, c in sorted(by_status.items(), key=lambda x: -x[1]):
        if s not in ("ok", "timeout", "err:ClientError"):
            print(f"  {s}: {c}")
    print()
    print(f"  Latency (ms):  min={mn:.0f}  p50={p50:.0f}  p95={p95:.0f}  p99={p99:.0f}  max={mx:.0f}  mean={mean:.0f}")
    if by_model:
        print(f"  Models: {dict(by_model)}")


if __name__ == "__main__":
    asyncio.run(main())
