"""Realistic-Day Test Runner — continuous-load edition.

Design: the system must STAY LOADED for 6 hours. No sleep between cycles.
Every 60s we spawn a fresh "wave" of new users, each limited to 10 requests
within their first ~60s of life. As one wave finishes, the next arrives.

Adaptive user-count: cycle N compares cycle N-1's p95 to baseline. If p95
improved >10% (learning kicked in), we add 20% more users next wave. If
p95 degraded >10%, we hold steady. If stable, +10%.

Stack:
- /v1/chat (fanpage, lite mode) for user requests
- /v1/ihi/cycles?limit=1 called every 30 minutes
- /v1/admin/queue + nvidia-smi for scaler feedback

State persists to /home/hung/ai-hub/reports/realistic-day-2026-06-08/.

Stop: touch reports/realistic-day-2026-06-08/stop_signal.txt
"""
from __future__ import annotations

import argparse
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

from realistic_day_state import State, append_jsonl, read_jsonl
from realistic_day_scaler import AdaptiveScaler
import realistic_day_generator as gen

ICT = timezone(timedelta(hours=7))
REPORT_DIR = Path(os.environ.get("RDAY_REPORT_DIR", "/home/hung/ai-hub/reports/realistic-day-2026-06-08"))
API_URL = "http://127.0.0.1:8000"


def _load_api_key() -> str:
    p = Path("/home/hung/ai-hub/.env")
    PREFIX = "API_KEY" + "="
    for line in p.read_text().splitlines():
        if line.startswith(PREFIX):
            k = line.split("=", 1)[1].strip().strip('"').strip("'")
            if k and len(k) > 20:
                return k
    return ""


_KEY = os.environ.get("AIHUB_KEY") or _load_api_key()
API_KEY = _KEY
if not API_KEY:
    print("FATAL: API_KEY not set", file=sys.stderr); sys.exit(1)
if not API_KEY:
    print("FATAL: API_KEY not set", file=sys.stderr); sys.exit(1)

# Tunables
USER_REQ_GAP_MIN_S = 3.0
USER_REQ_GAP_MAX_S = 8.0
WAVE_INTERVAL_S = 60              # spawn new wave every 60s
INITIAL_USERS_PER_WAVE = 20       # wave 0 = 20 users
MAX_USERS_PER_WAVE = 200          # hard cap (matches max-concurrency default)
SCALER_INTERVAL_S = 60
IHI_PULSE_MINUTES = (15, 30)      # minutes within each WAVE for iHi pulse
REQS_PER_USER_LIFETIME = 10
CYCLE_SUMMARIES_FILE = REPORT_DIR / "cycle_summaries.jsonl"
MEMORY_RECALL_FILE = REPORT_DIR / "memory_recall.jsonl"
LEARNING_CURVE_FILE = REPORT_DIR / "learning_curve.jsonl"
IHI_PULSE_FILE = REPORT_DIR / "ihi_pulses.jsonl"


def now_ict() -> str:
    return datetime.now(ICT).strftime("%Y-%m-%dT%H:%M:%S%z")


def _chat_request(user: str, message: str, model_mode: str = "lite",
                  session_id: str = None) -> dict:
    """Call /v1/chat synchronously. Retries once on empty/JSON fail."""
    payload = {
        "project_id": "fanpage",
        "tenant_id": "realistic_day",
        "user_name": user,
        "user_message": message,
        "model_mode": model_mode,
        "enable_search": False,
    }
    if session_id:
        payload["session_id"] = session_id
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{API_URL}/v1/chat",
        data=data,
        headers={"Content-Type": "application/json", "X-API-KEY": API_KEY},
        method="POST",
    )
    t0 = time.monotonic()
    last_err = ""
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                elapsed_ms = int((time.monotonic() - t0) * 1000)
                raw = r.read().decode("utf-8", errors="replace")
                if not raw.strip():
                    last_err = f"empty body (HTTP {r.status})"
                    if attempt == 0: time.sleep(0.5); continue
                    return {"status": r.status, "latency_ms": elapsed_ms, "error": last_err, "session_id": session_id, "content": "", "model": ""}
                try:
                    body = json.loads(raw)
                except json.JSONDecodeError as e:
                    last_err = f"json: {e} raw={raw[:200]!r}"
                    if attempt == 0: time.sleep(0.5); continue
                    return {"status": r.status, "latency_ms": elapsed_ms, "error": last_err, "session_id": session_id, "content": "", "model": ""}
                return {
                    "status": r.status,
                    "latency_ms": elapsed_ms,
                    "content": body.get("content", "") if isinstance(body, dict) else "",
                    "model": body.get("model", "") if isinstance(body, dict) else "",
                    "session_id": body.get("session_id", session_id) if isinstance(body, dict) else session_id,
                    "tokens_in": (body.get("usage") or {}).get("prompt_tokens", 0) if isinstance(body, dict) else 0,
                    "tokens_out": (body.get("usage") or {}).get("completion_tokens", 0) if isinstance(body, dict) else 0,
                }
        except urllib.error.HTTPError as e:
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            err_body = e.read().decode()[:200] if hasattr(e, 'read') else str(e)
            return {"status": e.code, "latency_ms": elapsed_ms, "error": f"HTTP {e.code}: {err_body}", "session_id": session_id, "content": "", "model": ""}
        except Exception as e:
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            last_err = repr(e)[:200]
            if attempt == 0: time.sleep(0.5); continue
            return {"status": 0, "latency_ms": elapsed_ms, "error": last_err, "session_id": session_id, "content": "", "model": ""}
    return {"status": 0, "latency_ms": 0, "error": last_err or "unknown", "session_id": session_id, "content": "", "model": ""}


async def send_one(state: State, scaler: AdaptiveScaler, user: str, intent_id: str,
                   message: str, session_id: str = None, phase: str = "active",
                   wave: int = 0) -> tuple:
    """Send one chat request. Returns (log_record, session_id)."""
    resp = await asyncio.to_thread(_chat_request, user, message, "lite", session_id)
    log = {
        "ts": now_ict(),
        "wave": wave,
        "phase": phase,
        "user": user,
        "intent_id": intent_id,
        "latency_ms": resp.get("latency_ms", 0),
        "status": resp.get("status", 0),
        "model": resp.get("model", ""),
        "tokens_in": resp.get("tokens_in", 0),
        "tokens_out": resp.get("tokens_out", 0),
        "content_preview": (resp.get("content") or "")[:200].replace("\n", " "),
    }
    scaler.observe(resp.get("latency_ms", 0), resp.get("status", 0))
    state.record_asked(user, wave, intent_id, log["ts"], resp.get("latency_ms", 0), resp.get("status", 0))
    return log, resp.get("session_id") or session_id


async def run_user_lifetime(state: State, scaler: AdaptiveScaler, user: str, wave: int,
                            sem: asyncio.Semaphore) -> int:
    """One user sends up to REQS_PER_USER_LIFETIME requests, then exits."""
    info = state.users.get(user)
    if not info:
        return 0
    questions = info["questions"]
    intent_ids = info["intent_ids"]
    asked = info.get("asked", [])
    already = sum(1 for a in asked if a["wave"] == wave)
    if already >= REQS_PER_USER_LIFETIME:
        return 0
    session_id = None
    sent = 0
    for i, q in enumerate(questions):
        if already + sent >= REQS_PER_USER_LIFETIME:
            break
        intent_id = intent_ids[i]
        async with sem:
            if state.should_stop():
                return sent
            log, sid = await send_one(state, scaler, user, intent_id, q, session_id, "active", wave)
            session_id = sid or session_id
            append_jsonl(state.cycle_log_path(wave), log)
            sent += 1
            gap = random.uniform(USER_REQ_GAP_MIN_S, USER_REQ_GAP_MAX_S)
            await asyncio.sleep(gap)
    return sent


def compute_next_wave_size(state: State, scaler: AdaptiveScaler, wave: int, initial_users: int) -> int:
    """Decide how many users to spawn in this wave.

    Two caps apply (the more restrictive wins):
    1. Trend-based growth: derived from prior wave p95 vs baseline
    2. scaler.concurrency: set every 60s by AdaptiveScaler based on queue/p95/VRAM

    The scaler is the authoritative live ceiling. If it pulled concurrency down
    (queue too deep, p95 spike, VRAM emergency), we respect that immediately,
    not just at the next trend comparison.
    """
    if wave == 0:
        trend = initial_users
    elif wave == 1:
        trend = int(initial_users * 1.10)
    else:
        # Compare wave N-1 to wave 0 (baseline)
        prior = read_jsonl(state.cycle_log_path(wave - 1))
        prior_lats = sorted([r["latency_ms"] for r in prior if r.get("status") == 200 and r.get("phase") == "active"])
        baseline = read_jsonl(state.cycle_log_path(0))
        base_lats = sorted([r["latency_ms"] for r in baseline if r.get("status") == 200 and r.get("phase") == "active"])
        prior_users = state.wave_user_count.get(wave - 1, initial_users)
        if not prior_lats or not base_lats:
            trend = prior_users
        else:
            prior_p95 = prior_lats[int(len(prior_lats) * 0.95)]
            base_p95 = base_lats[int(len(base_lats) * 0.95)]
            delta_pct = (prior_p95 - base_p95) / base_p95 * 100
            if delta_pct < -10:
                trend = min(int(prior_users * 1.30), scaler.cfg.cap_max)
                print(f"  [trend] wave {wave-1} p95={prior_p95:.0f}ms vs base={base_p95:.0f}ms Δ={delta_pct:+.1f}% → users {prior_users}→{trend} (learning)", flush=True)
            elif delta_pct > 20:
                trend = max(int(prior_users * 0.85), initial_users)
                print(f"  [trend] wave {wave-1} p95={prior_p95:.0f}ms vs base={base_p95:.0f}ms Δ={delta_pct:+.1f}% → users {prior_users}→{trend} (degrading, pull back)", flush=True)
            elif delta_pct > 10:
                trend = prior_users
                print(f"  [trend] wave {wave-1} p95={prior_p95:.0f}ms vs base={base_p95:.0f}ms Δ={delta_pct:+.1f}% → hold at {trend}", flush=True)
            else:
                trend = min(int(prior_users * 1.15), scaler.cfg.cap_max)
                print(f"  [trend] wave {wave-1} p95={prior_p95:.0f}ms vs base={base_p95:.0f}ms Δ={delta_pct:+.1f}% → users {prior_users}→{trend} (stable)", flush=True)

    # Apply scaler ceiling — the scaler sees queue/p95/VRAM every 60s, so it's
    # the authoritative live cap. The trend is just our longer-horizon growth guess.
    ceiling = scaler.concurrency
    final = min(trend, ceiling)
    if final < trend:
        print(f"  [cap] scaler.concurrency={ceiling} < trend={trend} → using {final}", flush=True)
    return max(final, 1)  # never spawn zero users — that would stall the test


async def wave_spawner(state: State, scaler: AdaptiveScaler, sem: asyncio.Semaphore,
                       initial_users: int, end_at: float) -> None:
    """Spawn a fresh wave every WAVE_INTERVAL_S until end_at."""
    wave = 0
    while time.monotonic() < end_at:
        if state.should_stop():
            return
        # Decide wave size
        n_users = compute_next_wave_size(state, scaler, wave, initial_users)
        state.wave_user_count[wave] = n_users
        spawned = []
        for i in range(n_users):
            topic = gen.pick_topics(1)[0]
            user = f"simu_w{wave:03d}_u{i:03d}"
            seed = gen.SEED_QUESTIONS.get(topic, gen.SEED_QUESTIONS["fanpage_consulting"])
            # Deterministic per-user shuffle so each user sees Qs in a unique order.
            # This makes the test less predictable than fixed [:10] slicing.
            questions = gen.pick_questions_for_user(topic, REQS_PER_USER_LIFETIME, user)
            state.add_user(user, topic, questions)
            spawned.append(user)
            # Fire-and-forget user lifetime task
            asyncio.create_task(run_user_lifetime(state, scaler, user, wave, sem))
        state.save_wave(wave)
        print(f"\n[wave {wave}] spawned {len(spawned)} users at {now_ict()}", flush=True)
        wave += 1
        await asyncio.sleep(WAVE_INTERVAL_S)


async def scaler_loop(state: State, scaler: AdaptiveScaler, sem: asyncio.Semaphore,
                      end_at: float) -> None:
    """Adaptive concurrency controller — runs forever, logging decisions."""
    while time.monotonic() < end_at:
        if state.should_stop():
            return
        await asyncio.sleep(SCALER_INTERVAL_S)
        decision = scaler.step()
        if decision["action"] != "hold":
            print(f"  [scaler] {decision['action']}: {decision['old']}->{decision['new']} ({decision['reason']})", flush=True)


async def ihi_pulse_loop(end_at: float) -> None:
    """Every 30 min, fire 2 pulses (at minute 15 and 30 within each 30-min block)."""
    start = time.monotonic()
    last_pulse_minute = -1
    while time.monotonic() < end_at:
        elapsed_min = (time.monotonic() - start) / 60.0
        # 30-min blocks; within each, fire at min 14-15 and 29-30
        block_min = elapsed_min % 30
        current_marker = int(elapsed_min // 30)
        if (14.5 <= block_min <= 15.5 or 29.5 <= block_min <= 30.5):
            if last_pulse_minute != current_marker:
                last_pulse_minute = current_marker
                try:
                    req = urllib.request.Request(
                        f"{API_URL}/v1/ihi/cycles?limit=1",
                        headers={"X-API-KEY": API_KEY},
                    )
                    with urllib.request.urlopen(req, timeout=5) as r:
                        body = json.loads(r.read().decode())
                        cycles = body.get("cycles", [])
                        if cycles:
                            c0 = cycles[0]
                            pulse = {
                                "ts": now_ict(),
                                "block": current_marker,
                                "minute": int(block_min),
                                "scrape_id": c0.get("scrape_id"),
                                "status": c0.get("status"),
                                "rows_added": c0.get("rows_added"),
                                "phase_count": len(c0.get("phases", [])),
                            }
                            append_jsonl(IHI_PULSE_FILE, pulse)
                            print(f"  [ihi pulse] block={current_marker} m={int(block_min)} scrape_id={pulse['scrape_id']} status={pulse['status']}", flush=True)
                except Exception as e:
                    print(f"  [ihi pulse] failed: {e!r}", flush=True)
        await asyncio.sleep(20)


def write_wave_summary(state: State, wave: int) -> dict:
    rows = read_jsonl(state.cycle_log_path(wave))
    if not rows:
        return {"wave": wave, "total": 0}
    ok = [r for r in rows if r.get("status") == 200]
    err = [r for r in rows if r.get("status") != 200]
    latencies = sorted(r["latency_ms"] for r in ok)
    summary = {
        "wave": wave,
        "ts_end": now_ict(),
        "total": len(rows),
        "ok": len(ok),
        "err": len(err),
        "err_rate": round(len(err) / max(len(rows), 1) * 100, 2),
        "p50_ms": latencies[len(latencies) // 2] if latencies else 0,
        "p95_ms": latencies[int(len(latencies) * 0.95)] if latencies else 0,
        "max_ms": latencies[-1] if latencies else 0,
        "users": len(set(r["user"] for r in rows)),
    }
    append_jsonl(CYCLE_SUMMARIES_FILE, summary)
    return summary


def write_final_summary(state: State, total_waves: int, duration_h: float) -> None:
    rows = read_jsonl(CYCLE_SUMMARIES_FILE)
    pulses = read_jsonl(IHI_PULSE_FILE)
    md = REPORT_DIR / "SUMMARY.md"
    with md.open("w") as f:
        f.write(f"# Realistic-Day Test — Final Summary\n\n")
        f.write(f"Generated: {now_ict()}\n")
        f.write(f"Duration: {duration_h}h | Waves: {len(rows)}/{total_waves}\n\n")
        f.write("| Wave | Total | OK | Err% | p50ms | p95ms | maxms | Users |\n")
        f.write("|---|---|---|---|---|---|---|---|\n")
        for r in rows:
            f.write(f"| {r['wave']} | {r.get('total',0)} | {r.get('ok',0)} | {r.get('err_rate',0)} | {r.get('p50_ms',0)} | {r.get('p95_ms',0)} | {r.get('max_ms',0)} | {r.get('users',0)} |\n")
        f.write(f"\n## Wave user-count progression\n\n")
        f.write("| Wave | Users |\n|---|---|\n")
        for w, n in sorted(state.wave_user_count.items()):
            f.write(f"| {w} | {n} |\n")
        f.write(f"\n## IHI pulses (every 30 min)\n\n")
        if pulses:
            f.write("| Block | Minute | scrape_id | status | rows |\n|---|---|---|---|---|\n")
            for p in pulses:
                f.write(f"| {p['block']} | m{p['minute']} | {p['scrape_id']} | {p['status']} | {p['rows_added']} |\n")
        f.write(f"\n## Adaptive scaler log\n\nSee `adaptive_scaler.log` for full trace.\n")
    print(f"\n[final] SUMMARY.md written: {md}", flush=True)


async def main_async(args: argparse.Namespace) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / "cycles").mkdir(exist_ok=True)
    state = State(REPORT_DIR)
    state.pid_file.write_text(str(os.getpid()))
    print(f"[runner] PID={os.getpid()} report_dir={REPORT_DIR}", flush=True)
    print(f"[runner] duration={args.duration_hours}h wave_interval={WAVE_INTERVAL_S}s initial={args.initial_users} max={args.max_concurrency}", flush=True)

    scaler = AdaptiveScaler(state, API_KEY)
    scaler.cfg.cap_max = args.max_concurrency

    start_ts = time.monotonic()
    end_at = start_ts + args.duration_hours * 3600
    total_waves_expected = int(args.duration_hours * 3600 / WAVE_INTERVAL_S)

    # Use semaphore as a coarse concurrency guard
    sem = asyncio.Semaphore(args.max_concurrency)

    spawner_task = asyncio.create_task(wave_spawner(state, scaler, sem, args.initial_users, end_at))
    scaler_task = asyncio.create_task(scaler_loop(state, scaler, sem, end_at))
    ihi_task = asyncio.create_task(ihi_pulse_loop(end_at))

    # Periodic wave summary writer
    async def summary_loop():
        last_wave = -1
        while time.monotonic() < end_at:
            await asyncio.sleep(WAVE_INTERVAL_S)
            # Write summary for any completed wave
            for w in range(max(0, last_wave + 1), 1000):
                if state.cycle_log_path(w).exists() and w not in state.summarized_waves:
                    summary = write_wave_summary(state, w)
                    state.summarized_waves.add(w)
                    last_wave = w
                    if w % 10 == 0:
                        print(f"[summary] wave {w}: {json.dumps(summary, ensure_ascii=False)[:200]}", flush=True)
                elif w in state.summarized_waves:
                    continue
                else:
                    break

    summary_task = asyncio.create_task(summary_loop())

    # Wait for end time
    try:
        await asyncio.sleep(args.duration_hours * 3600)
    except asyncio.CancelledError:
        pass
    finally:
        print(f"\n[runner] duration elapsed, stopping all tasks", flush=True)
        spawner_task.cancel()
        scaler_task.cancel()
        ihi_task.cancel()
        summary_task.cancel()
        for t in (spawner_task, scaler_task, ihi_task, summary_task):
            try: await t
            except (asyncio.CancelledError, Exception): pass

    # Final summary
    # Wait briefly for last writes
    await asyncio.sleep(2)
    write_final_summary(state, total_waves_expected, args.duration_hours)
    print(f"\n[runner] DONE at {now_ict()}", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--duration-hours", type=float, default=6.0)
    ap.add_argument("--initial-users", type=int, default=20)
    ap.add_argument("--max-concurrency", type=int, default=200)
    args = ap.parse_args()
    try:
        asyncio.run(main_async(args))
    except KeyboardInterrupt:
        print("\n[runner] interrupted", flush=True)
        sys.exit(0)


if __name__ == "__main__":
    main()
