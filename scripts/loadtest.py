#!/usr/bin/env python3
"""
AI Hub Continuous Load Test
Usage: python loadtest.py [duration_minutes] [concurrency] [ramp_up]

Examples:
  python loadtest.py 30 3          # 30 min, 3 concurrent
  python loadtest.py 180 5         # 3 hours, 5 concurrent
  python loadtest.py 240 3 60      # 4 hours, start at 3, ramp to 60 over 4h
"""
import asyncio
import aiohttp
import json
import time
import random
import sys
import os
from datetime import datetime

# Config
API_URL = "http://localhost:8000/v1/chat"
ENV_PATH = "/home/hung/ai-hub/.env"
REPORT_DIR = "/home/hung/ai-hub/reports"
TIMEOUT = 60
PRINT_EVERY_N = 20        # Print status every N requests
REPORT_INTERVAL_SEC = 300  # Full report every 5 min

# Read API key
API_KEY = ""
with open(ENV_PATH) as f:
    for line in f:
        if line.startswith("API_KEY="):
            API_KEY = line.split("=", 1)[1].strip()
            break

TENANTS = ["test", "tenant_a", "tenant_b"]

QUESTIONS = [
    "Xin chào, bạn là ai?",
    "Dự án AI Hub có những tính năng gì?",
    "Hướng dẫn sử dụng API chat",
    "Tạo một hàm Python sort array",
    "Giải thích machine learning là gì",
    "Viết đoạn code JavaScript fetch API",
    "So sánh PostgreSQL và MySQL",
    "Làm thế nào để deploy Docker container",
    "Giải thích REST API là gì",
    "Viết code Python đọc file CSV",
    "Tối ưu hóa query SQL như thế nào",
    "Giải thích microservices architecture",
    "WebSocket khác HTTP như thế nào",
    "Cách xử lý authentication trong API",
    "Redis dùng để làm gì?",
    "Giải thích async/await trong Python",
    "Docker compose là gì?",
    "CI/CD pipeline hoạt động như thế nào?",
    "Load balancing là gì?",
    "Caching strategy phổ biến",
    "Giải thích CAP theorem",
    "Message queue dùng khi nào?",
    "Rate limiting implementation",
    "Database indexing strategy",
    "Giải thích event-driven architecture",
]

class Stats:
    def __init__(self):
        self.lock = asyncio.Lock()
        self.total = 0
        self.success = 0
        self.error = 0
        self.timeout = 0
        self.latencies = []
        self.errors_detail = []
        self.start_time = time.monotonic()
        self.last_report = time.monotonic()
        self.report_snapshots = []

    async def record(self, success, latency, error_detail=None):
        async with self.lock:
            self.total += 1
            self.latencies.append(latency)
            if success:
                self.success += 1
            elif error_detail == "timeout":
                self.timeout += 1
            else:
                self.error += 1
                if error_detail:
                    self.errors_detail.append(error_detail[:120])
                    if len(self.errors_detail) > 100:
                        self.errors_detail = self.errors_detail[-50:]

    def summary(self):
        lats = sorted(self.latencies) if self.latencies else [0]
        elapsed_min = (time.monotonic() - self.start_time) / 60
        rpm = self.total / max(elapsed_min, 0.01)
        err_rate = (self.error + self.timeout) / max(self.total, 1) * 100

        return {
            "elapsed_min": round(elapsed_min, 1),
            "total": self.total,
            "success": self.success,
            "error": self.error,
            "timeout": self.timeout,
            "err_rate": round(err_rate, 1),
            "rpm": round(rpm, 1),
            "p50": round(lats[len(lats)//2], 2),
            "p95": round(lats[int(len(lats)*0.95)], 2),
            "p99": round(lats[int(len(lats)*0.99)], 2),
            "avg": round(sum(lats)/len(lats), 2),
            "max": round(lats[-1], 2),
        }

async def send_request(session, stats, idx):
    question = random.choice(QUESTIONS)
    tenant_id = TENANTS[idx % len(TENANTS)]
    payload = {
        "project_id": "test",
        "tenant_id": tenant_id,
        "user_message": question,
        "model_mode": "lite",
        "enable_search": False,
    }
    headers = {"Content-Type": "application/json", "X-API-KEY": API_KEY}

    t0 = time.monotonic()
    try:
        async with session.post(
            API_URL, json=payload, headers=headers,
            timeout=aiohttp.ClientTimeout(total=TIMEOUT),
        ) as resp:
            elapsed = time.monotonic() - t0
            body = await resp.text()
            if resp.status == 200:
                await stats.record(True, elapsed)
                return True
            else:
                await stats.record(False, elapsed, f"HTTP {resp.status}: {body[:80]}")
                return False
    except asyncio.TimeoutError:
        elapsed = time.monotonic() - t0
        await stats.record(False, elapsed, "timeout")
        return False
    except Exception as e:
        elapsed = time.monotonic() - t0
        await stats.record(False, elapsed, str(e)[:100])
        return False

async def worker(session, stats, queue, worker_id):
    while True:
        idx = await queue.get()
        if idx is None:
            break
        ok = await send_request(session, stats, idx)
        if idx % PRINT_EVERY_N == 0:
            s = stats.summary()
            status = "OK" if ok else "ERR"
            now = datetime.now().strftime("%H:%M:%S")
            print(f"[{now}] #{idx:>5} {status} | {s['elapsed_min']}min | {s['total']}req | {s['rpm']}rpm | p50={s['p50']}s p95={s['p95']}s | err={s['err_rate']}%", flush=True)
        queue.task_done()

async def reporter(stats, duration_sec, stop_event):
    """Print full report every REPORT_INTERVAL_SEC."""
    while not stop_event.is_set():
        await asyncio.sleep(REPORT_INTERVAL_SEC)
        if stop_event.is_set():
            break
        s = stats.summary()
        now = datetime.now().strftime("%H:%M:%S")
        remaining = max(0, (duration_sec/60) - s['elapsed_min'])
        print(f"\n{'='*60}", flush=True)
        print(f"[{now}] 📊 REPORT — {s['elapsed_min']}/{duration_sec/60:.0f}min — remaining ~{remaining:.0f}min", flush=True)
        print(f"  Requests: {s['total']} ({s['success']} ok / {s['error']} err / {s['timeout']} timeout)", flush=True)
        print(f"  Throughput: {s['rpm']} RPM", flush=True)
        print(f"  Latency: p50={s['p50']}s p95={s['p95']}s p99={s['p99']}s max={s['max']}s", flush=True)
        print(f"  Error rate: {s['err_rate']}%", flush=True)
        if stats.errors_detail:
            print(f"  Last errors: {stats.errors_detail[-3:]}", flush=True)
        print(f"{'='*60}\n", flush=True)

        # Save periodic snapshot
        stats.report_snapshots.append({**s, "snapshot_at": datetime.now().isoformat()})
        os.makedirs(REPORT_DIR, exist_ok=True)
        snap_path = f"{REPORT_DIR}/loadtest_snapshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(snap_path, "w") as f:
            json.dump({"snapshots": stats.report_snapshots, "errors": stats.errors_detail[-20:]}, f, indent=2)

        # Auto-stop if error rate > 80% after 50+ requests
        if s['total'] > 50 and s['err_rate'] > 80:
            print("⚠️ ERROR RATE > 80% — AUTO STOPPING", flush=True)
            stop_event.set()
            break

async def main():
    # Usage: python loadtest.py [duration_min] [concurrency] [ramp_to]
    #   OR:  python loadtest.py --total N [--concurrency C]
    # Examples:
    #   python loadtest.py 30 3          # 30 min, 3 concurrent
    #   python loadtest.py --total 1500 --concurrency 30   # 30x50 = 1500 requests, 30 concurrent

    total_requests = None
    concurrency = 3
    ramp_to = None
    duration_min = 30

    if "--total" in sys.argv:
        idx = sys.argv.index("--total")
        total_requests = int(sys.argv[idx + 1])
        if "--concurrency" in sys.argv:
            cidx = sys.argv.index("--concurrency")
            concurrency = int(sys.argv[cidx + 1])
        if "--ramp-to" in sys.argv:
            ridx = sys.argv.index("--ramp-to")
            ramp_to = int(sys.argv[ridx + 1])
        duration_min = 999  # won't be used
    else:
        duration_min = int(sys.argv[1]) if len(sys.argv) > 1 else 30
        concurrency = int(sys.argv[2]) if len(sys.argv) > 2 else 3
        ramp_to = int(sys.argv[3]) if len(sys.argv) > 3 else None

    duration_sec = duration_min * 60
    stats = Stats()
    stop_event = asyncio.Event()

    print(f"{'='*60}", flush=True)
    print(f"🚀 AI Hub Load Test", flush=True)
    if total_requests:
        print(f"  Mode: fixed {total_requests} requests", flush=True)
    else:
        print(f"  Mode: {duration_min} min duration", flush=True)
    print(f"  Concurrency: {concurrency}" + (f" → ramp to {ramp_to}" if ramp_to else ""), flush=True)
    print(f"  Target: {API_URL}", flush=True)
    print(f"  Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", flush=True)
    print(f"{'='*60}\n", flush=True)

    queue = asyncio.Queue()
    idx = 0

    async with aiohttp.ClientSession() as session:
        # Start initial workers
        workers = []
        current_concurrency = concurrency
        for i in range(current_concurrency):
            workers.append(asyncio.create_task(worker(session, stats, queue, i)))

        # Start reporter
        reporter_task = asyncio.create_task(reporter(stats, duration_sec, stop_event))

        end_time = time.monotonic() + duration_sec
        last_ramp = time.monotonic()

        while not stop_event.is_set():
            if total_requests and idx >= total_requests:
                break
            if not total_requests and time.monotonic() >= end_time:
                break

            await queue.put(idx)
            idx += 1

            # Ramp up concurrency if specified
            if ramp_to and ramp_to > concurrency:
                if total_requests:
                    progress = idx / total_requests
                else:
                    elapsed = time.monotonic() - stats.start_time
                    progress = elapsed / duration_sec
                target = int(concurrency + (ramp_to - concurrency) * progress)
                while current_concurrency < target and current_concurrency < ramp_to:
                    workers.append(asyncio.create_task(worker(session, stats, queue, current_concurrency)))
                    current_concurrency += 1
                    now = datetime.now().strftime("%H:%M:%S")
                    print(f"[{now}] ⬆️ Ramped to {current_concurrency} workers", flush=True)

            # Adaptive delay based on error rate
            s = stats.summary()
            if s['err_rate'] > 50 and s['total'] > 20:
                await asyncio.sleep(2.0)  # Slow down on high error rate
            else:
                await asyncio.sleep(random.uniform(0.1, 0.5))

        # Stop workers
        stop_event.set()
        for _ in workers:
            await queue.put(None)
        await asyncio.gather(*workers, return_exceptions=True)
        reporter_task.cancel()
        try:
            await reporter_task
        except asyncio.CancelledError:
            pass

    # Final report
    s = stats.summary()
    print(f"\n{'='*60}", flush=True)
    print(f"📊 FINAL RESULTS — {duration_min} min test", flush=True)
    print(f"{'='*60}", flush=True)
    print(f"  Duration: {s['elapsed_min']} min", flush=True)
    print(f"  Total requests: {s['total']}", flush=True)
    print(f"  Success: {s['success']} ({s['success']/max(s['total'],1)*100:.1f}%)", flush=True)
    print(f"  Errors: {s['error']}", flush=True)
    print(f"  Timeouts: {s['timeout']}", flush=True)
    print(f"  Throughput: {s['rpm']} RPM", flush=True)
    print(f"  Latency p50: {s['p50']}s", flush=True)
    print(f"  Latency p95: {s['p95']}s", flush=True)
    print(f"  Latency p99: {s['p99']}s", flush=True)
    print(f"  Latency avg: {s['avg']}s", flush=True)
    print(f"  Latency max: {s['max']}s", flush=True)
    print(f"  Error rate: {s['err_rate']}%", flush=True)
    if stats.errors_detail:
        print(f"\n  Last 5 errors:", flush=True)
        for e in stats.errors_detail[-5:]:
            print(f"    - {e}", flush=True)
    print(f"{'='*60}", flush=True)

    # Save report
    os.makedirs(REPORT_DIR, exist_ok=True)
    report_path = f"{REPORT_DIR}/loadtest_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(report_path, "w") as f:
        json.dump({
            "config": {
                "duration_min": duration_min,
                "concurrency": concurrency,
                "ramp_to": ramp_to,
            },
            "results": s,
            "errors": stats.errors_detail[-20:],
            "timestamp": datetime.now().isoformat(),
        }, f, indent=2)
    print(f"\n📁 Report: {report_path}", flush=True)

if __name__ == "__main__":
    asyncio.run(main())
