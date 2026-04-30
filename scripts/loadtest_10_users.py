#!/usr/bin/env python3
"""Concurrent 10-user smoke/load test for local ai-hub.

Uses the API key from .env without printing it. Designed to verify:
- concurrent request handling;
- per-user/session context isolation;
- whether GPU_CONCURRENCY serializes or overlaps LLM calls.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import mean
from urllib import request as urlrequest
from urllib.error import HTTPError, URLError

BASE_URL = os.getenv("AIHUB_LOADTEST_URL", "http://127.0.0.1:8010")
USER_COUNT = int(os.getenv("AIHUB_LOADTEST_USERS", "10"))
TIMEOUT_SECONDS = float(os.getenv("AIHUB_LOADTEST_TIMEOUT", "240"))
PROJECT_ID = os.getenv("AIHUB_LOADTEST_PROJECT", "test")
TENANT_ID = os.getenv("AIHUB_LOADTEST_TENANT", "loadtest")
DB_PATH = Path(os.getenv("DATABASE_PATH", "/tmp/aihub_loadtest.db"))


@dataclass
class CallResult:
    user: str
    round_name: str
    status: int | None
    ok: bool
    latency_s: float
    session_id: str | None = None
    content: str = ""
    error: str = ""


def load_api_key() -> str:
    env_path = Path(".env")
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("API_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("API_KEY not found in .env")


def post_chat(api_key: str, payload: dict) -> tuple[int, dict]:
    data = json.dumps(payload).encode("utf-8")
    req = urlrequest.Request(
        f"{BASE_URL}/v1/chat",
        data=data,
        headers={"Content-Type": "application/json", "X-API-KEY": api_key},
        method="POST",
    )
    with urlrequest.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))


async def call_chat(api_key: str, user_idx: int, round_name: str, message: str, session_id: str | None = None) -> CallResult:
    user = f"load_user_{user_idx:02d}"
    payload = {
        "project_id": PROJECT_ID,
        "tenant_id": TENANT_ID,
        "user_name": user,
        "user_message": message,
        "model_mode": "lite",
        "enable_search": False,
    }
    if session_id:
        payload["session_id"] = session_id
    start = time.perf_counter()
    try:
        status, body = await asyncio.to_thread(post_chat, api_key, payload)
        latency = time.perf_counter() - start
        return CallResult(
            user=user,
            round_name=round_name,
            status=status,
            ok=200 <= status < 300,
            latency_s=round(latency, 3),
            session_id=body.get("session_id"),
            content=body.get("content", ""),
        )
    except HTTPError as exc:
        latency = time.perf_counter() - start
        body = exc.read().decode("utf-8", errors="replace")
        return CallResult(user, round_name, exc.code, False, round(latency, 3), session_id=session_id, error=body[:500])
    except (URLError, TimeoutError, Exception) as exc:
        latency = time.perf_counter() - start
        return CallResult(user, round_name, None, False, round(latency, 3), session_id=session_id, error=repr(exc)[:500])


async def run_round(api_key: str, round_name: str, messages: list[str], session_ids: list[str | None] | None = None) -> list[CallResult]:
    session_ids = session_ids or [None] * len(messages)
    tasks = [
        call_chat(api_key, idx + 1, round_name, msg, session_ids[idx])
        for idx, msg in enumerate(messages)
    ]
    return await asyncio.gather(*tasks)


def db_counts() -> dict[str, int | str]:
    if not DB_PATH.exists():
        return {"db_path": str(DB_PATH), "exists": 0}
    conn = sqlite3.connect(DB_PATH)
    counts: dict[str, int | str] = {"db_path": str(DB_PATH), "exists": 1}
    for table in ["users", "sessions", "messages", "summaries"]:
        try:
            counts[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        except sqlite3.Error:
            counts[table] = -1
    conn.close()
    return counts


def summarize(results: list[CallResult]) -> dict:
    latencies = [r.latency_s for r in results]
    return {
        "count": len(results),
        "ok": sum(1 for r in results if r.ok),
        "errors": sum(1 for r in results if not r.ok),
        "min_latency_s": min(latencies) if latencies else None,
        "avg_latency_s": round(mean(latencies), 3) if latencies else None,
        "max_latency_s": max(latencies) if latencies else None,
        "statuses": sorted({r.status for r in results}, key=lambda x: -1 if x is None else x),
    }


def print_round(title: str, results: list[CallResult]) -> None:
    print(f"\n== {title} ==")
    print(json.dumps(summarize(results), ensure_ascii=False, indent=2))
    for r in results:
        preview = (r.content or r.error).replace("\n", " ")[:140]
        print(f"{r.user} {r.round_name} status={r.status} ok={r.ok} latency={r.latency_s}s session={r.session_id} preview={preview}")


async def main() -> None:
    api_key = load_api_key()
    print("base_url=", BASE_URL)
    print("project=", PROJECT_ID, "tenant=", TENANT_ID, "users=", USER_COUNT)
    print("db_before=", json.dumps(db_counts(), ensure_ascii=False))

    codes = [f"CTX-CODE-{idx:02d}" for idx in range(1, USER_COUNT + 1)]
    round1_messages = [
        (
            f"Bạn là bài test context. Hãy ghi nhớ mã riêng của tôi là {code}. "
            "Chỉ trả lời đúng một câu: DA_NHO <mã>."
        )
        for code in codes
    ]
    start1 = time.perf_counter()
    round1 = await run_round(api_key, "remember", round1_messages)
    wall1 = time.perf_counter() - start1
    print_round(f"round1 remember wall={wall1:.3f}s", round1)

    session_ids = [r.session_id for r in round1]
    round2_messages = [
        "Không dùng thông tin của user khác. Hãy trả lời đúng mã riêng tôi đã nói ở lượt trước, chỉ trả lời mã."
        for _ in range(USER_COUNT)
    ]
    start2 = time.perf_counter()
    round2 = await run_round(api_key, "recall", round2_messages, session_ids)
    wall2 = time.perf_counter() - start2
    print_round(f"round2 recall wall={wall2:.3f}s", round2)

    isolation = []
    for idx, result in enumerate(round2):
        expected = codes[idx]
        content = result.content or ""
        wrong_codes = [code for code in codes if code != expected and code in content]
        isolation.append(
            {
                "user": result.user,
                "expected": expected,
                "contains_expected": expected in content,
                "wrong_code_leak": wrong_codes,
                "ok": result.ok,
            }
        )

    print("\n== context_isolation ==")
    print(json.dumps(isolation, ensure_ascii=False, indent=2))
    print("db_after=", json.dumps(db_counts(), ensure_ascii=False))
    print("overall=", json.dumps({
        "round1_wall_s": round(wall1, 3),
        "round2_wall_s": round(wall2, 3),
        "round1": summarize(round1),
        "round2": summarize(round2),
        "expected_context_hits": sum(1 for item in isolation if item["contains_expected"]),
        "wrong_code_leaks": sum(1 for item in isolation if item["wrong_code_leak"]),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
