#!/usr/bin/env python3
"""Evaluate ai-hub memory behavior without printing secrets.

Checks:
- same-session short-term recall;
- user/session isolation;
- rolling-summary recall after the raw history window is exceeded;
- cross-session recall through user/project summary memory.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from urllib import request as urlrequest
from urllib.error import HTTPError, URLError

BASE_URL = os.getenv("AIHUB_MEMORY_EVAL_URL", "http://127.0.0.1:8012")
PROJECT_ID = os.getenv("AIHUB_MEMORY_EVAL_PROJECT", "test")
TENANT_ID = os.getenv("AIHUB_MEMORY_EVAL_TENANT", "memoryeval")
DB_PATH = Path(os.getenv("DATABASE_PATH", "/tmp/aihub_memory_eval.db"))
TIMEOUT_SECONDS = float(os.getenv("AIHUB_MEMORY_EVAL_TIMEOUT", "240"))


def load_api_key() -> str:
    env_path = Path(".env")
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("API_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("API_KEY not found in .env")


def post_chat(api_key: str, user_name: str, message: str, session_id: str | None = None) -> dict:
    payload = {
        "project_id": PROJECT_ID,
        "tenant_id": TENANT_ID,
        "user_name": user_name,
        "user_message": message,
        "model_mode": "lite",
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
        with urlrequest.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        return {
            "ok": True,
            "status": resp.status,
            "latency_s": round(time.perf_counter() - start, 3),
            **body,
        }
    except HTTPError as exc:
        return {
            "ok": False,
            "status": exc.code,
            "latency_s": round(time.perf_counter() - start, 3),
            "content": exc.read().decode("utf-8", errors="replace")[:500],
        }
    except (URLError, TimeoutError, Exception) as exc:
        return {
            "ok": False,
            "status": None,
            "latency_s": round(time.perf_counter() - start, 3),
            "content": repr(exc)[:500],
        }


def db_counts() -> dict:
    if not DB_PATH.exists():
        return {"db_path": str(DB_PATH), "exists": False}
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    out = {"db_path": str(DB_PATH), "exists": True}
    for table in [
        "users",
        "sessions",
        "messages",
        "summaries",
        "memory_episodes",
        "memory_items",
        "memory_consolidations",
    ]:
        try:
            out[table] = conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"]
        except sqlite3.Error:
            out[table] = "n/a"
    conn.close()
    return out


def latest_summary(user_name: str) -> str | None:
    if not DB_PATH.exists():
        return None
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        """
        SELECT s.content
        FROM summaries s
        JOIN users u ON u.id = s.user_id
        WHERE u.name = ? AND s.tenant_id = ? AND s.project_id = ?
        ORDER BY s.version DESC
        LIMIT 1
        """,
        (user_name, TENANT_ID, PROJECT_ID),
    ).fetchone()
    conn.close()
    return row["content"] if row else None


def wait_for_summary(user_name: str, timeout_s: float = 45.0) -> str | None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        summary = latest_summary(user_name)
        if summary:
            return summary
        time.sleep(1.0)
    return latest_summary(user_name)


def contains(text: str, token: str) -> bool:
    return token.lower() in (text or "").lower()


def print_step(name: str, result: dict, expected_token: str | None = None) -> dict:
    content = result.get("content", "")
    row = {
        "name": name,
        "ok": result.get("ok"),
        "status": result.get("status"),
        "latency_s": result.get("latency_s"),
        "session_id": result.get("session_id"),
        "contains_expected": contains(content, expected_token) if expected_token else None,
        "preview": content.replace("\n", " ")[:220],
    }
    print(json.dumps(row, ensure_ascii=False))
    return row


def main() -> None:
    api_key = load_api_key()
    print("config=" + json.dumps({
        "base_url": BASE_URL,
        "project": PROJECT_ID,
        "tenant": TENANT_ID,
        "db_path": str(DB_PATH),
    }, ensure_ascii=False))
    print("db_before=" + json.dumps(db_counts(), ensure_ascii=False))

    results = []

    # 1. Same-session short-term recall.
    token_a = "MEM-A-74291"
    r1 = post_chat(api_key, "memory_user_a", f"Ghi nhớ mã kiểm thử của tôi là {token_a}. Chỉ đáp: OK.")
    results.append(print_step("same_session_store", r1))
    r2 = post_chat(api_key, "memory_user_a", "Mã kiểm thử của tôi là gì? Chỉ trả lời mã.", r1.get("session_id"))
    results.append(print_step("same_session_recall", r2, token_a))

    # 2. User isolation: another user stores a different token; original user must not leak it.
    token_b = "MEM-B-19384"
    r3 = post_chat(api_key, "memory_user_b", f"Ghi nhớ mã kiểm thử của tôi là {token_b}. Chỉ đáp: OK.")
    results.append(print_step("isolation_user_b_store", r3))
    r4 = post_chat(api_key, "memory_user_a", "Mã kiểm thử của tôi là gì? Không dùng thông tin user khác.", r1.get("session_id"))
    leak_b = contains(r4.get("content", ""), token_b)
    row = print_step("isolation_user_a_recall", r4, token_a)
    row["contains_other_user_token"] = leak_b
    results.append(row)

    # 3. Exceed raw history window, then rely on summary memory.
    old_token = "LONG-MEM-55017"
    r_long = post_chat(
        api_key,
        "memory_user_long",
        f"Thông tin rất quan trọng: mã dài hạn của tôi là {old_token}. Hãy ghi nhớ. Chỉ đáp OK.",
    )
    session_long = r_long.get("session_id")
    results.append(print_step("long_store_old_fact", r_long))
    for idx in range(1, 8):
        filler = post_chat(
            api_key,
            "memory_user_long",
            f"Tin nhắn đệm số {idx}. Trả lời ngắn: đã nhận {idx}.",
            session_long,
        )
        results.append(print_step(f"long_filler_{idx}", filler))
    summary = wait_for_summary("memory_user_long")
    print("summary_memory_user_long=" + json.dumps({
        "exists": summary is not None,
        "contains_old_token": contains(summary or "", old_token),
        "preview": (summary or "").replace("\n", " ")[:500],
    }, ensure_ascii=False))
    r_long_recall = post_chat(
        api_key,
        "memory_user_long",
        "Mã dài hạn quan trọng tôi nói lúc đầu là gì? Chỉ trả lời mã.",
        session_long,
    )
    results.append(print_step("long_recall_same_session_after_window", r_long_recall, old_token))

    # 4. Cross-session recall for same user/project through summary.
    r_cross = post_chat(
        api_key,
        "memory_user_long",
        "Trong các cuộc trò chuyện trước của tôi, mã dài hạn quan trọng là gì? Chỉ trả lời mã.",
    )
    results.append(print_step("cross_session_summary_recall", r_cross, old_token))

    summary_score = {
        "same_session_recall": results[1]["contains_expected"],
        "isolation_expected": row["contains_expected"],
        "isolation_no_leak": not row["contains_other_user_token"],
        "summary_exists": summary is not None,
        "summary_contains_old_token": contains(summary or "", old_token),
        "long_recall_after_window": results[-2]["contains_expected"],
        "cross_session_recall": results[-1]["contains_expected"],
        "all_http_ok": all(item["ok"] for item in results),
    }
    print("score=" + json.dumps(summary_score, ensure_ascii=False, indent=2))
    print("db_after=" + json.dumps(db_counts(), ensure_ascii=False))


if __name__ == "__main__":
    main()
