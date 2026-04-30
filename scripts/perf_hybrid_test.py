#!/usr/bin/env python3
"""Hybrid local/cloud benchmark for ai-hub."""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from statistics import mean
from urllib import request as urlrequest
from urllib.error import HTTPError, URLError

BASE_URL = os.getenv("AIHUB_PERF_URL", "http://127.0.0.1:8000")
TENANT_ID = os.getenv("AIHUB_HYBRID_TENANT", "hybrid")
PROJECT_ID = os.getenv("AIHUB_HYBRID_PROJECT", "test")
USER_COUNT = int(os.getenv("AIHUB_HYBRID_USERS", "5"))
TOTAL_TURNS = int(os.getenv("AIHUB_HYBRID_TURNS", "20"))
STAGGER_SECONDS = float(os.getenv("AIHUB_HYBRID_STAGGER_SECONDS", "8"))
TIMEOUT_SECONDS = float(os.getenv("AIHUB_HYBRID_TIMEOUT", "180"))
CLOUD_EVERY_N = int(os.getenv("AIHUB_HYBRID_CLOUD_EVERY_N", "4"))
OUTPUT_DIR = Path(os.getenv("AIHUB_PERF_OUTPUT", "scripts"))

TOPICS = [
    ("thoi_tiet", [
        "Khi hau nhiet doi gio mua co dac diem gi?",
        "Tai sao mien Trung Viet Nam hay co bao lu?",
        "El Nino anh huong den Viet Nam nhu the nao?",
        "Bien doi khi hau lam nuoc bien dang ra sao?",
        "Du bao thoi tiet hien dai dung cong nghe gi?",
    ]),
    ("am_thuc", [
        "Am thuc mien Bac khac mien Nam nhu the nao?",
        "Pho bo co nguon goc va bien the nao?",
        "Van hoa an uong cua nguoi Viet dac trung gi?",
        "Mon Viet nao noi tieng nhat the gioi?",
        "Gia vi nao dac trung trong am thuc Viet Nam?",
    ]),
    ("lich_su", [
        "Cac trieu dai phong kien lon cua Viet Nam la gi?",
        "Khang chien chong Mong Co dien ra ra sao?",
        "Phong trao Dong Du co y nghia gi?",
        "Cach mang thang Tam thay doi lich su ra sao?",
        "Hiep dinh Paris 1973 co y nghia gi?",
    ]),
    ("cong_nghe", [
        "AI dang thay doi cuoc song nhu the nao?",
        "Blockchain co ung dung thuc te gi?",
        "Dien toan dam may giup doanh nghiep ra sao?",
        "Xe dien phat trien nhu the nao?",
        "IoT ung dung trong nha thong minh ra sao?",
    ]),
    ("du_lich", [
        "Nhung diem den noi tieng cua Viet Nam la gi?",
        "Du lich ben vung nen lam nhu the nao?",
        "Am thuc dia phuong anh huong trai nghiem du lich ra sao?",
        "Hoi An thu hut khach quoc te vi sao?",
        "Mua nao hop de du lich mien Bac?",
    ]),
]

SUMMARY_PROMPT = "Tom tat ngan gon cac chu de va diem chinh da trao doi tu dau den gio."


@dataclass
class TurnResult:
    turn: int
    route: str
    latency_ms: float
    ok: bool
    status: int | None
    tokens_approx: int
    error: str = ""


@dataclass
class UserResult:
    user_index: int
    topic: str
    session_id: str | None = None
    turns: list[TurnResult] = field(default_factory=list)
    summary_ok: bool = False
    summary_latency_ms: float = 0.0
    summary_preview: str = ""


def load_api_key() -> str:
    for line in Path(".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("API_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("API_KEY not found")


def post_chat(api_key: str, payload: dict) -> tuple[int, dict]:
    req = urlrequest.Request(
        f"{BASE_URL}/v1/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-API-KEY": api_key},
        method="POST",
    )
    with urlrequest.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))


def route_for_turn(turn: int) -> str:
    return "cloud" if CLOUD_EVERY_N > 0 and turn % CLOUD_EVERY_N == 0 else "local"


async def chat_turn(api_key: str, user_name: str, message: str, session_id: str | None, route: str) -> tuple[int, dict, float]:
    payload = {
        "project_id": PROJECT_ID,
        "tenant_id": TENANT_ID,
        "user_name": user_name,
        "user_message": message,
        "enable_search": False,
    }
    if route == "cloud":
        payload.update({"model_mode": "external", "allow_external": True})
    else:
        payload.update({"model_mode": "lite"})
    if session_id:
        payload["session_id"] = session_id
    start = time.perf_counter()
    status, body = await asyncio.to_thread(post_chat, api_key, payload)
    return status, body, time.perf_counter() - start


async def run_user(api_key: str, user_index: int, topic: tuple[str, list[str]], progress: asyncio.Queue) -> UserResult:
    topic_name, questions = topic
    user_name = f"hybrid_user_{user_index:02d}"
    result = UserResult(user_index=user_index, topic=topic_name)
    session_id: str | None = None
    if STAGGER_SECONDS > 0:
        await asyncio.sleep((user_index - 1) * STAGGER_SECONDS)
    for turn in range(1, TOTAL_TURNS + 1):
        route = route_for_turn(turn)
        message = questions[(turn - 1) % len(questions)]
        try:
            status, body, latency = await chat_turn(api_key, user_name, message, session_id, route)
            ok = 200 <= status < 300
            if ok and not session_id:
                session_id = body.get("session_id")
            content = body.get("content", "")
            item = TurnResult(turn, route, round(latency * 1000, 1), ok, status, len(content.split()))
            if not ok:
                item.error = str(body)[:240]
        except (HTTPError, URLError, TimeoutError, Exception) as exc:
            item = TurnResult(turn, route, 0.0, False, None, 0, repr(exc)[:240])
        result.turns.append(item)
        await progress.put((user_index, turn, route, item.ok, item.latency_ms))
    result.session_id = session_id
    try:
        status, body, latency = await chat_turn(api_key, user_name, SUMMARY_PROMPT, session_id, "cloud")
        result.summary_ok = 200 <= status < 300
        result.summary_latency_ms = round(latency * 1000, 1)
        result.summary_preview = body.get("content", "")[:500]
    except Exception as exc:
        result.summary_preview = repr(exc)[:240]
    return result


def pct(values: list[float], p: int) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(max(int((p / 100) * len(ordered) + 0.999999) - 1, 0), len(ordered) - 1)
    return round(ordered[idx], 1)


def stats(results: list[UserResult], wall: float) -> dict:
    turns = [turn for result in results for turn in result.turns]
    by_route = {}
    for route in ["local", "cloud"]:
        route_turns = [turn for turn in turns if turn.route == route]
        ok_lat = [turn.latency_ms for turn in route_turns if turn.ok]
        by_route[route] = {
            "requests": len(route_turns),
            "failures": sum(1 for turn in route_turns if not turn.ok),
            "p50_ms": pct(ok_lat, 50),
            "p90_ms": pct(ok_lat, 90),
            "mean_ms": round(mean(ok_lat), 1) if ok_lat else 0.0,
        }
    return {
        "users": len(results),
        "turns_per_user": TOTAL_TURNS,
        "cloud_every_n": CLOUD_EVERY_N,
        "total_turns": len(turns),
        "total_failures": sum(1 for turn in turns if not turn.ok),
        "summary_ok": sum(1 for result in results if result.summary_ok),
        "wall_seconds": round(wall, 1),
        "routes": by_route,
    }


async def report(progress: asyncio.Queue, total: int) -> None:
    done = 0
    while done < total:
        user_idx, turn, route, ok, latency_ms = await progress.get()
        done += 1
        print(f"progress: {done}/{total} user{user_idx:02d}/turn{turn} route={route} ok={ok} lat={latency_ms:.0f}ms", flush=True)


async def main() -> None:
    api_key = load_api_key()
    topics = TOPICS[:USER_COUNT]
    progress: asyncio.Queue = asyncio.Queue()
    print(f"base_url={BASE_URL} tenant={TENANT_ID} project={PROJECT_ID}", flush=True)
    print(f"users={len(topics)} turns={TOTAL_TURNS} cloud_every_n={CLOUD_EVERY_N}", flush=True)
    start = time.perf_counter()
    reporter = asyncio.create_task(report(progress, len(topics) * TOTAL_TURNS))
    results = await asyncio.gather(*(run_user(api_key, idx, topic, progress) for idx, topic in enumerate(topics, start=1)))
    await reporter
    wall = time.perf_counter() - start
    summary = stats(results, wall)
    print("\n== HYBRID STATS ==", flush=True)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    print("\n== SUMMARY RESULTS ==", flush=True)
    for result in results:
        print(f"user{result.user_index:02d} [{result.topic}] summary_ok={result.summary_ok} lat={result.summary_latency_ms:.0f}ms | {result.summary_preview[:160]}", flush=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUTPUT_DIR / f"hybrid_result_{time.strftime('%Y%m%d_%H%M%S')}.json"
    out.write_text(json.dumps({"stats": summary, "results": [asdict(item) for item in results]}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Results saved to {out}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
