#!/usr/bin/env python3
"""Simulate N concurrent users with varying question sizes to test adaptive load management."""

import asyncio
import httpx
import time
import json
from typing import Optional

API_KEY = "1XteCCQ_s_UbrqOGEIYybmDBnokWhxYap90D6_Jojx8"
BASE_URL = "http://127.0.0.1:8000"

# Question sizes: short (1-2 sentences), medium (paragraph), long (multi-paragraph)
SHORT_QUESTION = "Chào bạn, hôm nay thời tiết thế nào?"
MEDIUM_QUESTION = "Cho tôi hỏi về cách đầu tư chứng khoán cho người mới bắt đầu. Tôi nên tìm hiểu những gì trước, có bao nhiêu tiền nên bắt đầu, và làm sao để chọn được cổ phiếu tốt?"
LONG_QUESTION = """Tôi đang muốn xây dựng một hệ thống quản lý tri thức (knowledge management system) cho công ty.
Hiện tại công ty có khoảng 50 nhân viên, hoạt động trong lĩnh vực marketing và bán hàng.
Mỗi ngày có rất nhiều kiến thức, quy trình, và kinh nghiệm được chia sẻ qua email, Slack, và các cuộc họp nhưng không được lưu trữ tập trung.
Tôi muốn xây dựng một hệ thống để:
1. Thu thập và tổ chức kiến thức từ nhiều nguồn khác nhau (email, Slack, tài liệu, wiki)
2. Tìm kiếm nhanh khi cần
3. Chia sẻ tri thức giữa các phòng ban
4. Đo lường mức độ sử dụng tri thức
5. Bảo mật thông tin theo phân quyền

Bạn có thể đề xuất kiến trúc hệ thống, các công nghệ nên dùng, và lộ trình triển khai không?"""

# Priority levels
PRIORITY_HIGH = 10
PRIORITY_MEDIUM = 5
PRIORITY_LOW = 0


async def send_chat(client: httpx.AsyncClient, question: str, priority: int, user_id: int) -> dict:
    """Send a single chat request."""
    start = time.perf_counter()
    try:
        resp = await client.post(
            f"{BASE_URL}/v1/chat",
            headers={
                "Content-Type": "application/json",
                "X-API-KEY": API_KEY,
            },
            json={
                "project_id": "test",
                "tenant_id": "default",
                "user_name": f"user_{user_id}",
                "user_message": question,
                "model_mode": "lite",
                "priority": priority,
            },
            timeout=30.0,
        )
        elapsed = round((time.perf_counter() - start) * 1000, 1)
        if resp.status_code == 200:
            data = resp.json()
            return {
                "user_id": user_id,
                "status": "ok",
                "priority": priority,
                "question_len": len(question),
                "latency_ms": elapsed,
                "queue_wait_ms": data.get("queue_wait_ms"),
                "provider": data.get("provider"),
                "route": data.get("route"),
                "route_reason": data.get("route_reason"),
                "response_len": len(data.get("content", "")),
                "fallback": data.get("fallback_used"),
            }
        else:
            return {
                "user_id": user_id,
                "status": "error",
                "priority": priority,
                "question_len": len(question),
                "latency_ms": elapsed,
                "code": resp.status_code,
                "detail": resp.text[:200],
            }
    except Exception as e:
        elapsed = round((time.perf_counter() - start) * 1000, 1)
        return {
            "user_id": user_id,
            "status": "exception",
            "priority": priority,
            "question_len": len(question),
            "latency_ms": elapsed,
            "error": str(e)[:200],
        }


async def check_queue(client: httpx.AsyncClient) -> dict:
    """Check current queue status."""
    try:
        resp = await client.get(
            f"{BASE_URL}/v1/admin/queue",
            headers={"X-API-KEY": API_KEY},
            timeout=5.0,
        )
        if resp.status_code == 200:
            return resp.json()
    except:
        pass
    return {}


async def run_test(num_users: int, burst_seconds: float = 5.0):
    """Run concurrent load test."""
    print(f"\n{'='*60}")
    print(f"  LOAD TEST: {num_users} concurrent users, burst over {burst_seconds}s")
    print(f"{'='*60}\n")

    # Build user list with mixed priorities and question sizes
    users = []
    for i in range(num_users):
        # Distribute: 30% short, 40% medium, 30% long
        # Distribute priorities: 20% high, 50% medium, 30% low
        if i % 10 < 3:  # 30% short
            question = SHORT_QUESTION
        elif i % 10 < 7:  # 40% medium
            question = MEDIUM_QUESTION
        else:  # 30% long
            question = LONG_QUESTION

        if i % 10 < 2:  # 20% high priority
            priority = PRIORITY_HIGH
        elif i % 10 < 7:  # 50% medium
            priority = PRIORITY_MEDIUM
        else:  # 30% low
            priority = PRIORITY_LOW

        users.append((f"user_{i}", question, priority, i))

    # Check queue before
    async with httpx.AsyncClient() as client:
        before = await check_queue(client)
        print(f"Queue before: {before}")

    # Fire all requests as close together as possible
    print(f"\nFiring {num_users} requests...")
    start = time.perf_counter()
    async with httpx.AsyncClient(timeout=60.0) as client:
        tasks = [
            send_chat(client, q, p, uid)
            for (_, q, p, uid) in users
        ]
        results = await asyncio.gather(*tasks)

    total_time = round((time.perf_counter() - start) * 1000, 1)

    # Check queue after
    async with httpx.AsyncClient() as client:
        after = await check_queue(client)
        print(f"Queue after: {after}")

    # Analyze results
    print(f"\n{'='*60}")
    print(f"  RESULTS ({num_users} requests in {total_time}ms)")
    print(f"{'='*60}")

    ok = [r for r in results if r["status"] == "ok"]
    errors = [r for r in results if r["status"] != "ok"]

    print(f"\n--- Status ---")
    print(f"  OK: {len(ok)} ({len(ok)*100/num_users:.1f}%)")
    print(f"  Error/Exception: {len(errors)} ({len(errors)*100/num_users:.1f}%)")

    if ok:
        latencies = [r["latency_ms"] for r in ok]
        p50 = sorted(latencies)[len(latencies)//2]
        p95 = sorted(latencies)[int(len(latencies)*0.95)]
        p99 = sorted(latencies)[int(len(latencies)*0.99)] if len(latencies) >= 100 else max(latencies)
        print(f"\n--- Latency (ms) ---")
        print(f"  p50: {p50}")
        print(f"  p95: {p95}")
        print(f"  p99: {p99}")
        print(f"  min: {min(latencies)}")
        print(f"  max: {max(latencies)}")

        # Response length analysis
        resp_lens = [r["response_len"] for r in ok]
        avg_len = sum(resp_lens) / len(resp_lens)
        print(f"\n--- Response Length (chars) ---")
        print(f"  avg: {avg_len:.0f}")
        print(f"  min: {min(resp_lens)}")
        print(f"  max: {max(resp_lens)}")

        # Queue wait analysis
        queue_waits = [r["queue_wait_ms"] for r in ok if r.get("queue_wait_ms")]
        if queue_waits:
            print(f"\n--- Queue Wait (ms) ---")
            print(f"  avg: {sum(queue_waits)/len(queue_waits):.1f}")
            print(f"  max: {max(queue_waits)}")

        # Route breakdown
        print(f"\n--- Route ---")
        routes = {}
        for r in ok:
            key = f"{r['route']} ({r.get('route_reason', 'n/a')})"
            routes[key] = routes.get(key, 0) + 1
        for k, v in sorted(routes.items()):
            print(f"  {k}: {v}")

        # Fallback analysis
        fallback = [r for r in ok if r.get("fallback")]
        print(f"\n--- Fallback ---")
        print(f"  Used cloud fallback: {len(fallback)} ({len(fallback)*100/len(ok):.1f}%)")

        # Priority vs latency
        print(f"\n--- Priority vs Latency ---")
        for prio in [PRIORITY_HIGH, PRIORITY_MEDIUM, PRIORITY_LOW]:
            subset = [r for r in ok if r["priority"] == prio]
            if subset:
                avg_lat = sum(r["latency_ms"] for r in subset) / len(subset)
                print(f"  priority={prio} ({len(subset)} users): avg latency={avg_lat:.0f}ms")

        # Question length vs response length
        print(f"\n--- Question Size vs Response ---")
        for label, qlen in [("short", (0, 100)), ("medium", (100, 300)), ("long", (300, 9999))]:
            subset = [r for r in ok if qlen[0] < r["question_len"] <= qlen[1]]
            if subset:
                avg_q = sum(r["question_len"] for r in subset) / len(subset)
                avg_r = sum(r["response_len"] for r in subset) / len(subset)
                print(f"  {label} (avg q len={avg_q:.0f}): avg response len={avg_r:.0f}")

    if errors:
        print(f"\n--- Errors ---")
        for r in errors[:5]:
            print(f"  user={r['user_id']}: {r.get('detail', r.get('error', 'unknown'))[:100]}")

    print(f"\n{'='*60}\n")
    return results


async def main():
    # Quick smoke test
    print("Smoke test (1 user)...")
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{BASE_URL}/v1/chat",
            headers={"Content-Type": "application/json", "X-API-KEY": API_KEY},
            json={"project_id": "test", "user_message": "hello", "model_mode": "lite"},
            timeout=30.0,
        )
        print(f"  Smoke test: {resp.status_code} - {resp.json().get('content', '')[:50]}")

    # Test scenarios
    scenarios = [
        (5, 3.0),    # 5 users gentle
        (10, 3.0),   # 10 users moderate
        (16, 3.0),   # 16 users = queue capacity (no backlog)
        (20, 3.0),   # 20 users > capacity (backlog starts)
        (32, 5.0),   # 32 users (heavy load)
    ]

    for num_users, burst in scenarios:
        await run_test(num_users, burst)
        await asyncio.sleep(2)  # Cool down between tests


if __name__ == "__main__":
    asyncio.run(main())