"""Throughput probe — discover how many 10-user batches complete in 1 hour.

User's experiment:
- 10 users run concurrently
- Each user sends 10 requests sequentially (3-8s gap)
- When all 10 finish, the next 10-user batch starts
- Continue for 1 hour
- Count total users completed + throughput

This is a flat-rate test to discover the baseline capacity before
the full 6h realistic-day test scales up.
"""
from __future__ import annotations

import asyncio
import json
import os
import random
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR))
import realistic_day_generator as gen

ICT = timezone(timedelta(hours=7))
REPORT_DIR = Path("/home/hung/ai-hub/reports/throughput-probe")
REPORT_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = REPORT_DIR / "probe.log"
API_URL = "http://127.0.0.1:8000"


def _load_key() -> str:
    p = Path("/home/hung/ai-hub/.env")
    PREFIX = "API_KEY" + "="
    for line in p.read_text().splitlines():
        if line.startswith(PREFIX):
            k = line.split("=", 1)[1].strip().strip('"').strip("'")
            if k and len(k) > 20:
                return k
    return ""


KEY = os.environ.get("AIHUB_KEY") or _load_key()
if not KEY:
    print("FATAL: API_KEY not set"); sys.exit(1)


def now_ict() -> str:
    return datetime.now(ICT).strftime("%H:%M:%S")


def _log(msg: str) -> None:
    line = f"[{now_ict()}] {msg}"
    print(line, flush=True)
    with LOG_FILE.open("a") as f:
        f.write(line + "\n")


def _chat(user: str, message: str, session_id: str = None, project_id: str = "fanpage") -> dict:
    payload = {
        "project_id": project_id,
        "tenant_id": "throughput_probe",
        "user_name": user,
        "user_message": message,
        "model_mode": "lite",
        "enable_search": False,
    }
    if session_id:
        payload["session_id"] = session_id
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{API_URL}/v1/chat",
        data=data,
        headers={"Content-Type": "application/json", "X-API-KEY": KEY},
        method="POST",
    )
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            elapsed = int((time.monotonic() - t0) * 1000)
            body = json.loads(r.read().decode())
            return {
                "status": r.status, "latency_ms": elapsed,
                "session_id": body.get("session_id"), "content": body.get("content", "")[:100],
            }
    except urllib.error.HTTPError as e:
        return {"status": e.code, "latency_ms": int((time.monotonic()-t0)*1000), "error": e.read().decode()[:200]}
    except Exception as e:
        return {"status": 0, "latency_ms": int((time.monotonic()-t0)*1000), "error": repr(e)[:200]}


# Map topic → project_id (matches aihub config)
TOPIC_TO_PROJECT = {
    "fanpage_consulting": "fanpage",
    "fanpage_buy_sell": "fanpage",
    "fanpage_product_info": "fanpage",
    "fanpage_complaint": "fanpage",
    "fanpage_promo": "fanpage",
    "ihi_safety_query": "ihi",
    "iot_dashboard": "iot",
    "legal_qa": "fanpage",
    "vehix_lookup": "vehix",
    "iot_sensor_consult": "iot",
}


async def run_user(user: str, batch_id: int, questions: list, results: list,
                  project_id: str = "fanpage") -> int:
    """One user: send 10 questions sequentially. Returns count sent."""
    sent = 0
    sid = None
    for q in questions:
        resp = await asyncio.to_thread(_chat, user, q, sid, project_id)
        sid = resp.get("session_id") or sid
        results.append({
            "ts": now_ict(), "batch": batch_id, "user": user,
            "status": resp.get("status"), "latency_ms": resp.get("latency_ms", 0),
            "project_id": project_id,
        })
        sent += 1
        if resp.get("status") != 200:
            _log(f"  ✗ {user} req {sent}: status={resp.get('status')} err={resp.get('error','')[:80]}")
        await asyncio.sleep(random.uniform(3.0, 8.0))
    return sent


async def run_batch(batch_id: int, results: list) -> int:
    """One batch: 10 users, each 10 questions. 50% share topic to test cross-user learning.
    Topic distribution: 2 users on 5 shared topics, OR 4-3-3 split on 3 topics.
    This creates clusters of users chatting about the same thing so we can observe
    whether the system gets faster on repeated topics (learning curve)."""
    # Pick 2-3 topics for this batch; assign users to topics in clusters
    n_topics = random.choice([2, 3])
    # Ensure unique topics
    topics = []
    for _ in range(n_topics):
        t = gen.pick_topics(1)[0]
        attempts = 0
        while t in topics and attempts < 5:
            t = gen.pick_topics(1)[0]
            attempts += 1
        topics.append(t)
    if n_topics == 2:
        # 5 + 5
        topic_assignments = [topics[0]] * 5 + [topics[1]] * 5
    else:
        # 4 + 3 + 3
        topic_assignments = [topics[0]] * 4 + [topics[1]] * 3 + [topics[2]] * 3
    random.shuffle(topic_assignments)

    users = []
    for i, topic in enumerate(topic_assignments):
        user = f"probe_b{batch_id:03d}_u{i:02d}"
        seed = gen.SEED_QUESTIONS.get(topic, gen.SEED_QUESTIONS["fanpage_consulting"])
        questions = seed[:10]
        users.append((user, questions, topic))
    _log(f"batch {batch_id}: launching 10 users across {n_topics} topics: " +
         ", ".join(f"{t}={topic_assignments.count(t)}" for t in topics))
    t0 = time.monotonic()

    async def run_with_topic(user, questions, topic):
        project_id = TOPIC_TO_PROJECT.get(topic, "fanpage")
        sent = await run_user(user, batch_id, questions, results, project_id)
        # Tag results with topic + project
        for r in results[-sent:]:
            r["topic"] = topic
            r["project_id"] = project_id
        return sent

    tasks = [asyncio.create_task(run_with_topic(u, qs, t)) for u, qs, t in users]
    counts = await asyncio.gather(*tasks, return_exceptions=True)
    wall = time.monotonic() - t0
    completed = sum(c for c in counts if isinstance(c, int))
    _log(f"batch {batch_id}: 10 users done in {wall:.1f}s ({completed}/10 sent)")
    return 10


async def main():
    duration_min = int(os.environ.get("PROBE_DURATION_MIN", "30"))
    _log(f"=== THROUGHPUT PROBE START — duration {duration_min} min, batch=10 users, 10 req/user ===")
    end_at = time.monotonic() + duration_min * 60
    results: list = []
    batch_id = 0
    while time.monotonic() < end_at:
        await run_batch(batch_id, results)
        batch_id += 1
        # Brief pause between batches
        await asyncio.sleep(2)

    total_users = batch_id * 10
    total_reqs = len(results)
    ok = sum(1 for r in results if r.get("status") == 200)
    err = total_reqs - ok
    lats = sorted(r["latency_ms"] for r in results if r.get("status") == 200)
    summary = {
        "duration_min": duration_min,
        "total_batches": batch_id,
        "total_users_completed": total_users,
        "total_requests": total_reqs,
        "ok": ok,
        "err": err,
        "err_rate": round(err / max(total_reqs, 1) * 100, 2),
        "users_per_hour": total_users,
        "reqs_per_hour": total_reqs,
        "p50_ms": lats[len(lats)//2] if lats else 0,
        "p95_ms": lats[int(len(lats)*0.95)] if lats else 0,
        "max_ms": lats[-1] if lats else 0,
    }
    with (REPORT_DIR / "summary.json").open("w") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    # Per-topic stats
    by_topic = {}
    for r in results:
        t = r.get("topic", "?")
        by_topic.setdefault(t, []).append(r)
    topic_stats = {}
    for t, items in by_topic.items():
        ok = sum(1 for r in items if r.get("status") == 200)
        lats = sorted(r["latency_ms"] for r in items if r.get("status") == 200)
        topic_stats[t] = {
            "total": len(items), "ok": ok, "err": len(items) - ok,
            "p50_ms": lats[len(lats)//2] if lats else 0,
            "p95_ms": lats[int(len(lats)*0.95)] if lats else 0,
        }
    with (REPORT_DIR / "topic_stats.json").open("w") as f:
        json.dump(topic_stats, f, indent=2, ensure_ascii=False)
    _log(f"=== PROBE DONE ===")
    _log(json.dumps(summary, indent=2, ensure_ascii=False))
    _log("=== PER-TOPIC STATS ===")
    _log(json.dumps(topic_stats, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
