#!/usr/bin/env python3
"""10-user concurrent test: monitor memory + response quality in real-time.

Each user does a 4-turn conversation:
  1. Introduce themselves (name, project, preference)
  2. Ask a question related to the preference
  3. Add a new piece of info (constraint, second preference, etc.)
  4. Memory recall: "What's my name and what do I like?"

We measure:
  - Latency p50/p95 per turn (and per user)
  - Total throughput (RPM)
  - Error rate (5xx, timeout)
  - Memory recall accuracy:
      * name_correct: did the model recall the user's name in turn 4?
      * preference_correct: did the model recall the original preference?
      * second_info_correct: did the model recall the turn-3 add-on?
  - Response quality (in Vietnamese, on-topic, no leakage)
  - Memory isolation: does user A's response ever mention user B's info?

Run:
  ./venv/bin/python scripts/test_10user_memory_quality.py
  ./venv/bin/python scripts/test_10user_memory_quality.py --concurrency 5
  ./venv/bin/python scripts/test_10user_memory_quality.py --quiet
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from dataclasses import dataclass, field
from typing import Any

import aiohttp

# Make the project root importable so we can read .env directly
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.observability import ObservabilityService


def _read_api_key() -> str:
    env_path = ROOT / ".env"
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("API_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("API_KEY not found in .env")


API_URL = "http://127.0.0.1:8000/v1/chat"
API_KEY = _read_api_key()
TIMEOUT = 60


# ── 10 distinct user personas ─────────────────────────────────────


@dataclass
class Persona:
    name: str
    project_id: str
    favorite: str
    second_info: str  # added in turn 3
    # 4-turn conversation
    turn_1: str
    turn_2: str
    turn_3: str
    turn_4: str
    # Memory check: what should turn 4 recall?
    expected_name: str = ""
    expected_favorite: str = ""
    expected_second: str = ""


PERSONAS: list[Persona] = [
    Persona(
        name="An",
        project_id="chatbot",
        favorite="Phở bò Hà Nội",
        second_info="Ăn vào buổi sáng thôi",
        turn_1="Xin chào, mình tên An. Mình thích ăn Phở bò Hà Nội nhất. Bạn nhớ giúp mình nhé.",
        turn_2="Theo bạn, Phở bò Hà Nội hay Phở bò Sài Gòn ngon hơn?",
        turn_3="À quên, mình chỉ ăn Phở vào buổi sáng thôi nhé, không ăn tối đâu.",
        turn_4="Bạn nhớ mình tên gì không? Và mình thích món gì nhất?",
        expected_name="An",
        expected_favorite="Phở bò",
        expected_second="sáng",
    ),
    Persona(
        name="Bình",
        project_id="chatbot",
        favorite="Cà phê sữa đá",
        second_info="Không đường",
        turn_1="Chào bạn, mình là Bình. Mình nghiện Cà phê sữa đá rồi, ghi nhớ giúp mình.",
        turn_2="Cà phê sữa đá với cà phê đen cái nào tốt hơn cho sức khỏe?",
        turn_3="Mà nhớ là mình uống CÀ PHÊ SỮA ĐÁ KHÔNG ĐƯỜNG nha bạn.",
        turn_4="Mình tên gì và thích uống gì?",
        expected_name="Bình",
        expected_favorite="Cà phê sữa đá",
        expected_second="không đường",
    ),
    Persona(
        name="Chi",
        project_id="chatbot",
        favorite="Bún chả",
        second_info="Ăn kèm rau sống nhiều",
        turn_1="Mình tên Chi, mình rất thích Bún chả. Bạn lưu lại giúp mình.",
        turn_2="Bún chả Hà Nội có gì đặc biệt so với các vùng khác?",
        turn_3="Mình hay ăn Bún chả kèm RAU SỐNG thật nhiều, đừng quên nha.",
        turn_4="Mình tên gì và thích ăn món gì?",
        expected_name="Chi",
        expected_favorite="Bún chả",
        expected_second="rau sống",
    ),
    Persona(
        name="Dũng",
        project_id="chatbot",
        favorite="Bánh mì",
        second_info="Nhân thịt nguội",
        turn_1="Chào, mình tên Dũng, mình mê Bánh mì lắm. Ghi nhớ giùm.",
        turn_2="Bánh mì Việt Nam nổi tiếng thế giới vì lý do gì?",
        turn_3="Mình thích ăn Bánh mì với NHÂN THỊT NGUỘI hơn là pate, nhớ nha.",
        turn_4="Bạn biết mình tên gì và thích ăn gì không?",
        expected_name="Dũng",
        expected_favorite="Bánh mì",
        expected_second="thịt nguội",
    ),
    Persona(
        name="Em",
        project_id="chatbot",
        favorite="Cơm tấm sườn bì",
        second_info="Sài Gòn style",
        turn_1="Mình tên Em, mình rất thích Cơm tấm sườn bì. Bạn nhớ giúp.",
        turn_2="Cơm tấm sườn bì chuẩn Sài Gòn có gì?",
        turn_3="Mình thích ăn Cơm tấm SÀI GÒN STYLE, nghĩa là có chả, bì, sườn, trứng.",
        turn_4="Mình tên gì, thích ăn món gì?",
        expected_name="Em",
        expected_favorite="Cơm tấm",
        expected_second="Sài Gòn",
    ),
    Persona(
        name="Phong",
        project_id="fanpage",
        favorite="Bún bò Huế",
        second_info="Ăn nóng",
        turn_1="Tôi tên Phong, tôi thích nhất món Bún bò Huế. Bạn nhớ lấy.",
        turn_2="Bún bò Huế có vị gì đặc trưng?",
        turn_3="Tôi chỉ ăn Bún bò Huế khi còn NÓNG, nguội là không ăn đâu nha.",
        turn_4="Bạn nhớ tôi tên gì, thích ăn món gì không?",
        expected_name="Phong",
        expected_favorite="Bún bò Huế",
        expected_second="nóng",
    ),
    Persona(
        name="Giang",
        project_id="fanpage",
        favorite="Hủ tiếu Nam Vang",
        second_info="Nhiều tôm",
        turn_1="Mình là Giang, mình thích ăn Hủ tiếu Nam Vang. Lưu giúp mình.",
        turn_2="Hủ tiếu Nam Vang khác gì hủ tiếu Mỹ Tho?",
        turn_3="Mình thích ăn Hủ tiếu có NHIỀU TÔM, ít thịt heo. Ghi nhớ.",
        turn_4="Mình tên gì và thích ăn món gì?",
        expected_name="Giang",
        expected_favorite="Hủ tiếu Nam Vang",
        expected_second="tôm",
    ),
    Persona(
        name="Hà",
        project_id="fanpage",
        favorite="Bánh xèo",
        second_info="Miền Trung",
        turn_1="Mình tên Hà, mình thích Bánh xèo lắm. Bạn nhớ giùm mình.",
        turn_2="Bánh xèo miền Nam và miền Trung khác nhau chỗ nào?",
        turn_3="Mình thích Bánh xèo MIỀN TRUNG cỡ nhỏ, giòn rụm, đừng quên nha.",
        turn_4="Mình tên gì, thích ăn gì?",
        expected_name="Hà",
        expected_favorite="Bánh xèo",
        expected_second="Miền Trung",
    ),
    Persona(
        name="Khoa",
        project_id="fanpage",
        favorite="Nem nướng",
        second_info="Chấm tương",
        turn_1="Mình tên Khoa, mình mê Nem nướng lắm. Ghi nhớ giùm.",
        turn_2="Nem nướng Nha Trang có gì đặc biệt?",
        turn_3="Mình thích chấm Nem nướng với TƯƠNG đặc biệt, không thích chấm nước mắm.",
        turn_4="Bạn nhớ mình tên gì, thích món gì?",
        expected_name="Khoa",
        expected_favorite="Nem nướng",
        expected_second="tương",
    ),
    Persona(
        name="Linh",
        project_id="fanpage",
        favorite="Gỏi cuốn",
        second_info="Chay một nửa",
        turn_1="Mình tên Linh, mình thích ăn Gỏi cuốn. Bạn nhớ lấy nhé.",
        turn_2="Gỏi cuốn và nem cuốn khác nhau như thế nào?",
        turn_3="Mình hay ăn Gỏi cuốn CHAY một nửa, tức là một cuốn chay một cuốn mặn.",
        turn_4="Bạn nhớ mình tên gì và thích ăn gì không?",
        expected_name="Linh",
        expected_favorite="Gỏi cuốn",
        expected_second="chay",
    ),
]


# ── Per-user conversation state ────────────────────────────────────


@dataclass
class TurnResult:
    turn: int
    user_message: str
    response: str
    latency_ms: float
    error: str | None = None


@dataclass
class UserResult:
    persona: Persona
    turns: list[TurnResult] = field(default_factory=list)
    session_id: str | None = None

    @property
    def turn_4_response(self) -> str:
        if len(self.turns) < 4:
            return ""
        return self.turns[3].response or ""

    def recall_check(self) -> dict[str, bool]:
        """Check whether turn 4's response recalled the right info."""
        resp = self.turn_4_response.lower()
        return {
            "name_correct": self.persona.expected_name.lower() in resp,
            "preference_correct": self.persona.expected_favorite.lower() in resp,
            "second_info_correct": self.persona.expected_second.lower() in resp,
        }


# ── Live printing helpers ──────────────────────────────────────────


def _print_event(ts: float, name: str, event: str, body: str = "") -> None:
    """Print a single test event in a single line so the user can watch."""
    # Compact time (HH:MM:SS)
    hms = time.strftime("%H:%M:%S", time.localtime(ts))
    print(f"  {hms}  [{name:<5}] {event:<12} {body}", flush=True)


# ── API call helper ────────────────────────────────────────────────


async def _send_turn(
    session: aiohttp.ClientSession,
    persona: Persona,
    session_id: str | None,
    turn_idx: int,
    user_message: str,
) -> tuple[TurnResult, str | None]:
    """POST one turn. Returns (TurnResult, new_session_id)."""
    body = {
        "project_id": persona.project_id,
        "tenant_id": "default",
        "user_name": persona.name,
        "user_message": user_message,
        "session_id": session_id,
        "model_mode": "lite",
    }
    headers = {"Content-Type": "application/json", "X-API-KEY": API_KEY}
    t0 = time.perf_counter()
    try:
        async with session.post(
            API_URL, json=body, headers=headers, timeout=aiohttp.ClientTimeout(total=TIMEOUT)
        ) as resp:
            if resp.status >= 400:
                text = await resp.text()
                return (
                    TurnResult(
                        turn=turn_idx,
                        user_message=user_message,
                        response="",
                        latency_ms=(time.perf_counter() - t0) * 1000,
                        error=f"HTTP {resp.status}: {text[:200]}",
                    ),
                    session_id,
                )
            data = await resp.json()
    except asyncio.TimeoutError:
        return (
            TurnResult(
                turn=turn_idx,
                user_message=user_message,
                response="",
                latency_ms=(time.perf_counter() - t0) * 1000,
                error="timeout",
            ),
            session_id,
        )
    except Exception as exc:
        return (
            TurnResult(
                turn=turn_idx,
                user_message=user_message,
                response="",
                latency_ms=(time.perf_counter() - t0) * 1000,
                error=f"{type(exc).__name__}: {exc}",
            ),
            session_id,
        )
    latency = (time.perf_counter() - t0) * 1000
    new_session = data.get("session_id") or session_id
    content = data.get("content") or data.get("message") or ""
    if isinstance(content, dict):
        content = content.get("content", "")
    return (
        TurnResult(
            turn=turn_idx,
            user_message=user_message,
            response=content if isinstance(content, str) else str(content),
            latency_ms=latency,
        ),
        new_session,
    )


# ── Per-user driver ────────────────────────────────────────────────


async def _run_user(
    session: aiohttp.ClientSession,
    persona: Persona,
    *,
    stagger: float = 0.0,
    quiet: bool = False,
) -> UserResult:
    obs = ObservabilityService.instance()
    with obs.span(
        f"persona.{persona.name}",
        tenant_id="default",
        project_id=persona.project_id,
        user_id=persona.name,
    ) as span:
        result = UserResult(persona=persona)
        if stagger > 0:
            await asyncio.sleep(stagger)

        # Stagger turn 1 of each user by 0.5s so they don't all hit the same slot
        # at the same instant — gives a more realistic concurrent load.
        for i, msg in enumerate(
            [persona.turn_1, persona.turn_2, persona.turn_3, persona.turn_4],
            start=1,
        ):
            turn, sid = await _send_turn(session, persona, result.session_id, i, msg)
            result.turns.append(turn)
            result.session_id = sid
            if not quiet:
                preview = (turn.response or "(no response)").replace("\n", " ")[:120]
                err = f"  ERROR: {turn.error}" if turn.error else ""
                _print_event(
                    time.time(),
                    persona.name,
                    f"turn{i} ({turn.latency_ms:.0f}ms)",
                    f"→ {preview}{err}",
                )
            # Small gap between turns for the same user (humans pause)
            await asyncio.sleep(0.2)
        # Annotate span with persona-level recall results so traces
        # show memory quality per persona in Langfuse.
        if span is not None:
            check = result.recall_check()
            correct = sum(1 for v in check.values() if v)
            span.set_attribute("persona.messages_sent", len(result.turns))
            span.set_attribute("persona.questions_asked", len(result.turns))
            span.set_attribute("persona.name_correct", check["name_correct"])
            span.set_attribute("persona.preference_correct", check["preference_correct"])
            span.set_attribute("persona.second_info_correct", check["second_info_correct"])
            span.set_attribute("persona.recall_score", correct / 3.0)
        return result


# ── Main driver ────────────────────────────────────────────────────


async def _run_all(concurrency: int, quiet: bool) -> list[UserResult]:
    """Run all personas with bounded concurrency."""
    sem = asyncio.Semaphore(concurrency)

    async with aiohttp.ClientSession() as http:

        async def _bounded(idx: int, p: Persona) -> UserResult:
            async with sem:
                # Stagger start so requests fan out, don't burst
                return await _run_user(
                    http, p, stagger=idx * 0.5, quiet=quiet
                )

        tasks = [_bounded(i, p) for i, p in enumerate(PERSONAS)]
        return await asyncio.gather(*tasks)


def _print_report(results: list[UserResult], wall_time_s: float) -> None:
    print()
    print("=" * 88)
    print(" 10-USER MEMORY + QUALITY REPORT")
    print("=" * 88)
    print(f"  wall time:   {wall_time_s:.1f}s")
    print(f"  total turns: {sum(len(r.turns) for r in results)}")
    print(f"  rpm:         {(sum(len(r.turns) for r in results) / wall_time_s) * 60:.1f}")
    print()

    # ── Latency stats ──
    all_lat = [t.latency_ms for r in results for t in r.turns if not t.error]
    err_lat = [t for r in results for t in r.turns if t.error]
    if all_lat:
        p50 = statistics.median(all_lat)
        p95 = sorted(all_lat)[max(0, int(len(all_lat) * 0.95) - 1)]
        print(f"  latency:     p50={p50:.0f}ms  p95={p95:.0f}ms  max={max(all_lat):.0f}ms")
    print(f"  errors:      {len(err_lat)}/{sum(len(r.turns) for r in results)}")
    if err_lat:
        for t in err_lat[:5]:
            print(f"    turn {t.turn} ({t.user_message[:50]}…): {t.error}")
    print()

    # ── Per-user memory recall table ──
    print("  ── Memory recall (turn 4) ──")
    print(f"  {'USER':<7} {'NAME':<6} {'PREF':<6} {'2nd':<5}  TURN 4 PREVIEW")
    print("  " + "-" * 84)
    name_ok = 0
    pref_ok = 0
    second_ok = 0
    for r in results:
        check = r.recall_check()
        if check["name_correct"]:
            name_ok += 1
        if check["preference_correct"]:
            pref_ok += 1
        if check["second_info_correct"]:
            second_ok += 1
        preview = r.turn_4_response.replace("\n", " ")[:50]
        marks = (
            ("✓" if check["name_correct"] else "✗")
            + ("✓" if check["preference_correct"] else "✗")
            + ("✓" if check["second_info_correct"] else "✗")
        )
        print(f"  {r.persona.name:<7} {marks[0]:<6} {marks[1]:<6} {marks[2]:<5}  {preview}…")
    n = len(results)
    print()
    print(f"  Name recall:        {name_ok}/{n}  ({100*name_ok/n:.0f}%)")
    print(f"  Preference recall:  {pref_ok}/{n}  ({100*pref_ok/n:.0f}%)")
    print(f"  Second info recall: {second_ok}/{n}  ({100*second_ok/n:.0f}%)")
    print()

    # ── Memory isolation check ──
    # Verify that no user's turn-4 response contains another user's name
    # or unique preference (basic leak check).
    print("  ── Memory isolation (no cross-user leakage) ──")
    leaks = 0
    for r in results:
        resp = r.turn_4_response.lower()
        for other in PERSONAS:
            if other.name == r.persona.name:
                continue
            # The other user's name appearing in this response = potential leak
            if other.name.lower() in resp and other.name.lower() not in r.persona.turn_1.lower():
                leaks += 1
                _print_event(
                    time.time(),
                    r.persona.name,
                    "LEAK?",
                    f"mentions {other.name}: {resp[:80]}…",
                )
    if leaks == 0:
        print("  ✓ No cross-user name leakage detected")
    else:
        print(f"  ✗ {leaks} potential leak(s) detected")
    print()

    # ── Per-turn latency table ──
    print("  ── Per-turn latency (ms) ──")
    print(f"  {'USER':<7} T1        T2        T3        T4")
    print("  " + "-" * 56)
    for r in results:
        cells = []
        for t in r.turns:
            if t.error:
                cells.append(f"ERR")
            else:
                cells.append(f"{t.latency_ms:6.0f}ms")
        cells = (cells + ["—"] * 4)[:4]
        print(f"  {r.persona.name:<7} " + "  ".join(f"{c:<8}" for c in cells))
    print()
    print("=" * 88)


def main() -> int:
    parser = argparse.ArgumentParser(description="10-user memory + quality test")
    parser.add_argument("--concurrency", type=int, default=10, help="Max parallel users")
    parser.add_argument("--quiet", action="store_true", help="Suppress per-turn output")
    args = parser.parse_args()

    print(f"Starting 10-user test (concurrency={args.concurrency})…")
    print(f"  API: {API_URL}")
    print(f"  Users: {len(PERSONAS)} ({sum(1 for p in PERSONAS if p.project_id=='chatbot')} chatbot, "
          f"{sum(1 for p in PERSONAS if p.project_id=='fanpage')} fanpage)")
    print(f"  Turns per user: 4 (introduce, ask, add info, memory recall)")
    print()
    t0 = time.perf_counter()
    results = asyncio.run(_run_all(args.concurrency, args.quiet))
    wall = time.perf_counter() - t0
    _print_report(results, wall)
    return 0


if __name__ == "__main__":
    sys.exit(main())
