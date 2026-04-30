#!/usr/bin/env python3
"""20 concurrent users through AI Hub OpenRouter integration.

Reads API_KEY from .env without printing it.
"""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import time
from pathlib import Path
from urllib import request as urlrequest
from urllib.error import HTTPError, URLError

BASE_URL = "http://127.0.0.1:8018"
TENANT_ID = "openrouter_20user"
PROJECT_ID = "test"
USERS = 20
TIMEOUT = 180


def load_api_key() -> str:
    for line in Path(".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("API_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("API_KEY missing")


def post_chat(api_key: str, user_idx: int, session_id: str | None, message: str) -> dict:
    payload = {
        "tenant_id": TENANT_ID,
        "project_id": PROJECT_ID,
        "user_name": f"openrouter_user_{user_idx:02d}",
        "user_message": message,
        "model_mode": "external",
        "provider": "cloud",
        "allow_external": True,
        "enable_search": False,
    }
    if session_id:
        payload["session_id"] = session_id
    req = urlrequest.Request(
        f"{BASE_URL}/v1/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-API-KEY": api_key},
        method="POST",
    )
    start = time.perf_counter()
    try:
        with urlrequest.urlopen(req, timeout=TIMEOUT) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            content = body.get("content", "")
            return {
                "user": user_idx,
                "status": resp.status,
                "ok": 200 <= resp.status < 300,
                "latency_ms": round((time.perf_counter() - start) * 1000, 1),
                "session_id": body.get("session_id"),
                "model": body.get("model"),
                "provider": body.get("provider"),
                "content_preview": content[:220],
                "content": content,
            }
    except HTTPError as exc:
        return {"user": user_idx, "status": exc.code, "ok": False, "latency_ms": round((time.perf_counter() - start) * 1000, 1), "error": exc.read().decode(errors="replace")[:500]}
    except (URLError, TimeoutError, Exception) as exc:
        return {"user": user_idx, "status": None, "ok": False, "latency_ms": round((time.perf_counter() - start) * 1000, 1), "error": repr(exc)[:500]}


async def run_round(api_key: str, round_no: int, sessions: dict[int, str]) -> list[dict]:
    async def one(user_idx: int) -> dict:
        code = f"OR20-U{user_idx:02d}-SENTINEL"
        if round_no == 1:
            message = (
                f"Bạn là OpenRouter integration test. Mã riêng của user này là {code}. "
                "Trả lời tiếng Việt trong đúng 2 câu, có nhắc lại mã riêng."
            )
        else:
            message = (
                "Hãy nhắc lại đúng mã riêng user này đã đưa ở lượt trước. "
                "Chỉ trả lời mã, không giải thích, không dùng mã user khác."
            )
        result = await asyncio.to_thread(post_chat, api_key, user_idx, sessions.get(user_idx), message)
        if result.get("session_id"):
            sessions[user_idx] = result["session_id"]
        text = result.get("content", "")
        expected = code
        wrong_codes = [f"OR20-U{i:02d}-SENTINEL" for i in range(1, USERS + 1) if i != user_idx and f"OR20-U{i:02d}-SENTINEL" in text]
        public = {k: v for k, v in result.items() if k != "content"}
        public.update({"round": round_no, "expected_code_present": expected in text, "wrong_codes_present": wrong_codes})
        return public

    return await asyncio.gather(*[one(i) for i in range(1, USERS + 1)])


def percentile(vals: list[float], p: int) -> float:
    if not vals:
        return 0.0
    vals = sorted(vals)
    idx = max(0, min(len(vals)-1, round((p/100) * (len(vals)-1))))
    return round(vals[idx], 1)


def db_counts(path: str) -> dict:
    conn = sqlite3.connect(path)
    try:
        out = {}
        for table in ["users", "sessions", "messages", "summaries", "pinned_memories"]:
            try:
                out[table] = conn.execute(f"select count(*) from {table}").fetchone()[0]
            except Exception as exc:
                out[table] = f"error:{exc}"
        return out
    finally:
        conn.close()


async def main() -> None:
    api_key = load_api_key()
    sessions: dict[int, str] = {}
    start = time.perf_counter()
    round1 = await run_round(api_key, 1, sessions)
    round2 = await run_round(api_key, 2, sessions)
    wall = round(time.perf_counter() - start, 3)
    events = round1 + round2
    lats = [e["latency_ms"] for e in events if e.get("ok")]
    recall = [e for e in events if e["round"] == 2]
    stats = {
        "users": USERS,
        "rounds": 2,
        "total_requests": len(events),
        "ok_requests": sum(1 for e in events if e.get("ok")),
        "failed_requests": sum(1 for e in events if not e.get("ok")),
        "wall_seconds": wall,
        "latency_min_ms": min(lats) if lats else 0,
        "latency_avg_ms": round(sum(lats) / len(lats), 1) if lats else 0,
        "latency_p50_ms": percentile(lats, 50),
        "latency_p90_ms": percentile(lats, 90),
        "latency_p99_ms": percentile(lats, 99),
        "latency_max_ms": max(lats) if lats else 0,
        "recall_ok": sum(1 for e in recall if e.get("expected_code_present")),
        "leak_count": sum(len(e.get("wrong_codes_present", [])) for e in events),
        "providers": sorted(set(e.get("provider", "") for e in events if e.get("provider"))),
        "models": sorted(set(e.get("model", "") for e in events if e.get("model"))),
        "db_counts": db_counts(os.environ.get("DATABASE_PATH", "/tmp/aihub_openrouter_20user_1257151.db")),
    }
    payload = {"stats": stats, "events": events}
    out = Path("reports/openrouter_20user_concurrent_20260427.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    print("REPORT", out)


if __name__ == "__main__":
    asyncio.run(main())
