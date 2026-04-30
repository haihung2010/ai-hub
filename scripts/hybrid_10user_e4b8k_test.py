#!/usr/bin/env python3
"""10-user hybrid benchmark for AI Hub: local-gemma4-e4b-q4 8k local + OpenRouter cloud."""
from __future__ import annotations

import asyncio
import json
import sqlite3
import os
import statistics
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib import error as urlerror
from urllib import request as urlrequest

ROOT = Path(__file__).resolve().parents[1]
BASE_URL = os.getenv("AIHUB_TEST_URL", "http://127.0.0.1:8010")
TENANT_ID = f"hybrid10_e4b8k_{time.strftime('%Y%m%d_%H%M%S')}"
PROJECT_ID = "test"
USERS = 10
TURNS = 6
CLOUD_TURNS = {3, 6}
TIMEOUT_SECONDS = 900
DB_PATH = os.getenv("AIHUB_TEST_DB_PATH", "")

TOPICS = [
    ("weather", ["Khi hau nhiet doi gio mua co dac diem gi?", "El Nino anh huong Viet Nam ra sao?", "Mua bao mien Trung can chuan bi gi?"]),
    ("food", ["Am thuc mien Bac khac mien Nam nhu the nao?", "Pho bo co nguon goc ra sao?", "Gia vi Viet Nam dac trung la gi?"]),
    ("history", ["Cac trieu dai lon cua Viet Nam la gi?", "Khang chien chong Mong Co dien ra ra sao?", "Hiep dinh Paris 1973 co y nghia gi?"]),
    ("tech", ["AI dang thay doi doanh nghiep nhu the nao?", "IoT ung dung trong nha may ra sao?", "Cloud giup startup nhu the nao?"]),
    ("travel", ["Nhung diem den noi tieng cua Viet Nam la gi?", "Du lich ben vung nen lam nhu the nao?", "Hoi An thu hut khach vi sao?"]),
    ("finance", ["Quan ly dong tien ca nhan nen bat dau tu dau?", "Rui ro khi dau tu ngan han la gi?", "Da dang hoa danh muc co loi gi?"]),
    ("health", ["Ngu ngon quan trong nhu the nao?", "Tap the duc nhe moi ngay co loi gi?", "An uong can bang gom nhung gi?"]),
    ("education", ["Hoc lap trinh nen bat dau tu dau?", "Ghi chu hieu qua nhu the nao?", "AI ho tro giao duc ra sao?"]),
    ("business", ["Startup nen validate y tuong nhu the nao?", "Cham soc khach hang tot can gi?", "KPI nen thiet ke ra sao?"]),
    ("security", ["Bao mat API key nen lam the nao?", "Rate limit giup he thong ra sao?", "Log audit can ghi nhung gi?"]),
]

@dataclass
class TurnResult:
    user: int
    topic: str
    turn: int
    route: str
    ok: bool
    status: int | None
    latency_ms: float
    hit: bool = False
    leaks: list[str] = field(default_factory=list)
    provider: str | None = None
    model: str | None = None
    session_id: str | None = None
    preview: str = ""
    error: str = ""


def load_api_key() -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("API_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("API_KEY not found")

API_KEY = load_api_key()
HEADERS = {"Content-Type": "application/json", "X-API-KEY": API_KEY}


def post_chat(payload: dict) -> tuple[bool, int | None, dict | str, float]:
    req = urlrequest.Request(
        f"{BASE_URL}/v1/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers=HEADERS,
        method="POST",
    )
    start = time.perf_counter()
    try:
        with urlrequest.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            return 200 <= resp.status < 300, resp.status, body, (time.perf_counter() - start) * 1000
    except urlerror.HTTPError as exc:
        return False, exc.code, exc.read().decode("utf-8", errors="replace")[:500], (time.perf_counter() - start) * 1000
    except Exception as exc:
        return False, None, repr(exc), (time.perf_counter() - start) * 1000


def make_payload(user_idx: int, message: str, route: str, session_id: str | None = None) -> dict:
    payload = {
        "project_id": PROJECT_ID,
        "tenant_id": TENANT_ID,
        "user_name": f"hybrid10_user_{user_idx:02d}",
        "user_message": message,
        "enable_search": False,
        "model_mode": "external" if route == "cloud" else "lite",
    }
    if route == "cloud":
        payload["allow_external"] = True
    if session_id:
        payload["session_id"] = session_id
    return payload


async def one_call(user_idx: int, topic: str, turn: int, route: str, msg: str, code: str, all_codes: list[str], session_id: str | None) -> TurnResult:
    ok, status, body, latency_ms = await asyncio.to_thread(post_chat, make_payload(user_idx, msg, route, session_id))
    content = body.get("content", "") if isinstance(body, dict) else str(body)
    leaks = [item for item in all_codes if item != code and item in content]
    return TurnResult(
        user=user_idx,
        topic=topic,
        turn=turn,
        route=route,
        ok=ok,
        status=status,
        latency_ms=round(latency_ms, 1),
        hit=code in content,
        leaks=leaks,
        provider=body.get("provider") if isinstance(body, dict) else None,
        model=body.get("model") if isinstance(body, dict) else None,
        session_id=body.get("session_id") if isinstance(body, dict) else session_id,
        preview=content[:240],
        error="" if ok else content[:240],
    )


async def run_user(user_idx: int, topic_data: tuple[str, list[str]], all_codes: list[str], progress: asyncio.Queue) -> list[TurnResult]:
    topic, questions = topic_data
    code = all_codes[user_idx - 1]
    session_id: str | None = None
    results: list[TurnResult] = []
    for turn in range(1, TURNS + 1):
        route = "cloud" if turn in CLOUD_TURNS else "local"
        if turn == 1:
            msg = f"Bạn là bài test hybrid 10 users. Hãy ghi nhớ mã riêng của tôi là {code}. Trả lời ngắn và phải chứa đúng mã {code}."
        elif turn == TURNS:
            msg = "Kiểm tra cuối: không dùng thông tin user khác, chỉ trả lời mã riêng tôi đã nói từ đầu."
        else:
            msg = questions[(turn - 2) % len(questions)] + f" Nhắc kín: mã riêng phiên này là {code}."
        item = await one_call(user_idx, topic, turn, route, msg, code, all_codes, session_id)
        if item.session_id:
            session_id = item.session_id
        results.append(item)
        await progress.put(item)
    return results


def bounded_pct(values: list[float], pct: int) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(max(int((pct / 100) * len(ordered) + 0.999999) - 1, 0), len(ordered) - 1)
    return round(ordered[idx], 1)


def db_counts() -> dict:
    if not DB_PATH or not Path(DB_PATH).exists():
        return {"missing": DB_PATH or "AIHUB_TEST_DB_PATH not set"}
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    counts = {}
    for table in ["users", "sessions", "messages", "summaries", "pinned_memories", "memory_episodes", "memory_items", "prediction_records", "usage_events", "api_keys"]:
        try:
            counts[table] = cur.execute(f"select count(*) from {table}").fetchone()[0]
        except Exception as exc:
            counts[table] = repr(exc)
    conn.close()
    return counts


def shell(cmd: str) -> str:
    try:
        return subprocess.check_output(cmd, shell=True, text=True, timeout=20).strip()
    except Exception as exc:
        return repr(exc)


async def reporter(progress: asyncio.Queue, total: int) -> None:
    done = 0
    while done < total:
        item = await progress.get()
        done += 1
        print(
            f"progress {done}/{total} user{item.user:02d}/turn{item.turn} route={item.route} ok={item.ok} hit={item.hit} leaks={len(item.leaks)} lat={item.latency_ms:.0f}ms",
            flush=True,
        )


def summarize(results: list[TurnResult], wall_seconds: float) -> dict:
    by_route = {}
    for route in ["local", "cloud"]:
        xs = [r for r in results if r.route == route]
        lat = [r.latency_ms for r in xs if r.ok]
        by_route[route] = {
            "requests": len(xs),
            "ok": sum(r.ok for r in xs),
            "failures": sum(not r.ok for r in xs),
            "hits": sum(r.hit for r in xs),
            "leaks": sum(len(r.leaks) for r in xs),
            "mean_ms": round(statistics.mean(lat), 1) if lat else 0.0,
            "p50_ms": bounded_pct(lat, 50),
            "p90_ms": bounded_pct(lat, 90),
            "max_ms": max(lat) if lat else 0.0,
            "statuses": sorted({str(r.status) for r in xs}),
        }
    by_turn = {}
    for turn in range(1, TURNS + 1):
        xs = [r for r in results if r.turn == turn]
        lat = [r.latency_ms for r in xs if r.ok]
        by_turn[str(turn)] = {
            "route": "cloud" if turn in CLOUD_TURNS else "local",
            "ok": sum(r.ok for r in xs),
            "hits": sum(r.hit for r in xs),
            "leaks": sum(len(r.leaks) for r in xs),
            "mean_ms": round(statistics.mean(lat), 1) if lat else 0.0,
            "max_ms": max(lat) if lat else 0.0,
        }
    return {
        "users": USERS,
        "turns_per_user": TURNS,
        "total_requests": len(results),
        "total_ok": sum(r.ok for r in results),
        "total_failures": sum(not r.ok for r in results),
        "total_hits": sum(r.hit for r in results),
        "total_leaks": sum(len(r.leaks) for r in results),
        "wall_seconds": round(wall_seconds, 1),
        "routes": by_route,
        "turns": by_turn,
    }


async def main() -> None:
    stamp = time.strftime("%H%M%S")
    all_codes = [f"HYB10-8K-{stamp}-{i:02d}" for i in range(1, USERS + 1)]
    progress: asyncio.Queue = asyncio.Queue()
    print(f"base_url={BASE_URL} tenant={TENANT_ID} users={USERS} turns={TURNS} cloud_turns={sorted(CLOUD_TURNS)}", flush=True)
    start = time.perf_counter()
    rep = asyncio.create_task(reporter(progress, USERS * TURNS))
    nested = await asyncio.gather(*(run_user(idx, TOPICS[idx - 1], all_codes, progress) for idx in range(1, USERS + 1)))
    await rep
    wall = time.perf_counter() - start
    results = [item for sub in nested for item in sub]
    report = {
        "config": {
            "local_model": "local-gemma4-e4b-q4",
            "requested_context": 8192,
            "gpu_concurrency": 1,
            "cloud_model": "openai/gpt-oss-20b:free",
            "tenant_id": TENANT_ID,
            "db_path": DB_PATH,
        },
        "summary": summarize(results, wall),
        "db_counts": db_counts(),
        "gpu_after": shell("nvidia-smi --query-gpu=index,name,memory.used,memory.total,utilization.gpu --format=csv,noheader,nounits"),
        "results": [asdict(r) for r in results],
    }
    out = ROOT / "reports" / f"hybrid10_e4b8k_openrouter20b_{time.strftime('%Y%m%d_%H%M%S')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n== HYBRID10 SUMMARY ==", flush=True)
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2), flush=True)
    print("DB_COUNTS", json.dumps(report["db_counts"], ensure_ascii=False), flush=True)
    print("GPU_AFTER", report["gpu_after"], flush=True)
    print(f"REPORT_PATH={out}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
