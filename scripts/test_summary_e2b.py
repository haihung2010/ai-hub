#!/usr/bin/env python3
"""
Test E2B summary: 10 users x 50 messages
Reports: latency, errors, summary count, summary quality sample
Usage:
  python test_summary_e2b.py          # run with current config
  LABEL="4slot" python test_summary_e2b.py
"""
import asyncio, aiohttp, json, time, os, random, sys
from datetime import datetime

API_URL  = "http://127.0.0.1:8000/v1/chat"
ENV_PATH = "/home/hung/ai-hub/.env"
DB_URL   = "postgresql://aihub:aihub_pass@localhost:5432/ai_hub"
LABEL    = os.environ.get("LABEL", "2slot")
USERS    = 10
MSGS     = 50
TIMEOUT  = 90

API_KEY = ""
with open(ENV_PATH) as f:
    for line in f:
        if line.startswith("API_KEY="):
            API_KEY = line.split("=", 1)[1].strip()

QUESTIONS = [
    "Xin chào, bạn là ai?",
    "Giải thích machine learning là gì?",
    "Tạo một hàm Python sort list of dicts by key",
    "So sánh PostgreSQL và MySQL chi tiết",
    "Viết code JavaScript fetch API với error handling",
    "Docker compose là gì, ví dụ thực tế?",
    "Giải thích async/await trong Python",
    "Redis dùng để làm gì, khi nào nên dùng?",
    "WebSocket khác HTTP như thế nào?",
    "Giải thích CAP theorem bằng ví dụ",
    "Load balancing strategies là gì?",
    "Viết SQL query JOIN 3 bảng users, orders, products",
    "Microservices vs monolith khi nào dùng cái nào?",
    "Rate limiting implementation trong FastAPI",
    "Giải thích event-driven architecture",
    "CI/CD pipeline hoạt động như thế nào?",
    "Caching strategy: CDN, Redis, in-memory khác nhau gì?",
    "Database indexing: B-tree vs Hash index",
    "Message queue: Kafka vs RabbitMQ",
    "OAuth2 flow giải thích chi tiết",
]

results = []
lock = asyncio.Lock()

async def user_session(session: aiohttp.ClientSession, user_id: int):
    user_name = f"test_e2b_u{user_id:02d}"
    project = "test"
    session_id = None
    ok = err = 0
    lats = []

    for i in range(MSGS):
        q = QUESTIONS[i % len(QUESTIONS)]
        payload = {
            "project_id": project,
            "user_name": user_name,
            "user_message": q,
            "model_mode": "lite",
            "enable_search": False,
        }
        if session_id:
            payload["session_id"] = session_id

        t0 = time.time()
        try:
            async with session.post(
                API_URL, json=payload,
                headers={"X-API-KEY": API_KEY, "Content-Type": "application/json"},
                timeout=aiohttp.ClientTimeout(total=TIMEOUT),
            ) as r:
                data = await r.json()
                lat = time.time() - t0
                if r.status == 200:
                    session_id = data.get("session_id", session_id)
                    lats.append(lat)
                    ok += 1
                else:
                    err += 1
                    async with lock:
                        results.append({"type":"error","user":user_name,"msg":i,"status":r.status,"detail":str(data)[:120]})
        except Exception as e:
            err += 1
            async with lock:
                results.append({"type":"error","user":user_name,"msg":i,"status":0,"detail":str(e)[:120]})

        # small jitter to avoid thundering herd
        await asyncio.sleep(random.uniform(0.1, 0.5))

    async with lock:
        results.append({"type":"user_done","user":user_name,"ok":ok,"err":err,"lats":lats})

async def count_summaries(user_names: list[str]) -> list[dict]:
    import subprocess, json as j
    names_sql = ",".join(f"'{n}'" for n in user_names)
    sql = f"""
        SELECT u.name, s.content, length(s.content) as chars, s.updated_at
        FROM summaries s JOIN users u ON u.id=s.user_id
        WHERE u.name IN ({names_sql})
        ORDER BY s.updated_at DESC
    """
    r = subprocess.run(
        ["psql", DB_URL, "-t", "-c", sql],
        capture_output=True, text=True
    )
    rows = []
    for line in r.stdout.strip().splitlines():
        parts = [p.strip() for p in line.split("|")]
        if len(parts) >= 3:
            rows.append({"user": parts[0], "chars": parts[2], "preview": parts[1][:120]})
    return rows

async def main():
    print(f"\n{'='*60}")
    print(f" E2B Summary Test — LABEL={LABEL}")
    print(f" {USERS} users x {MSGS} msgs = {USERS*MSGS} total requests")
    print(f" Started: {datetime.now().strftime('%H:%M:%S')}")
    print(f"{'='*60}\n")

    user_names = [f"test_e2b_u{i:02d}" for i in range(USERS)]
    t_start = time.time()

    async with aiohttp.ClientSession() as session:
        await asyncio.gather(*[user_session(session, i) for i in range(USERS)])

    elapsed = time.time() - t_start

    # aggregate
    user_results = [r for r in results if r["type"] == "user_done"]
    errors       = [r for r in results if r["type"] == "error"]
    all_lats     = sorted(lat for r in user_results for lat in r["lats"])
    total_ok     = sum(r["ok"] for r in user_results)
    total_err    = sum(r["err"] for r in user_results)

    def pct(lst, p): return lst[int(len(lst)*p/100)] if lst else 0

    print(f"{'='*60}")
    print(f" RESULTS — {LABEL}")
    print(f"{'='*60}")
    print(f" Total requests : {total_ok + total_err}")
    print(f" Success        : {total_ok}")
    print(f" Errors         : {total_err}")
    print(f" Elapsed        : {elapsed:.0f}s")
    print(f" Throughput     : {total_ok/elapsed:.1f} req/s")
    print(f"")
    print(f" Latency (chat):")
    print(f"   p50 = {pct(all_lats,50):.1f}s")
    print(f"   p90 = {pct(all_lats,90):.1f}s")
    print(f"   p99 = {pct(all_lats,99):.1f}s")
    print(f"   max = {all_lats[-1]:.1f}s" if all_lats else "   no data")

    if errors:
        print(f"\n Errors sample:")
        for e in errors[:5]:
            print(f"   [{e['user']}] msg#{e['msg']} status={e['status']} {e['detail']}")

    print(f"\n Checking summaries in DB...")
    summaries = await count_summaries(user_names)
    print(f" Summaries generated: {len(summaries)}/{USERS} users")
    if summaries:
        print(f"\n Sample summaries:")
        for s in summaries[:3]:
            print(f"   [{s['user']}] {s['chars']} chars | {s['preview']}")

    print(f"\n{'='*60}\n")

    # save report
    os.makedirs("reports", exist_ok=True)
    report = {
        "label": LABEL, "users": USERS, "msgs": MSGS,
        "total_ok": total_ok, "total_err": total_err,
        "elapsed": round(elapsed,1),
        "throughput": round(total_ok/elapsed,2),
        "p50": round(pct(all_lats,50),2),
        "p90": round(pct(all_lats,90),2),
        "p99": round(pct(all_lats,99),2),
        "summaries": len(summaries),
    }
    fname = f"reports/e2b_summary_test_{LABEL}_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
    with open(fname, "w") as f:
        json.dump(report, f, indent=2)
    print(f" Report saved: {fname}")

asyncio.run(main())
