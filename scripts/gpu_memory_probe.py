#!/usr/bin/env python3
"""Probe GPU memory while sending concurrent AI Hub chat requests.

Reads API_KEY from .env without printing it.
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import time
from pathlib import Path
from urllib import request as urlrequest
from urllib.error import HTTPError, URLError

BASE_URL = "http://127.0.0.1:8016"
USERS = 2
TIMEOUT = 300


def load_api_key() -> str:
    for line in Path(".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("API_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("API_KEY not found")


def nvidia_sample() -> dict:
    cmd = [
        "nvidia-smi",
        "--query-gpu=timestamp,index,name,memory.used,memory.total,utilization.gpu",
        "--format=csv,noheader,nounits",
    ]
    out = subprocess.check_output(cmd, text=True).strip()
    # Single GPU expected.
    parts = [p.strip() for p in out.split(",")]
    return {
        "timestamp": parts[0],
        "index": int(parts[1]),
        "name": parts[2],
        "memory_used_mb": int(parts[3]),
        "memory_total_mb": int(parts[4]),
        "gpu_util_pct": int(parts[5]),
    }


def post_chat(api_key: str, user_idx: int) -> dict:
    msg = (
        "Bạn là bài test GPU. Hãy trả lời bằng tiếng Việt khoảng 8 câu, "
        "giải thích ngắn gọn vì sao cần đo VRAM khi chạy 2 request đồng thời. "
        f"Mã user của tôi là GPU-E4B-{user_idx:02d}."
    )
    payload = {
        "project_id": "test",
        "tenant_id": "gpu_probe_e4b",
        "user_name": f"gpu_probe_user_{user_idx:02d}",
        "user_message": msg,
        "model_mode": "lite",
        "enable_search": False,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urlrequest.Request(
        f"{BASE_URL}/v1/chat",
        data=data,
        headers={"Content-Type": "application/json", "X-API-KEY": api_key},
        method="POST",
    )
    start = time.perf_counter()
    try:
        with urlrequest.urlopen(req, timeout=TIMEOUT) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            return {
                "user": user_idx,
                "status": resp.status,
                "ok": 200 <= resp.status < 300,
                "latency_s": round(time.perf_counter() - start, 3),
                "session_id": body.get("session_id"),
                "content_preview": body.get("content", "")[:160],
            }
    except HTTPError as exc:
        return {"user": user_idx, "status": exc.code, "ok": False, "latency_s": round(time.perf_counter() - start, 3), "error": exc.read().decode(errors="replace")[:300]}
    except (URLError, TimeoutError, Exception) as exc:
        return {"user": user_idx, "status": None, "ok": False, "latency_s": round(time.perf_counter() - start, 3), "error": repr(exc)[:300]}


async def sampler(stop: asyncio.Event, samples: list[dict]) -> None:
    while not stop.is_set():
        try:
            samples.append(nvidia_sample())
        except Exception as exc:
            samples.append({"error": repr(exc)})
        await asyncio.sleep(1)


async def main() -> None:
    api_key = load_api_key()
    samples: list[dict] = []
    stop = asyncio.Event()
    print("baseline", json.dumps(nvidia_sample(), ensure_ascii=False))
    sampler_task = asyncio.create_task(sampler(stop, samples))
    start = time.perf_counter()
    results = await asyncio.gather(*[asyncio.to_thread(post_chat, api_key, i) for i in range(1, USERS + 1)])
    wall = round(time.perf_counter() - start, 3)
    stop.set()
    await asyncio.sleep(0)
    sampler_task.cancel()
    try:
        await sampler_task
    except asyncio.CancelledError:
        pass
    final = nvidia_sample()
    valid = [s for s in samples if "memory_used_mb" in s]
    peak = max(valid, key=lambda s: s["memory_used_mb"]) if valid else None
    util_peak = max(valid, key=lambda s: s["gpu_util_pct"]) if valid else None
    print("results", json.dumps(results, ensure_ascii=False, indent=2))
    print("wall_s", wall)
    print("sample_count", len(samples))
    print("peak_memory", json.dumps(peak, ensure_ascii=False))
    print("peak_util", json.dumps(util_peak, ensure_ascii=False))
    print("final", json.dumps(final, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
