#!/usr/bin/env python3
"""Deep memory continuity test for vehix tenant.

Phase 1: Send 25 turns per user across multiple domains to exceed
SUMMARY_THRESHOLD=20 and trigger memory consolidation jobs.
Phase 2: Resume — send a new prompt with the same user_name and no
session_id. Verify the assistant references prior context.
Phase 3: Print DB stats so memory chain can be inspected manually.

Usage:
  ./venv/bin/python scripts/testbed/deep_memory_test.py
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
ENV = ROOT / ".env"
SCEN = ROOT / "scripts" / "testbed" / "scenarios_deep_vehix.json"
API = "http://localhost:8000"


def _api_key() -> str:
    for line in ENV.read_text().splitlines():
        if line.startswith("API_KEY="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError("API_KEY missing")


async def _send(client: httpx.AsyncClient, headers: dict, body: dict) -> dict:
    resp = await client.post(f"{API}/v1/chat", json=body, headers=headers, timeout=180.0)
    resp.raise_for_status()
    return resp.json()


async def phase1_seed_history(client: httpx.AsyncClient, headers: dict, scenario: dict) -> dict[str, str]:
    """Run all turns sequentially per user. Returns {user_name: last_session_id}."""
    sessions: dict[str, str] = {}
    print(f"\n=== Phase 1: seed history for {len(scenario['users'])} deep users ===")
    for user in scenario["users"]:
        name = user["name"]
        session_id = None
        latencies: list[float] = []
        print(f"\n[user] {name} — {len(user['messages'])} turns")
        for idx, msg in enumerate(user["messages"], 1):
            body = {
                "tenant_id": scenario["tenant_id"],
                "project_id": scenario["project_id"],
                "user_name": name,
                "user_message": msg,
                "model_mode": scenario.get("model_mode", "lite"),
            }
            if session_id:
                body["session_id"] = session_id
            t = time.perf_counter()
            try:
                payload = await _send(client, headers, body)
            except Exception as exc:
                print(f"  [FAIL turn {idx}]: {exc}")
                continue
            latencies.append((time.perf_counter() - t) * 1000)
            session_id = payload.get("session_id") or session_id
            if idx % 5 == 0 or idx == len(user["messages"]):
                avg = sum(latencies) / len(latencies)
                print(f"  turn {idx:>2d}: avg={avg:.0f}ms last={latencies[-1]:.0f}ms")
        sessions[name] = session_id or ""
    return sessions


async def phase2_resume(client: httpx.AsyncClient, headers: dict, sessions: dict[str, str]) -> None:
    """Resume each user with a new prompt that requires recalling earlier turns."""
    print("\n=== Phase 2: resume by user_name (no session_id) ===")
    probes = {
        "vehix_deep_lan": "Tóm tắt lại 3 chủ đề chính tôi đã hỏi từ đầu cuộc trò chuyện này.",
        "vehix_deep_phong": "Hãy nhắc lại hợp đồng nào tôi đã hỏi và tổng tiền của nó.",
        "vehix_deep_huong": "Tôi đã hỏi xe gì cho khách trẻ và bạn đã so sánh với xe nào?",
    }
    # Wait a few seconds so any in-flight summary task can finish
    print("  waiting 8s for memory jobs to settle...")
    await asyncio.sleep(8)

    for name, probe in probes.items():
        body = {
            "tenant_id": "vehix",
            "project_id": "vehix",
            "user_name": name,
            "user_message": probe,
            "model_mode": "lite",
        }
        t = time.perf_counter()
        try:
            payload = await _send(client, headers, body)
        except Exception as exc:
            print(f"\n[{name}] FAIL: {exc}")
            continue
        latency = (time.perf_counter() - t) * 1000
        prior_session = sessions.get(name)
        new_session = payload.get("session_id")
        resumed = "YES" if new_session == prior_session else "NO (new session)"
        print(f"\n[{name}] resumed_same_session={resumed} latency={latency:.0f}ms")
        print(f"  Q: {probe}")
        print(f"  A: {payload.get('content','')[:400]}")


def phase3_db_stats() -> None:
    import subprocess
    print("\n=== Phase 3: DB memory chain inspection ===")
    sql = """
    SELECT 'total messages: '||count(*) FROM messages WHERE user_id IN (
      SELECT id FROM users WHERE name LIKE 'vehix_deep_%'
    );
    SELECT 'unsummarized messages: '||count(*) FROM messages
      WHERE is_summarized = 0 AND user_id IN (
        SELECT id FROM users WHERE name LIKE 'vehix_deep_%'
      );
    SELECT 'summarized messages: '||count(*) FROM messages
      WHERE is_summarized = 1 AND user_id IN (
        SELECT id FROM users WHERE name LIKE 'vehix_deep_%'
      );
    SELECT 'summaries rows: '||count(*) FROM summaries
      WHERE user_id IN (SELECT id FROM users WHERE name LIKE 'vehix_deep_%');
    SELECT 'memory_items: '||count(*) FROM memory_items
      WHERE user_id IN (SELECT id FROM users WHERE name LIKE 'vehix_deep_%');
    SELECT 'pinned_memories: '||count(*) FROM pinned_memories
      WHERE user_id IN (SELECT id FROM users WHERE name LIKE 'vehix_deep_%');
    SELECT 'sessions per user' AS hdr;
    SELECT u.name, count(s.id) AS sessions
      FROM users u LEFT JOIN sessions s ON s.user_id = u.id
      WHERE u.name LIKE 'vehix_deep_%'
      GROUP BY u.name ORDER BY u.name;
    """
    cmd = ["env", "PGPASSWORD=aihub_pass", "psql", "-h", "localhost", "-U", "aihub",
           "-d", "ai_hub", "-tAc", sql]
    out = subprocess.run(cmd, capture_output=True, text=True)
    print(out.stdout)
    if out.returncode != 0:
        print(out.stderr)

    print("\n--- latest summary text per user ---")
    sql2 = """
    SELECT u.name, left(s.content, 600) FROM summaries s
      JOIN users u ON s.user_id = u.id
      WHERE u.name LIKE 'vehix_deep_%';
    """
    cmd2 = ["env", "PGPASSWORD=aihub_pass", "psql", "-h", "localhost", "-U", "aihub",
            "-d", "ai_hub", "-tAc", sql2]
    out2 = subprocess.run(cmd2, capture_output=True, text=True)
    print(out2.stdout or "(no summary rows yet)")


async def _amain() -> int:
    headers = {"X-API-KEY": _api_key(), "Content-Type": "application/json"}
    scenario = json.loads(SCEN.read_text())
    async with httpx.AsyncClient() as client:
        sessions = await phase1_seed_history(client, headers, scenario)
        await phase2_resume(client, headers, sessions)
    phase3_db_stats()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_amain()))
