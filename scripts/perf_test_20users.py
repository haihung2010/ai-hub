#!/usr/bin/env python3
"""Multi-user performance test for local ai-hub.

Measures:
- Per-turn latency (P50/P90/P99) across all users
- Latency drift: first up-to-10 turns vs last up-to-10 turns
- Failure rate per user and overall
- Context retention: whether the model summarises the full conversation after normal turns
- GPU concurrency behaviour (serialized vs overlapping calls)

Each user is assigned a distinct Vietnamese topic and cycles through
5 prepared questions for AIHUB_PERF_TURNS turns.
After normal turns, the user asks for a full conversation summary.

Output: scripts/perf_result_{timestamp}.json
"""

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
TENANT_ID = os.getenv("AIHUB_PERF_TENANT", "perf")
PROJECT_ID = os.getenv("AIHUB_PERF_PROJECT", "test")
TOTAL_TURNS = int(os.getenv("AIHUB_PERF_TURNS", "50"))
USER_COUNT = int(os.getenv("AIHUB_PERF_USERS", "20"))
STAGGER_SECONDS = float(os.getenv("AIHUB_PERF_STAGGER_SECONDS", "0"))
TIMEOUT_SECONDS = float(os.getenv("AIHUB_PERF_TIMEOUT", "300"))
OUTPUT_DIR = Path(os.getenv("AIHUB_PERF_OUTPUT", "scripts"))

# 20 distinct Vietnamese topics, 5 questions each
TOPICS: list[dict] = [
    {
        "name": "thoi_tiet",
        "questions": [
            "Khi hau nhiet doi gio mua co dac diem gi?",
            "Tai sao mien Trung Viet Nam hay co bao lu?",
            "La Nina va El Nino anh huong den thoi tiet Viet Nam nhu the nao?",
            "Bien doi khi hau lam muc nuoc bien dang nguy hiem the nao?",
            "Du bao thoi tiet hien dai dua tren nhung cong nghe gi?",
        ],
    },
    {
        "name": "am_thuc",
        "questions": [
            "Am thuc mien Bac Viet Nam co gi khac biet so voi mien Nam?",
            "Pho bo nguon goc tu dau va co cac bien the nao?",
            "Van hoa an uong cua nguoi Viet co net dac trung gi?",
            "Mon an nao cua Viet Nam duoc the gioi cong nhan nhieu nhat?",
            "Gia vi nao dac trung nhat trong am thuc Viet Nam?",
        ],
    },
    {
        "name": "lich_su",
        "questions": [
            "Cac trieu dai phong kien lon trong lich su Viet Nam la gi?",
            "Cuoc khang chien chong Mong Co dien ra nhu the nao?",
            "Phong trao Dong Du co y nghia lich su gi?",
            "Cach mang thang Tam 1945 thay doi lich su Viet Nam ra sao?",
            "Hiep dinh Paris 1973 co noi dung va y nghia nhu the nao?",
        ],
    },
    {
        "name": "cong_nghe",
        "questions": [
            "Tri tue nhan tao dang thay doi cuoc song theo huong nao?",
            "Blockchain la gi va ung dung thuc te cua no la gi?",
            "Dien toan dam may mang lai loi ich gi cho doanh nghiep?",
            "Xe dien dang phat trien nhu the nao tren the gioi?",
            "Internet van vat (IoT) ung dung trong cuoc song thuong ngay ra sao?",
        ],
    },
    {
        "name": "du_lich",
        "questions": [
            "Nhung diem den noi tieng nhat cua Viet Nam la nhung noi nao?",
            "Du lich ben vung co y nghia gi va cach thuc hien?",
            "Am thuc dia phuong dong vai tro the nao trong trai nghiem du lich?",
            "Hoi An co gi dac biet thu hut khach quoc te?",
            "Mua nao thich hop nhat de du lich mien Bac Viet Nam?",
        ],
    },
    {
        "name": "the_thao",
        "questions": [
            "Lich su bong da Viet Nam phat trien nhu the nao?",
            "Tai sao the thao dien tu (esports) ngay cang pho bien?",
            "Tap the duc thuong xuyen mang lai nhung loi ich gi cho suc khoe?",
            "Vo thuat co truyen Viet Nam co nhung mon nao noi bat?",
            "The thao hoc duong quan trong nhu the nao voi hoc sinh?",
        ],
    },
    {
        "name": "kinh_te",
        "questions": [
            "Kinh te thi truong dinh huong xa hoi chu nghia la mo hinh nhu the nao?",
            "FDI co vai tro gi trong phat trien kinh te Viet Nam?",
            "Lam phat anh huong den doi song nguoi dan ra sao?",
            "Kinh te so dang tao ra nhung co hoi va thach thuc gi?",
            "Xuat khau nong san Viet Nam co tiem nang phat trien nhu the nao?",
        ],
    },
    {
        "name": "y_te",
        "questions": [
            "He thong y te cong va tu tai Viet Nam hoat dong nhu the nao?",
            "Bao hiem y te toan dan co y nghia va thach thuc gi?",
            "Y hoc co truyen va y hoc hien dai bo sung cho nhau nhu the nao?",
            "Cac benh khong lay nhiem pho bien o Viet Nam hien nay la gi?",
            "Dinh duong hop ly quan trong nhu the nao trong phong benh?",
        ],
    },
    {
        "name": "giao_duc",
        "questions": [
            "He thong giao duc Viet Nam co cau truc nhu the nao?",
            "Nhung thach thuc lon cua giao duc pho thong Viet Nam la gi?",
            "Hoc truc tuyen va hoc truc tiep co uu nhuoc diem gi?",
            "Giao duc STEM quan trong nhu the nao trong thoi dai so?",
            "Lam the nao de khuyen khich tu duy phan bien trong hoc sinh?",
        ],
    },
    {
        "name": "moi_truong",
        "questions": [
            "O nhiem khong khi o cac do thi lon Viet Nam nghiem trong den muc nao?",
            "Rac thai nhua gay tac hai gi cho moi truong bien?",
            "Nang luong tai tao co the thay the nang luong hoa thach khong?",
            "Trong cay xanh do thi mang lai nhung loi ich gi?",
            "Moi ca nhan co the lam gi de giam dau chan carbon?",
        ],
    },
    {
        "name": "am_nhac",
        "questions": [
            "Am nhac dan gian Viet Nam co nhung the loai dac sac nao?",
            "Nhac pop Viet Nam (V-pop) phat trien nhu the nao trong 20 nam qua?",
            "Am nhac anh huong den tam ly va cam xuc con nguoi ra sao?",
            "Don ca tai tu Nam Bo co dac diem gi noi bat?",
            "Cong nghe da thay doi cach san xuat va tieu thu am nhac nhu the nao?",
        ],
    },
    {
        "name": "phap_luat",
        "questions": [
            "He thong phap luat Viet Nam duoc to chuc theo mo hinh nao?",
            "Hien phap 2013 co nhung diem moi quan trong nao?",
            "Quyen va nghia vu co ban cua cong dan Viet Nam la gi?",
            "Luat doanh nghiep tao dieu kien gi cho kinh doanh?",
            "Tai sao pho bien phap luat den nguoi dan quan trong?",
        ],
    },
    {
        "name": "khoa_hoc",
        "questions": [
            "Vu tru hinh thanh nhu the nao theo ly thuyet Big Bang?",
            "Cau truc DNA va y nghia cua no trong di truyen hoc?",
            "Cong nghe CRISPR co the thay doi y hoc ra sao?",
            "Ly thuyet tuong doi cua Einstein anh huong den vat ly hien dai the nao?",
            "Nghien cuu khoa hoc co ban quan trong nhu the nao voi phat trien?",
        ],
    },
    {
        "name": "tai_chinh_ca_nhan",
        "questions": [
            "Nguyen tac co ban cua quan ly tai chinh ca nhan la gi?",
            "Dau tu chung khoan co nhung rui ro can luu y nao?",
            "Lap ke hoach tiet kiem ngan va dai han nhu the nao?",
            "Bao hiem nhan tho co y nghia gi trong ke hoach tai chinh?",
            "Lai suat kep (compound interest) hoat dong nhu the nao?",
        ],
    },
    {
        "name": "van_hoc",
        "questions": [
            "Van hoc Viet Nam hien dai co nhung tac pham kinh dien nao?",
            "Tho Duong luat anh huong den van hoc Viet Nam nhu the nao?",
            "Truyen Kieu co y nghia gi trong van hoa va van hoc Viet?",
            "Van hoc dan gian Viet Nam phan anh dieu gi ve ban sac dan toc?",
            "Nha van Nam Cao viet ve chu de gi va co anh huong the nao?",
        ],
    },
    {
        "name": "tam_ly_hoc",
        "questions": [
            "Tam ly hoc hanh vi (behaviorism) giai thich hanh vi nguoi nhu the nao?",
            "Tri tue cam xuc (EQ) quan trong the nao trong cong viec va cuoc song?",
            "Hien tuong tam ly dam dong anh huong den quyet dinh ra sao?",
            "Lieu phap nhan thuc hanh vi (CBT) dieu tri nhung van de gi?",
            "Suc khoe tam than va suc khoe the chat lien quan nhu the nao?",
        ],
    },
    {
        "name": "nong_nghiep",
        "questions": [
            "Nong nghiep cong nghe cao dang phat trien nhu the nao o Viet Nam?",
            "Canh tac huu co co loi ich va thach thuc gi?",
            "Lua gao dong vai tro gi trong an ninh luong thuc Viet Nam?",
            "Bien doi khi hau tac dong den san xuat nong nghiep nhu the nao?",
            "Lien ket chuoi gia tri trong nong nghiep mang lai loi ich gi?",
        ],
    },
    {
        "name": "kien_truc",
        "questions": [
            "Kien truc nha o truyen thong Viet Nam co dac diem gi?",
            "Xu huong kien truc xanh va ben vung la gi?",
            "Pho co Ha Noi co gia tri kien truc va lich su nhu the nao?",
            "Kien truc Dong Duong de lai dau an gi tai Viet Nam?",
            "Nha thong minh (smart home) hoat dong dua tren cong nghe gi?",
        ],
    },
    {
        "name": "dien_anh",
        "questions": [
            "Dien anh Viet Nam co lich su phat trien nhu the nao?",
            "Phim tai lieu khac phim truyen o diem gi?",
            "Cac giai thuong dien anh quoc te lon nhat la nhung giai nao?",
            "Hieu ung dac biet (VFX) thay doi nganh dien anh ra sao?",
            "Phim Viet Nam ngay cang duoc dau tu va chu trong dieu gi?",
        ],
    },
    {
        "name": "triet_hoc",
        "questions": [
            "Triet hoc phuong Dong va phuong Tay khac nhau o diem cot loi nao?",
            "Tu tuong Khong Tu anh huong den xa hoi Viet Nam ra sao?",
            "Triet hoc hien sinh (existentialism) tra loi cau hoi gi ve con nguoi?",
            "Dao duc hoc nghien cuu nhung van de co ban nao?",
            "Moi quan he giua triet hoc va khoa hoc hien dai la gi?",
        ],
    },
]

SUMMARY_PROMPT = (
    "Hay tom tat toan bo cuoc tro chuyen cua chung ta tu dau den gio. "
    "Bao gom: chu de chinh da thao luan, cac diem noi bat va do chi tiet cua cau tra loi. "
    "Tom tat bang tieng Viet, toi thieu 5 cau."
)


@dataclass
class TurnResult:
    turn: int
    latency_ms: float
    status: int | None
    ok: bool
    tokens_approx: int
    error: str = ""


@dataclass
class UserResult:
    user_index: int
    topic: str
    session_id: str | None = None
    failed_turns: int = 0
    summary_latency_ms: float = 0.0
    summary_ok: bool = False
    summary_preview: str = ""
    turns: list[TurnResult] = field(default_factory=list)


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


async def chat_turn(
    api_key: str,
    user_name: str,
    message: str,
    session_id: str | None,
) -> tuple[int, dict, float]:
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
    start = time.perf_counter()
    status_code, body = await asyncio.to_thread(post_chat, api_key, payload)
    latency = time.perf_counter() - start
    return status_code, body, latency


async def run_user(
    api_key: str,
    user_index: int,
    topic: dict,
    progress_queue: asyncio.Queue,
) -> UserResult:
    user_name = f"perf_user_{user_index:02d}"
    result = UserResult(user_index=user_index, topic=topic["name"])
    questions = topic["questions"]
    session_id: str | None = None

    if STAGGER_SECONDS > 0:
        await asyncio.sleep((user_index - 1) * STAGGER_SECONDS)

    for turn_num in range(1, TOTAL_TURNS + 1):
        message = questions[(turn_num - 1) % len(questions)]
        try:
            status, body, latency = await chat_turn(api_key, user_name, message, session_id)
            ok = 200 <= status < 300
            if ok and not session_id:
                session_id = body.get("session_id")
            content = body.get("content", "")
            tokens_approx = len(content.split())
            tr = TurnResult(
                turn=turn_num,
                latency_ms=round(latency * 1000, 1),
                status=status,
                ok=ok,
                tokens_approx=tokens_approx,
            )
            if not ok:
                result.failed_turns += 1
                tr.error = str(body)[:300]
        except (HTTPError, URLError, TimeoutError, Exception) as exc:
            tr = TurnResult(
                turn=turn_num,
                latency_ms=0.0,
                status=None,
                ok=False,
                tokens_approx=0,
                error=repr(exc)[:300],
            )
            result.failed_turns += 1

        result.turns.append(tr)
        await progress_queue.put((user_index, turn_num, tr.ok, tr.latency_ms))

    result.session_id = session_id

    # Turn 51: full conversation summary
    try:
        status, body, latency = await chat_turn(
            api_key, user_name, SUMMARY_PROMPT, session_id
        )
        result.summary_latency_ms = round(latency * 1000, 1)
        result.summary_ok = 200 <= status < 300
        result.summary_preview = body.get("content", "")[:500]
    except Exception as exc:
        result.summary_ok = False
        result.summary_preview = repr(exc)[:300]

    return result


def _percentile(values: list[float], p: int) -> float:
    """Return a bounded nearest-rank percentile.

    statistics.quantiles(..., n=100) can extrapolate above max for small samples;
    load-test reports should never show p99 greater than the observed max.
    """
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, int((p / 100) * len(ordered) + 0.999999))
    idx = min(rank - 1, len(ordered) - 1)
    return round(ordered[idx], 1)


def _safe_mean(lst: list[float]) -> float:
    return round(mean(lst), 1) if lst else 0.0


def compute_stats(results: list[UserResult]) -> dict:
    all_ok = [t.latency_ms for r in results for t in r.turns if t.ok]
    early_end = min(10, TOTAL_TURNS)
    late_start = max(1, TOTAL_TURNS - 9)
    early = [t.latency_ms for r in results for t in r.turns if t.ok and 1 <= t.turn <= early_end]
    late = [t.latency_ms for r in results for t in r.turns if t.ok and late_start <= t.turn <= TOTAL_TURNS]
    total_turns = sum(len(r.turns) for r in results)
    total_failed = sum(r.failed_turns for r in results)
    summary_ok_count = sum(1 for r in results if r.summary_ok)

    early_mean = _safe_mean(early)
    late_mean = _safe_mean(late)
    drift_pct = (
        round(100 * (late_mean - early_mean) / max(early_mean, 1), 1)
        if early and late and late_start > 1
        else None
    )

    return {
        "users": len(results),
        "turns_per_user": TOTAL_TURNS,
        "total_turns": total_turns,
        "total_failed_turns": total_failed,
        "failure_rate_pct": round(100 * total_failed / max(total_turns, 1), 2),
        "summary_ok": summary_ok_count,
        "summary_ok_rate_pct": round(100 * summary_ok_count / max(len(results), 1), 1),
        "all_turns": {
            "p50_ms": _percentile(all_ok, 50),
            "p90_ms": _percentile(all_ok, 90),
            "p99_ms": _percentile(all_ok, 99),
            "mean_ms": _safe_mean(all_ok),
            "min_ms": round(min(all_ok), 1) if all_ok else 0.0,
            "max_ms": round(max(all_ok), 1) if all_ok else 0.0,
        },
        "early_turns": {
            "range": f"1-{early_end}",
            "p50_ms": _percentile(early, 50),
            "p90_ms": _percentile(early, 90),
            "mean_ms": early_mean,
        },
        "late_turns": {
            "range": f"{late_start}-{TOTAL_TURNS}",
            "p50_ms": _percentile(late, 50),
            "p90_ms": _percentile(late, 90),
            "mean_ms": late_mean,
        },
        "latency_drift_pct": drift_pct,
    }


async def progress_reporter(queue: asyncio.Queue, total_events: int) -> None:
    done = 0
    next_report = time.perf_counter() + 30
    while done < total_events:
        try:
            user_idx, turn, ok, latency_ms = await asyncio.wait_for(queue.get(), timeout=1.0)
            done += 1
            now = time.perf_counter()
            if now >= next_report or done == total_events:
                pct = round(100 * done / total_events, 1)
                print(
                    f"  progress: {done}/{total_events} ({pct}%) "
                    f"last=user{user_idx:02d}/turn{turn} ok={ok} lat={latency_ms:.0f}ms"
                )
                next_report = now + 30
        except asyncio.TimeoutError:
            continue


async def main() -> None:
    api_key = load_api_key()
    if USER_COUNT < 1:
        raise ValueError("AIHUB_PERF_USERS must be at least 1")
    if USER_COUNT > len(TOPICS):
        raise ValueError(f"AIHUB_PERF_USERS cannot exceed {len(TOPICS)} prepared topics")
    topics = TOPICS[:USER_COUNT]
    user_count = len(topics)
    print(f"base_url={BASE_URL}  tenant={TENANT_ID}  project={PROJECT_ID}")
    print(
        f"users={user_count}  turns_per_user={TOTAL_TURNS}  "
        f"stagger_seconds={STAGGER_SECONDS}  total_turns={user_count * TOTAL_TURNS}"
    )
    print("Starting...")

    progress_queue: asyncio.Queue = asyncio.Queue()
    total_events = user_count * TOTAL_TURNS

    wall_start = time.perf_counter()
    reporter_task = asyncio.create_task(progress_reporter(progress_queue, total_events))
    user_tasks = [
        run_user(api_key, idx + 1, topics[idx], progress_queue)
        for idx in range(user_count)
    ]
    results: list[UserResult] = await asyncio.gather(*user_tasks)
    wall_seconds = round(time.perf_counter() - wall_start, 1)
    reporter_task.cancel()

    stats = compute_stats(results)
    stats["summary_requests"] = user_count
    stats["wall_seconds"] = wall_seconds
    stats["chat_turns_per_sec"] = round(total_events / max(wall_seconds, 0.001), 2)
    stats["total_requests_per_sec"] = round((total_events + user_count) / max(wall_seconds, 0.001), 2)

    print(f"\n== OVERALL STATS (wall={wall_seconds}s) ==")
    print(json.dumps(stats, ensure_ascii=False, indent=2))

    print("\n== SUMMARY RESULTS (turn 51) ==")
    for r in sorted(results, key=lambda x: x.user_index):
        ok_mark = "OK  " if r.summary_ok else "FAIL"
        preview = r.summary_preview.replace("\n", " ")[:120]
        print(f"  user{r.user_index:02d} [{r.topic:<22}] {ok_mark} lat={r.summary_latency_ms:.0f}ms | {preview}")

    failed_users = [r for r in results if r.failed_turns > 0]
    if failed_users:
        print("\n== PER-USER FAILURES ==")
        for r in failed_users:
            print(f"  user{r.user_index:02d} [{r.topic}] failed_turns={r.failed_turns}")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    out_path = OUTPUT_DIR / f"perf_result_{ts}.json"
    payload = {
        "config": {
            "base_url": BASE_URL,
            "tenant_id": TENANT_ID,
            "project_id": PROJECT_ID,
            "users": user_count,
            "turns_per_user": TOTAL_TURNS,
            "stagger_seconds": STAGGER_SECONDS,
            "timeout_seconds": TIMEOUT_SECONDS,
        },
        "stats": stats,
        "users": [
            {
                **{k: v for k, v in asdict(r).items() if k != "turns"},
                "turns": [asdict(t) for t in r.turns],
            }
            for r in sorted(results, key=lambda x: x.user_index)
        ],
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nResults saved to {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
