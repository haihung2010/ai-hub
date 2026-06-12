#!/usr/bin/env python3
"""Multi-user load test for 5060 Ti 16GB tuned config (2026-06-12).

Spins up N concurrent clients that each send a steady stream of
chat requests, then reports:
  - Throughput (req/s)
  - p50 / p95 / p99 latency
  - Status code distribution (200 / 413 / 429 / 5xx)
  - Wall-clock duration

The test runs against a LIVE AI Hub instance (the user's own
server). llama.cpp is what actually limits throughput — this
script measures the END-TO-END path including middleware
(rate limits, ctx overflow guard).

Why this matters: the 16GB tuned config has parallel=4, ctx=6K.
That's 4 concurrent inference slots. Beyond 4 users, requests
queue up at llama.cpp. Beyond 60 RPM (per-key limit) or 200 RPM
(per-tenant limit), the middleware returns 429. The load test
exercises all three backpressure layers.

Usage:
  ./scripts/load_test_5060ti_16gb.py \\
    --base http://localhost:8000 \\
    --api-key test-api-key-aaaaaaaaaa \\
    --users 8 --duration 30

  --users N        concurrent virtual users (default 4, the
                   tuned config's parallel slot count)
  --duration S     test duration in seconds (default 30)
  --rate R         per-user request rate (default 2 req/s;
                   4 users × 2 = 8 req/s, double the slot count
                   to surface queueing)
  --prompt-size S  prompt size in chars (default 200; raise to
                   exercise the ctx overflow guard at ~6000)
  --tenant ID      tenant_id to use (default 'loadtest')
  --ramp S         warmup seconds before recording (default 5)
  --json           emit machine-readable JSON at the end

For a smoke test: --users 2 --duration 5 --rate 1 (~10 req total)
For a real benchmark: --users 8 --duration 60 --rate 2
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from collections import Counter
from typing import Any

import httpx


# Sample prompts in Vietnamese + English to exercise both tokenizers
SAMPLE_PROMPTS = [
    "Xin chào, bạn có thể giúp tôi đặt hàng không?",
    "Cho tôi biết giá sản phẩm A và B",
    "Tôi muốn hỏi về chính sách đổi trả",
    "What's the weather like in Hanoi today?",
    "Can you summarize the latest news on AI?",
    "Hãy giải thích cách sử dụng sản phẩm này",
    "I need help with my order #12345",
    "Bạn có thể gợi ý quà tặng cho bạn gái không?",
]


async def simulate_user(
    client: httpx.AsyncClient,
    base: str,
    api_key: str,
    tenant_id: str,
    prompt_size: int,
    rate: float,
    duration: float,
    ramp: float,
    results: list[dict],
) -> None:
    """One virtual user. Sends a request every (1/rate) seconds
    for `duration` seconds, records each response."""
    interval = 1.0 / rate
    deadline = time.monotonic() + duration + ramp
    next_send = time.monotonic() + ramp + (hash(id(results)) % 1.0)  # stagger start
    user_id = f"user-{id(results) % 100:02d}"
    while time.monotonic() < deadline:
        now = time.monotonic()
        if now < next_send:
            await asyncio.sleep(min(next_send - now, 0.05))
            continue
        next_send = now + interval
        # Build the request body
        prompt = SAMPLE_PROMPTS[len(results) % len(SAMPLE_PROMPTS)]
        # Pad to the requested size to exercise ctx overflow
        if len(prompt) < prompt_size:
            prompt = prompt + " " + ("x" * (prompt_size - len(prompt)))
        body = {
            "project_id": "loadtest",
            "user_message": prompt,
            "model_mode": "lite",
        }
        t0 = time.monotonic()
        try:
            r = await client.post(
                f"{base}/v1/chat",
                headers={"X-API-KEY": api_key},
                json=body,
                timeout=httpx.Timeout(60.0, connect=5.0),
            )
            dt_ms = (time.monotonic() - t0) * 1000
            results.append({
                "user": user_id,
                "status": r.status_code,
                "latency_ms": dt_ms,
                "ts": time.time(),
            })
        except Exception as exc:
            dt_ms = (time.monotonic() - t0) * 1000
            results.append({
                "user": user_id,
                "status": -1,  # exception
                "latency_ms": dt_ms,
                "error": exc.__class__.__name__,
                "ts": time.time(),
            })


async def run(
    base: str,
    api_key: str,
    users: int,
    duration: float,
    rate: float,
    prompt_size: int,
    tenant_id: str,
    ramp: float,
) -> dict[str, Any]:
    # Warmup: one cheap request to make sure server is up
    async with httpx.AsyncClient() as warmup:
        try:
            r = await warmup.get(f"{base}/health", timeout=httpx.Timeout(5.0))
            r.raise_for_status()
        except Exception as exc:
            return {"error": f"warmup failed: {exc}"}

    results: list[dict] = []
    async with httpx.AsyncClient() as client:
        tasks = [
            asyncio.create_task(
                simulate_user(
                    client, base, api_key, tenant_id, prompt_size, rate, duration, ramp, results
                )
            )
            for _ in range(users)
        ]
        t0 = time.monotonic()
        await asyncio.gather(*tasks)
        wall = time.monotonic() - t0

    # Stats over the recording window (skip ramp)
    cutoff = time.time() - duration
    recorded = [r for r in results if r["ts"] >= cutoff]
    if not recorded:
        return {"error": "no requests recorded (increase duration or check server)"}

    statuses = Counter(r["status"] for r in recorded)
    latencies = sorted(r["latency_ms"] for r in recorded if r["status"] == 200)
    all_latencies = sorted(r["latency_ms"] for r in recorded)

    def pct(arr, p):
        if not arr:
            return 0
        idx = min(int(len(arr) * p), len(arr) - 1)
        return arr[idx]

    return {
        "config": {
            "users": users,
            "duration_s": duration,
            "ramp_s": ramp,
            "per_user_rate_rps": rate,
            "prompt_size_chars": prompt_size,
            "target_throughput_rps": users * rate,
        },
        "totals": {
            "wall_s": wall,
            "requests": len(recorded),
            "throughput_rps": len(recorded) / duration,
        },
        "status_codes": dict(statuses),
        "latency_ms": {
            "all_p50": pct(all_latencies, 0.50),
            "all_p95": pct(all_latencies, 0.95),
            "all_p99": pct(all_latencies, 0.99),
            "all_max": max(all_latencies) if all_latencies else 0,
            "ok_p50": pct(latencies, 0.50),
            "ok_p95": pct(latencies, 0.95),
            "ok_p99": pct(latencies, 0.99),
        },
        "verdict": _verdict(statuses, len(recorded), pct(latencies, 0.95) if latencies else 0),
    }


def _verdict(statuses, total, p95_ms) -> str:
    if total == 0:
        return "no requests"
    five_xx = sum(v for k, v in statuses.items() if isinstance(k, int) and k >= 500)
    rate_limited = statuses.get(429, 0)
    over_ctx = statuses.get(413, 0)
    ok = statuses.get(200, 0)
    if five_xx > 0:
        return f"❌ {five_xx} 5xx errors (server crash / OOM?)"
    if over_ctx > 0:
        return f"⚠️  {over_ctx} 413s (ctx overflow — lower prompt_size or raise ctx)"
    if p95_ms > 5000:
        return f"⚠️  p95 {p95_ms:.0f}ms > 5s (slow — queue too long?)"
    if rate_limited > total * 0.5:
        return f"⚠️  {rate_limited}/{total} rate-limited (raise rate limits or lower load)"
    return f"✅ {ok}/{total} 200s, p95 {p95_ms:.0f}ms"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--base", default="http://localhost:8000")
    p.add_argument("--api-key", default="test-api-key-aaaaaaaaaa")
    p.add_argument("--users", type=int, default=4)
    p.add_argument("--duration", type=float, default=30.0)
    p.add_argument("--rate", type=float, default=2.0)
    p.add_argument("--prompt-size", type=int, default=200)
    p.add_argument("--tenant", default="loadtest")
    p.add_argument("--ramp", type=float, default=5.0)
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    report = asyncio.run(run(
        args.base, args.api_key, args.users, args.duration,
        args.rate, args.prompt_size, args.tenant, args.ramp,
    ))

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print("\n" + "=" * 60)
        print("MULTI-USER LOAD TEST — 5060 Ti 16GB tuned config")
        print("=" * 60)
        for k, v in report.get("config", {}).items():
            print(f"  {k}: {v}")
        print("---")
        for k, v in report.get("totals", {}).items():
            print(f"  {k}: {v}")
        print("---")
        print("  status_codes:", report.get("status_codes", {}))
        print("  latency_ms:", report.get("latency_ms", {}))
        print("---")
        print("  verdict:", report.get("verdict", "?"))
        print("=" * 60)
    return 0 if "error" not in report else 1


if __name__ == "__main__":
    sys.exit(main())
