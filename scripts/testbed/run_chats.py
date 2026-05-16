#!/usr/bin/env python3
"""Driver: chạy kịch bản chat đa tenant theo profile fast/full.

fast:  1 user/tenant, sequential, không gap. Smoke test.
full:  5 user/tenant, fanpage/iot dãn theo gap, vehix/sales bursty parallel.

Usage:  ./venv/bin/python scripts/testbed/run_chats.py --profile fast [--api ...]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import statistics
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
SCENARIOS = ROOT / "scripts" / "testbed" / "scenarios"
ENV_FILE = ROOT / ".env"


def _load_api_key() -> str:
    for line in ENV_FILE.read_text().splitlines():
        if line.startswith("API_KEY="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError("API_KEY not found in .env")


def _load_scenarios() -> list[dict]:
    scenarios = []
    for path in sorted(SCENARIOS.glob("*.json")):
        if path.name == "knowledge.json":
            continue
        scenarios.append(json.loads(path.read_text()))
    return scenarios


async def _run_user(
    client: httpx.AsyncClient,
    api: str,
    headers: dict[str, str],
    tenant_id: str,
    project_id: str,
    model_mode: str,
    user: dict,
    pace: str,
    min_gap: float,
    max_gap: float,
    results: list[dict],
) -> None:
    session_id = None
    for index, message in enumerate(user["messages"]):
        if index > 0 and pace != "no_gap":
            await asyncio.sleep(random.uniform(min_gap, max_gap))
        body = {
            "tenant_id": tenant_id,
            "project_id": project_id,
            "user_name": user["name"],
            "user_message": message,
            "model_mode": model_mode,
        }
        if session_id:
            body["session_id"] = session_id
        start = time.perf_counter()
        try:
            resp = await client.post(f"{api}/v1/chat", json=body, headers=headers, timeout=120.0)
            latency_ms = (time.perf_counter() - start) * 1000
            if resp.status_code == 200:
                payload = resp.json()
                session_id = payload.get("session_id") or session_id
                results.append({
                    "tenant": tenant_id,
                    "user": user["name"],
                    "turn": index + 1,
                    "ok": True,
                    "latency_ms": round(latency_ms, 1),
                    "server_latency_ms": payload.get("latency_ms"),
                    "queue_wait_ms": payload.get("queue_wait_ms"),
                    "route": payload.get("route"),
                    "fallback": payload.get("fallback_used"),
                    "model": payload.get("model"),
                    "question": message,
                    "answer": payload.get("content", ""),
                })
            else:
                results.append({
                    "tenant": tenant_id,
                    "user": user["name"],
                    "turn": index + 1,
                    "ok": False,
                    "latency_ms": round(latency_ms, 1),
                    "status": resp.status_code,
                    "error": resp.text[:200],
                })
        except Exception as exc:
            latency_ms = (time.perf_counter() - start) * 1000
            results.append({
                "tenant": tenant_id,
                "user": user["name"],
                "turn": index + 1,
                "ok": False,
                "latency_ms": round(latency_ms, 1),
                "error": str(exc)[:200],
            })


async def _run_scenario(
    client: httpx.AsyncClient,
    api: str,
    headers: dict[str, str],
    scenario: dict,
    profile: str,
    scale: int,
    results: list[dict],
) -> None:
    users = scenario["users"]
    if profile == "fast":
        users = users[:1]
        pace = "no_gap"
        min_gap = 0.0
        max_gap = 0.0
    else:
        pace = scenario.get("pace", "intermittent")
        min_gap = scenario.get("min_gap_seconds", 0)
        max_gap = scenario.get("max_gap_seconds", 0)

    if scale > 1 and profile != "fast":
        scaled: list[dict] = []
        for u in users:
            scaled.append(u)
            for k in range(2, scale + 1):
                scaled.append({**u, "name": f"{u['name']}_x{k:02d}"})
        users = scaled

    coros = [
        _run_user(
            client, api, headers,
            scenario["tenant_id"], scenario["project_id"], scenario.get("model_mode", "lite"),
            user, pace, min_gap, max_gap, results,
        )
        for user in users
    ]
    if profile == "fast":
        for coro in coros:
            await coro
    else:
        await asyncio.gather(*coros)


def _summarize(results: list[dict]) -> None:
    print(f"\n=== Summary: {len(results)} turns ===")
    by_tenant: dict[str, list[dict]] = {}
    for r in results:
        by_tenant.setdefault(r["tenant"], []).append(r)

    for tenant, items in sorted(by_tenant.items()):
        ok = [r for r in items if r["ok"]]
        fail = [r for r in items if not r["ok"]]
        latencies = [r["latency_ms"] for r in ok]
        if latencies:
            p50 = statistics.median(latencies)
            p95 = sorted(latencies)[max(0, int(len(latencies) * 0.95) - 1)]
            avg = statistics.mean(latencies)
        else:
            p50 = p95 = avg = 0.0
        print(
            f"  {tenant:8s} ok={len(ok):3d} fail={len(fail):2d} "
            f"avg={avg:7.0f}ms p50={p50:7.0f}ms p95={p95:7.0f}ms"
        )
        for f in fail[:3]:
            print(f"    [FAIL] {f.get('user')}/turn{f.get('turn')}: {f.get('error', '')[:120]}")


async def _amain(args: argparse.Namespace) -> int:
    api_key = _load_api_key()
    headers = {"X-API-KEY": api_key, "Content-Type": "application/json"}
    scenarios = _load_scenarios()
    print(f"[run] profile={args.profile} tenants={[s['tenant_id'] for s in scenarios]}")

    started = time.perf_counter()
    results: list[dict] = []
    async with httpx.AsyncClient() as client:
        await asyncio.gather(*[
            _run_scenario(client, args.api, headers, scenario, args.profile, args.scale, results)
            for scenario in scenarios
        ])
    duration = time.perf_counter() - started

    _summarize(results)
    print(f"\n[run] total={duration:.1f}s")

    if args.report:
        Path(args.report).write_text(json.dumps({
            "profile": args.profile,
            "duration_seconds": round(duration, 1),
            "results": results,
        }, ensure_ascii=False, indent=2))
        print(f"[run] report -> {args.report}")

    fail_count = sum(1 for r in results if not r["ok"])
    return 0 if fail_count == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", choices=["fast", "full"], default="fast")
    parser.add_argument("--api", default="http://localhost:8000")
    parser.add_argument("--report", default=None, help="optional path to write JSON report")
    parser.add_argument("--scale", type=int, default=1, help="multiply users per tenant (full profile only)")
    args = parser.parse_args()
    return asyncio.run(_amain(args))


if __name__ == "__main__":
    sys.exit(main())
