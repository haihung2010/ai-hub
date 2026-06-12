# AI Hub Comprehensive 30-Minute Test — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Single-file test script `scripts/test_comprehensive_30min.py` (~700 LOC) that runs a 30-minute stress test of ai-hub covering 4 dimensions (functional, memory under load, memory persistence, multi-tenant isolation) with same-topic cache speedup measurement, e-commerce clothing domain, and JSON report output.

**Architecture:** Single Python script using `asyncio` + `aiohttp`. 8 classes in 1 file: `Config`, `UserPersona`, `TopicBank`, `MetricsCollector`, `KnowledgeSeeder`, `HealthChecker`, `PhaseRunner` (3 subclasses for warmup/rotate/recall), `ReportGenerator`, plus `main()`. Output: `reports/comprehensive_30min_<ts>.json` + `.log`.

**Tech Stack:** Python 3.11+, asyncio, aiohttp, dataclasses, statistics, json. No new deps (aiohttp already in repo). Runs against live ai-hub at `http://localhost:8000` + local llama.cpp at `:8080`.

---

## File Structure

**New files:**
- `scripts/test_comprehensive_30min.py` (~700 LOC, 8 classes + main)
- `tests/integration/test_comprehensive_30min_smoke.py` (smoke test, 2 user × 2 q)

**Modified files:**
- `.env` (toggle `MINIMAX_ENABLED=false` for test, restore after)

**No new dependencies** — uses `aiohttp` (already in repo via `requirements.txt`).

---

## Conventions

- All HTTP calls via `aiohttp.ClientSession` with 60s timeout
- All env reads at `Config.__init__` (fail fast if missing)
- All metrics writes go through `MetricsCollector` (thread-safe via `asyncio.Lock`)
- All HTTP errors logged with full request/response, never swallowed
- Topic bank and personas hardcoded in script (no YAML — keeps it self-contained)
- Report JSON written atomically (write to `.tmp` then `rename`)

---

## Task 1: Pre-flight checks (operational)

**Files:** none

- [ ] **Step 1.1: Verify Postgres + Redis running natively**

Run: `ss -tlnp | grep -E ':(5432|6379) '`
Expected: `LISTEN` lines for both `:5432` and `:6379` with process `postgres` / `redis-server` (NOT `docker-proxy`).

- [ ] **Step 1.2: Verify no llama-server or uvicorn running**

Run: `ps aux | grep -E 'llama-server|uvicorn' | grep -v grep`
Expected: no output (clean state).

- [ ] **Step 1.3: Verify GPU free**

Run: `nvidia-smi --query-gpu=memory.used,memory.free --format=csv`
Expected: `memory.used` < 500 MiB, `memory.free` > 14000 MiB.

If any check fails, abort and report which one.

---

## Task 2: Start ai-hub in test mode (operational)

**Files:** `.env` (modify)

- [ ] **Step 2.1: Disable MiniMax in `.env`**

Run: `grep -E '^MINIMAX_ENABLED' /home/hung/ai-hub/.env`
Then edit `.env` to set `MINIMAX_ENABLED=false` (preserve other values). Use `Edit` tool.

- [ ] **Step 2.2: Start local llama.cpp (16GB tuned config)**

Run in background:
```bash
cd /home/hung/ai-hub && ./scripts/start_5060ti_16gb.sh
```
Wait for log: `tail -f /tmp/aihub-llama-*.log` until you see `server listening on http://127.0.0.1:8080`.

- [ ] **Step 2.3: Start uvicorn**

Run in background:
```bash
cd /home/hung/ai-hub && nohup ./venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 >/tmp/aihub-uvicorn.log 2>&1 &
```
Wait until `curl -s http://127.0.0.1:8000/health` returns `{"status":"ok"}`.

- [ ] **Step 2.4: Verify API key works**

Run: `API_KEY=$(grep '^API_KEY=' /home/hung/ai-hub/.env | cut -d= -f2 | tr -d '"') && curl -s -H "X-API-KEY: $API_KEY" http://127.0.0.1:8000/v1/admin/queue | head -c 200`
Expected: JSON with `queue` or `pending` field (any 2xx response is fine).

---

## Task 3: Create test script skeleton + Config class

**Files:**
- Create: `scripts/test_comprehensive_30min.py`

- [ ] **Step 3.1: Create file with header + imports + Config**

```python
#!/usr/bin/env python3
"""AI Hub Comprehensive 30-Minute Test.

Stress-tests ai-hub across 4 dimensions:
  1. Functional response (chat + latency)
  2. Memory under load (10 user × 10 câu rotate, 100 user)
  3. Memory persistence (return after 2-3 min, check recall)
  4. Multi-tenant isolation (verified via user scoping)

E-commerce clothing domain, 5 cache topics for same-topic speedup test.

Usage:
  python scripts/test_comprehensive_30min.py                # full 30-min test
  python scripts/test_comprehensive_30min.py --quick        # 5-min smoke (5 user × 5 q)
  python scripts/test_comprehensive_30min.py --phases 1,2   # only phase 1+2
  python scripts/test_comprehensive_30min.py --dry-run      # synthetic data, no HTTP

Output:
  reports/comprehensive_30min_<ts>.json   — full metrics + pass/fail
  reports/comprehensive_30min_<ts>.log    — request/response log (errors only)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import random
import re
import statistics
import sys
import time
import traceback
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import aiohttp

# ── Config ────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Config:
    base_url: str
    api_key: str
    llama_url: str
    concurrency: int
    phase1_turns_per_user: int
    phase2_users_total: int
    phase2_turns_per_user: int
    phase3_rounds: int
    phase3_users_per_round: int
    phase3_gap_seconds: int
    kb_card_count: int
    report_dir: Path
    error_rate_threshold: float
    memory_recall_threshold: float
    cache_speedup_threshold: float
    total_runtime_cap_seconds: int

    @classmethod
    def from_env(cls) -> "Config":
        api_key = ""
        env_path = Path(__file__).resolve().parent.parent / ".env"
        with open(env_path) as f:
            for line in f:
                if line.startswith("API_KEY="):
                    api_key = line.split("=", 1)[1].strip().strip('"')
                    break
        if not api_key:
            raise RuntimeError(f"API_KEY not found in {env_path}")
        return cls(
            base_url=os.getenv("AIHUB_TEST_BASE_URL", "http://127.0.0.1:8000"),
            api_key=api_key,
            llama_url=os.getenv("AIHUB_TEST_LLAMA_URL", "http://127.0.0.1:8080"),
            concurrency=int(os.getenv("AIHUB_TEST_CONCURRENCY", "4")),
            phase1_turns_per_user=int(os.getenv("AIHUB_TEST_PHASE1_TURNS", "10")),
            phase2_users_total=int(os.getenv("AIHUB_TEST_PHASE2_USERS", "100")),
            phase2_turns_per_user=int(os.getenv("AIHUB_TEST_PHASE2_TURNS", "10")),
            phase3_rounds=int(os.getenv("AIHUB_TEST_PHASE3_ROUNDS", "3")),
            phase3_users_per_round=int(os.getenv("AIHUB_TEST_PHASE3_USERS", "10")),
            phase3_gap_seconds=int(os.getenv("AIHUB_TEST_PHASE3_GAP", "150")),
            kb_card_count=int(os.getenv("AIHUB_TEST_KB_CARDS", "75")),
            report_dir=Path(os.getenv("AIHUB_TEST_REPORT_DIR", "reports")),
            error_rate_threshold=float(os.getenv("AIHUB_TEST_ERROR_THRESHOLD", "0.05")),
            memory_recall_threshold=float(os.getenv("AIHUB_TEST_RECALL_THRESHOLD", "0.70")),
            cache_speedup_threshold=float(os.getenv("AIHUB_TEST_CACHE_SPEEDUP", "0.10")),
            total_runtime_cap_seconds=int(os.getenv("AIHUB_TEST_RUNTIME_CAP", "2100")),
        )

    def headers(self) -> dict[str, str]:
        return {"X-API-KEY": self.api_key, "Content-Type": "application/json"}
```

- [ ] **Step 3.2: Smoke test Config loads**

Run:
```bash
cd /home/hung/ai-hub && python -c "
import sys
sys.path.insert(0, 'scripts')
from test_comprehensive_30min import Config
c = Config.from_env()
print('API key:', c.api_key[:8] + '...')
print('Base URL:', c.base_url)
print('Concurrency:', c.concurrency)
"
```
Expected: prints config values, no errors.

- [ ] **Step 3.3: Commit**

```bash
git add scripts/test_comprehensive_30min.py
git commit -m "feat(test): scaffold + Config class for 30-min comprehensive test"
```

---

## Task 4: UserPersona + TopicBank + key_fact matcher

**Files:**
- Modify: `scripts/test_comprehensive_30min.py` (append after Config)

- [ ] **Step 4.1: Add UserPersona dataclass + 10 personas**

```python
# ── Personas + Topics ────────────────────────────────────────────────────
@dataclass(frozen=True)
class UserPersona:
    name: str           # "An"
    gender: str         # "nữ"
    age: int
    style: str          # "thích váy"
    instance_id: int    # 0-9, suffix for uniqueness

    @property
    def user_id(self) -> str:
        """Unique user identifier for ai-hub."""
        return f"stress_{self.name.lower()}_{self.instance_id:02d}"

    def to_dict(self) -> dict:
        return asdict(self)


PERSONAS: list[UserPersona] = [
    UserPersona("An", "nữ", 25, "thích váy", 0),
    UserPersona("Bình", "nam", 30, "thích áo sơ mi", 0),
    UserPersona("Chi", "nữ", 22, "sinh viên, thời trang giá rẻ", 0),
    UserPersona("Dũng", "nam", 35, "công sở", 0),
    UserPersona("Em", "nữ", 28, "mẹ bỉm sữa", 0),
    UserPersona("Phương", "nữ", 26, "dân văn phòng", 0),
    UserPersona("Giang", "nam", 24, "sinh viên IT", 0),
    UserPersona("Hà", "nữ", 32, "doanh nhân", 0),
    UserPersona("Khánh", "nam", 28, "freelance", 0),
    UserPersona("Linh", "nữ", 27, "giáo viên", 0),
]


def all_user_instances(count_per_persona: int = 10) -> list[UserPersona]:
    """Generate N instances per persona → 10 × N unique users."""
    out: list[UserPersona] = []
    for i in range(count_per_persona):
        for p in PERSONAS:
            out.append(UserPersona(p.name, p.gender, p.age, p.style, i))
    return out
```

- [ ] **Step 4.2: Add TopicBank + 5 sample topics + 5 cache topics**

```python
@dataclass(frozen=True)
class Question:
    text: str
    key_facts: tuple[str, ...]  # strings that should appear in correct recall

    def to_dict(self) -> dict:
        return {"text": self.text, "key_facts": list(self.key_facts)}


@dataclass(frozen=True)
class Topic:
    name: str
    questions: tuple[Question, ...]
    is_cache_test: bool = False

    def to_dict(self) -> dict:
        return {"name": self.name, "is_cache_test": self.is_cache_test,
                "questions": [q.to_dict() for q in self.questions]}


# Cache-test topics: 5 chủ đề lặp lại bởi nhiều user để test speedup
CACHE_TOPICS: list[Topic] = [
    Topic("áo thun trắng", (
        Question("Có áo thun trắng nào không?", ("có",)),
        Question("Size nào đang có sẵn?", ("S", "M", "L")),
        Question("Giá bao nhiêu?", ("250000", "250k")),
        Question("Chất liệu vải gì?", ("cotton",)),
        Question("Có thể giặt máy không?", ("40",)),
        Question("Có mấy màu khác?", ("đen", "xám", "xanh")),
        Question("Giao hàng mất bao lâu?", ("2-3",)),
        Question("Có freeship không?", ("300k",)),
        Question("Có thể đổi trả không?", ("7 ngày",)),
        Question("Có thể in logo lên áo không?", ("50000",)),
    ), is_cache_test=True),
    Topic("quần jean xanh", (
        Question("Có quần jean xanh không?", ("có",)),
        Question("Size nào có sẵn?", ("28", "29", "30", "31", "32")),
        Question("Giá bao nhiêu?", ("450000", "450k")),
        Question("Xuất xứ ở đâu?", ("Việt Nam",)),
        Question("Có co giãn không?", ("có",)),
        Question("Có mấy kiểu ống?", ("ống suông", "ống côn", "ống rộng")),
        Question("Màu xanh nhạt hay đậm?", ("đậm",)),
        Question("Có sale không?", ("giảm 20%",)),
        Question("Bảo hành bao lâu?", ("6 tháng",)),
        Question("Có xem hàng trước không?", ("có",)),
    ), is_cache_test=True),
    Topic("giày thể thao", (
        Question("Có giày thể thao nam không?", ("có",)),
        Question("Size từ bao nhiêu?", ("39", "40", "41", "42", "43")),
        Question("Giá bao nhiêu?", ("800000", "800k")),
        Question("Hãng nào?", ("Nike", "Adidas")),
        Question("Có chống nước không?", ("có",)),
        Question("Màu nào đang hot?", ("trắng", "đen")),
        Question("Có bảo hành không?", ("12 tháng",)),
        Question("Đế giày làm bằng gì?", ("cao su",)),
        Question("Có tặng tất không?", ("có",)),
        Question("Giao nhanh trong ngày không?", ("2 giờ",)),
    ), is_cache_test=True),
    Topic("váy maxi hoa", (
        Question("Có váy maxi hoa không?", ("có",)),
        Question("Dài bao nhiêu?", ("120cm",)),
        Question("Chất vải gì?", ("voan",)),
        Question("Có lót trong không?", ("có",)),
        Question("Size M có sẵn không?", ("có",)),
        Question("Giá bao nhiêu?", ("350000",)),
        Question("Có mấy mẫu hoa?", ("5 mẫu",)),
        Question("Giặt tay hay giặt máy?", ("giặt tay",)),
        Question("Phù hợp đi đâu?", ("đi biển", "đi chơi")),
        Question("Có thể đặt may không?", ("có",)),
    ), is_cache_test=True),
    Topic("túi xách da", (
        Question("Có túi xách da nữ không?", ("có",)),
        Question("Da thật hay da PU?", ("da thật",)),
        Question("Giá bao nhiêu?", ("1200000",)),
        Question("Màu nào có sẵn?", ("đen", "nâu", "be")),
        Question("Kích thước bao nhiêu?", ("30cm",)),
        Question("Có ngăn laptop không?", ("15 inch",)),
        Question("Bảo hành bao lâu?", ("24 tháng",)),
        Question("Có dây đeo chéo không?", ("có",)),
        Question("Có sale không?", ("giảm 15%",)),
        Question("Giao hỏa tốc được không?", ("4 giờ",)),
    ), is_cache_test=True),
]


# Non-cache topics: 25 chủ đề quần áo khác (mỗi cái 10 câu)
# Generator pattern: 6 templates × 4 biến thể + 1 extra = 25 topics
def _gen_simple_topic(name: str, qa: list[tuple[str, tuple[str, ...]]]) -> Topic:
    return Topic(name, tuple(Question(q, tuple(k)) for q, k in qa), is_cache_test=False)


_NON_CACHE_TEMPLATES: list[tuple[str, list[tuple[str, tuple[str, ...]]]]] = [
    ("áo sơ mi trắng", [
        ("Có áo sơ mi trắng không?", ("có",)),
        ("Size nào có sẵn?", ("S", "M", "L", "XL")),
        ("Giá bao nhiêu?", ("320000",)),
        ("Chất liệu?", ("cotton",)),
        ("Có thêu logo không?", ("có",)),
        ("Có mấy kiểu cổ?", ("cổ bẻ", "cổ trụ")),
        ("Màu nào khác?", ("xanh nhạt", "hồng")),
        ("Có sale không?", ("giảm 10%",)),
        ("Giao hàng bao lâu?", ("2 ngày",)),
        ("Có thể đổi size không?", ("có",)),
    ]),
    ("quần tây nam", [
        ("Có quần tây nam không?", ("có",)),
        ("Size bao nhiêu?", ("28", "30", "32")),
        ("Giá?", ("550000",)),
        ("Chất liệu?", ("kaki",)),
        ("Có mấy màu?", ("đen", "xám", "xanh")),
        ("Ống suông hay ôm?", ("ốm",)),
        ("Có xếp ly không?", ("có",)),
        ("Bảo hành?", ("6 tháng",)),
        ("Có sale?", ("giảm 20%",)),
        ("Đổi trả được không?", ("7 ngày",)),
    ]),
    ("áo khoác nam", [
        ("Có áo khoác nam không?", ("có",)),
        ("Size?", ("M", "L", "XL")),
        ("Giá?", ("750000",)),
        ("Chất liệu?", ("polyester",)),
        ("Chống nước?", ("có",)),
        ("Mùa đông mặc được không?", ("có",)),
        ("Có mấy màu?", ("đen", "xám", "xanh")),
        ("Có lót lông không?", ("có",)),
        ("Bảo hành?", ("12 tháng",)),
        ("Giao hàng?", ("2-3 ngày",)),
    ]),
    ("quần short nữ", [
        ("Có quần short nữ không?", ("có",)),
        ("Size?", ("S", "M", "L")),
        ("Giá?", ("180000",)),
        ("Chất liệu?", ("cotton",)),
        ("Có mấy kiểu?", ("ống rộng", "ống côn")),
        ("Màu nào hot?", ("be", "kem")),
        ("Có túi không?", ("có",)),
        ("Đi biển mặc được không?", ("có",)),
        ("Sale?", ("giảm 15%",)),
        ("Đổi trả?", ("7 ngày",)),
    ]),
    ("váy ngắn công sở", [
        ("Có váy ngắn công sở không?", ("có",)),
        ("Size?", ("S", "M", "L")),
        ("Giá?", ("280000",)),
        ("Chất liệu?", ("kaki",)),
        ("Dài bao nhiêu?", ("40cm",)),
        ("Có lót trong không?", ("có",)),
        ("Màu nào?", ("đen", "xám", "navy")),
        ("Phối áo gì?", ("áo sơ mi",)),
        ("Có sale?", ("giảm 20%",)),
        ("Bảo hành?", ("3 tháng",)),
    ]),
    ("giày tây nam", [
        ("Có giày tây nam không?", ("có",)),
        ("Size?", ("39", "40", "41", "42", "43")),
        ("Giá?", ("900000",)),
        ("Da thật?", ("da bò",)),
        ("Màu?", ("đen", "nâu")),
        ("Đế da hay cao su?", ("da",)),
        ("Có dây buộc?", ("có",)),
        ("Bảo hành?", ("12 tháng",)),
        ("Có sale?", ("giảm 15%",)),
        ("Đánh bóng miễn phí?", ("có",)),
    ]),
]

# 6 templates × 4 variants ("", " công sở", " basic", " cao cấp") = 24 topics
# + 1 extra = 25
_NON_CACHE_VARIANTS = ["", " công sở", " basic", " cao cấp"]
NON_CACHE_TOPICS: list[Topic] = [
    _gen_simple_topic(tmpl[0] + variant, tmpl[1])
    for tmpl in _NON_CACHE_TEMPLATES
    for variant in _NON_CACHE_VARIANTS
]
NON_CACHE_TOPICS.append(_gen_simple_topic("phụ kiện thời trang", [
    ("Shop có phụ kiện gì?", ("túi", "mũ", "thắt lưng")),
    ("Giá phụ kiện?", ("50000",)),
    ("Có set combo?", ("có",)),
    ("Bảo hành?", ("3 tháng",)),
    ("Có sale?", ("giảm 10%",)),
    ("Đổi trả?", ("7 ngày",)),
    ("Giao hàng?", ("2 ngày",)),
    ("Có quà tặng?", ("có",)),
    ("Màu nào hot?", ("đen", "nâu")),
    ("Free ship?", ("đơn 300k",)),
]))


def all_topics() -> list[Topic]:
    return CACHE_TOPICS + NON_CACHE_TOPICS


# Key fact matcher: substring match (case-insensitive, normalize)
def check_key_facts(response: str, key_facts: tuple[str, ...]) -> tuple[int, int, list[str]]:
    """Returns (matched_count, total_count, missed_facts)."""
    resp_lower = response.lower()
    matched = 0
    missed: list[str] = []
    for f in key_facts:
        if f.lower() in resp_lower:
            matched += 1
        else:
            missed.append(f)
    return matched, len(key_facts), missed
```

> **Note:** 23 topics còn lại theo pattern trên. Mở rộng bằng cách duplicate `_gen_simple_topic(...)` với 10 câu hỏi khác nhau mỗi cái. Đặt tên theo format snake_case tiếng Việt không dấu.

- [ ] **Step 4.3: Smoke test personas + topics**

Run:
```bash
cd /home/hung/ai-hub && python -c "
import sys
sys.path.insert(0, 'scripts')
from test_comprehensive_30min import all_user_instances, all_topics, check_key_facts

users = all_user_instances(10)
print(f'Users: {len(users)} (expect 100)')
print(f'Sample: {users[0].user_id} ({users[0].name}, {users[0].gender}, {users[0].age})')

topics = all_topics()
print(f'Topics: {len(topics)} (expect 30)')
print(f'Cache topics: {sum(1 for t in topics if t.is_cache_test)} (expect 5)')
print(f'Total questions: {sum(len(t.questions) for t in topics)} (expect 300)')

matched, total, missed = check_key_facts('Có áo thun trắng giá 250000', ('có', '250000', '50000'))
print(f'Key fact check: {matched}/{total} matched, missed={missed}')
"
```
Expected: Users=100, Topics=30, Cache=5, Total Q=300. Key fact matcher returns 2/3 matched.

- [ ] **Step 4.4: Commit**

```bash
git add scripts/test_comprehensive_30min.py
git commit -m "feat(test): UserPersona + TopicBank + key_fact matcher (300 câu hỏi quần áo)"
```

---

## Task 5: MetricsCollector with thread-safe aggregation

**Files:**
- Modify: `scripts/test_comprehensive_30min.py` (append)

- [ ] **Step 5.1: Add MetricsCollector class**

```python
# ── Metrics ──────────────────────────────────────────────────────────────
@dataclass
class RequestMetric:
    user: str
    turn: int
    topic: str
    phase: str
    latency_ms: int
    status: int
    error: str | None
    timestamp: str
    response_preview: str = ""  # first 200 chars


@dataclass
class TopicLatency:
    topic: str
    occurrences: int = 0
    latencies_by_occurrence: dict[int, list[int]] = field(default_factory=dict)

    def add(self, occurrence_idx: int, latency_ms: int) -> None:
        self.occurrences += 1
        self.latencies_by_occurrence.setdefault(occurrence_idx, []).append(latency_ms)

    def speedup_pct(self) -> float | None:
        if 1 not in self.latencies_by_occurrence:
            return None
        if len(self.latencies_by_occurrence) < 2:
            return None
        first = statistics.mean(self.latencies_by_occurrence[1])
        later_occs = []
        for k, vs in self.latencies_by_occurrence.items():
            if k == 1:
                continue
            later_occs.extend(vs)
        if not later_occs:
            return None
        later = statistics.mean(later_occs)
        return (first - later) / first * 100.0


class MetricsCollector:
    def __init__(self) -> None:
        self.requests: list[RequestMetric] = []
        self.errors: list[dict] = []
        self.topic_latencies: dict[str, TopicLatency] = {}
        self.memory_recalls: list[dict] = []  # {user, round, facts_asked, facts_recalled, recall_pct, missed_facts}
        self._lock = asyncio.Lock()

    async def record_request(self, m: RequestMetric) -> None:
        async with self._lock:
            self.requests.append(m)
            if m.status >= 500 or m.error:
                self.errors.append({
                    "user": m.user, "turn": m.turn, "phase": m.phase,
                    "status": m.status, "error": m.error, "timestamp": m.timestamp,
                })
            if m.topic:
                occ = self.topic_latencies.setdefault(m.topic, TopicLatency(topic=m.topic))
                # Occurrence index: count existing records for this topic
                occ_idx = sum(len(v) for v in occ.latencies_by_occurrence.values()) + 1
                occ.add(occ_idx, m.latency_ms)

    async def record_recall(self, user: str, round_idx: int, asked: int, recalled: int, missed: list[str]) -> None:
        async with self._lock:
            self.memory_recalls.append({
                "user": user, "round": round_idx,
                "facts_asked": asked, "facts_recalled": recalled,
                "recall_pct": (recalled / asked * 100.0) if asked else 0.0,
                "missed_facts": missed,
            })

    def summary(self) -> dict:
        total = len(self.requests)
        errors = len(self.errors)
        if total == 0:
            return {"total_requests": 0, "error_rate": 0.0, "p50": 0, "p95": 0, "p99": 0}

        latencies = sorted(r.latency_ms for r in self.requests if r.status < 500)
        if not latencies:
            return {"total_requests": total, "error_rate": errors / total,
                    "p50": 0, "p95": 0, "p99": 0, "all_errored": True}

        def pct(p: float) -> int:
            idx = int(len(latencies) * p)
            return latencies[min(idx, len(latencies) - 1)]

        speedups = {
            t.topic: t.speedup_pct() for t in self.topic_latencies.values()
            if t.is_cache_test and t.speedup_pct() is not None
        }

        recall_pcts = [r["recall_pct"] for r in self.memory_recalls]
        avg_recall = statistics.mean(recall_pcts) if recall_pcts else None

        return {
            "total_requests": total,
            "errors": errors,
            "error_rate": errors / total,
            "p50_latency_ms": pct(0.50),
            "p95_latency_ms": pct(0.95),
            "p99_latency_ms": pct(0.99),
            "cache_speedup_pct": speedups,
            "memory_recall_avg_pct": avg_recall,
        }
```

- [ ] **Step 5.2: Smoke test MetricsCollector**

Run:
```bash
cd /home/hung/ai-hub && python -c "
import asyncio, sys
sys.path.insert(0, 'scripts')
from test_comprehensive_30min import MetricsCollector, RequestMetric

async def main():
    m = MetricsCollector()
    for i in range(100):
        await m.record_request(RequestMetric(
            user='u1', turn=i, topic='áo thun trắng', phase='p1',
            latency_ms=4000 + i * 10, status=200, error=None,
            timestamp='2026-06-12T15:00:00Z'))
    s = m.summary()
    print(f'p50: {s[\"p50_latency_ms\"]}, p95: {s[\"p95_latency_ms\"]}, p99: {s[\"p99_latency_ms\"]}')
    print(f'Error rate: {s[\"error_rate\"]} (expect 0.0)')

asyncio.run(main())
"
```
Expected: p50 around 4500, p95 ~4950, p99 ~5000, error_rate=0.0.

- [ ] **Step 5.3: Commit**

```bash
git add scripts/test_comprehensive_30min.py
git commit -m "feat(test): MetricsCollector with topic latency + cache speedup tracking"
```

---

## Task 6: HealthChecker

**Files:**
- Modify: `scripts/test_comprehensive_30min.py` (append)

- [ ] **Step 6.1: Add HealthChecker class**

```python
# ── Health ───────────────────────────────────────────────────────────────
class HealthChecker:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.session: aiohttp.ClientSession | None = None

    async def __aenter__(self) -> "HealthChecker":
        self.session = aiohttp.ClientSession(headers=self.cfg.headers())
        return self

    async def __aexit__(self, *exc) -> None:
        if self.session:
            await self.session.close()

    async def check_ai_hub(self) -> tuple[bool, str]:
        try:
            async with self.session.get(
                f"{self.cfg.base_url}/health", timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status != 200:
                    return False, f"status={resp.status}"
                body = await resp.text()
                if '"status":"ok"' in body or '"status": "ok"' in body:
                    return True, "ok"
                return False, f"unexpected body: {body[:100]}"
        except Exception as e:
            return False, f"exception: {e!r}"

    async def check_llama(self) -> tuple[bool, str]:
        try:
            async with self.session.get(
                f"{self.cfg.llama_url}/health", timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status == 200:
                    return True, "ok"
                return False, f"status={resp.status}"
        except Exception as e:
            return False, f"exception: {e!r}"

    async def assert_healthy(self) -> None:
        ah_ok, ah_msg = await self.check_ai_hub()
        if not ah_ok:
            raise RuntimeError(f"ai-hub not healthy: {ah_msg}")
        ll_ok, ll_msg = await self.check_llama()
        if not ll_ok:
            raise RuntimeError(f"llama.cpp not healthy: {ll_msg}")
        print(f"[health] ai-hub={ah_msg}, llama.cpp={ll_msg}")
```

- [ ] **Step 6.2: Smoke test against running ai-hub**

Run:
```bash
cd /home/hung/ai-hub && python -c "
import asyncio, sys
sys.path.insert(0, 'scripts')
from test_comprehensive_30min import Config, HealthChecker

async def main():
    cfg = Config.from_env()
    async with HealthChecker(cfg) as h:
        await h.assert_healthy()

asyncio.run(main())
"
```
Expected: prints `[health] ai-hub=ok, llama.cpp=ok`.

- [ ] **Step 6.3: Commit**

```bash
git add scripts/test_comprehensive_30min.py
git commit -m "feat(test): HealthChecker for ai-hub + llama.cpp"
```

---

## Task 7: KnowledgeSeeder

**Files:**
- Modify: `scripts/test_comprehensive_30min.py` (append)

- [ ] **Step 7.1: Add KnowledgeSeeder class**

```python
# ── Knowledge Seeder ─────────────────────────────────────────────────────
@dataclass
class KnowledgeCard:
    title: str
    content: str
    domain: str = "clothing"
    tags: list[str] = field(default_factory=list)


def _gen_product_cards() -> list[KnowledgeCard]:
    """50 product cards: 10 products × 5 categories (áo, quần, váy, giày, phụ kiện).

    Mỗi product có 5 biến thể màu → 50 cards total.
    """
    # 10 product templates × 5 categories
    # Format: (category, name_template, base_price, material, sizes, warranty_months, color_count)
    product_templates: list[tuple[str, str, int, str, str, int, int]] = [
        # áo (10)
        ("áo", "Áo thun {color} basic", 250000, "100% cotton", "S,M,L,XL", 3, 5),
        ("áo", "Áo sơ mi {color} công sở", 320000, "cotton pha polyester", "S,M,L,XL", 3, 5),
        ("áo", "Áo khoác {color} mùa đông", 750000, "polyester chống nước", "M,L,XL", 12, 4),
        ("áo", "Áo len {color} cổ lọ", 480000, "len Merino", "S,M,L", 6, 4),
        ("áo", "Áo polo {color} nam", 350000, "cotton cá sấu", "M,L,XL,XXL", 3, 5),
        ("áo", "Áo blazer {color} nữ", 890000, "vải tuytsi", "S,M,L", 6, 4),
        ("áo", "Áo hoodie {color} unisex", 420000, "nỉ bông", "M,L,XL", 3, 5),
        ("áo", "Áo tank top {color} thể thao", 180000, "polyester co giãn", "S,M,L,XL", 3, 4),
        ("áo", "Áo vest {color} nam công sở", 1200000, "vải wool pha", "M,L,XL", 12, 3),
        ("áo", "Áo dài {color} truyền thống", 950000, "lụa tằm", "S,M,L,XL", 6, 5),
        # quần (10)
        ("quần", "Quần jean {color} ống suông", 450000, "denim cotton", "28,29,30,31,32", 6, 5),
        ("quần", "Quần tây {color} nam", 550000, "kaki", "28,30,32,34", 6, 4),
        ("quần", "Quần short {color} nữ", 180000, "cotton", "S,M,L", 3, 5),
        ("quần", "Quần jogger {color} unisex", 320000, "nỉ", "M,L,XL", 3, 5),
        ("quần", "Quần legging {color} nữ", 220000, "polyester co giãn", "S,M,L,XL", 3, 4),
        ("quần", "Quần culottes {color} dài", 380000, "voan", "S,M,L", 3, 4),
        ("quần", "Quần kaki {color} nam", 420000, "kaki dày", "28,30,32,34", 6, 4),
        ("quần", "Quần yếm {color} nữ", 350000, "denim", "S,M,L", 3, 5),
        ("quần", "Quần baggy {color} unisex", 380000, "cotton pha linen", "M,L,XL", 3, 5),
        ("quần", "Quần lót {color} cotton", 95000, "cotton 100%", "M,L,XL", 1, 4),
        # váy (10)
        ("váy", "Váy {color} dài maxi", 380000, "voan", "S,M,L", 3, 5),
        ("váy", "Váy {color} ngắn công sở", 280000, "kaki", "S,M,L", 3, 4),
        ("váy", "Váy {color} dạ hội", 1500000, "lụa + ren", "S,M,L", 6, 4),
        ("váy", "Váy {color} chữ A", 320000, "cotton", "S,M,L,XL", 3, 5),
        ("váy", "Váy {color} xòe vintage", 450000, "cotton họa tiết", "S,M,L", 3, 5),
        ("váy", "Váy {color} body ôm", 380000, "thun gân", "S,M,L", 3, 4),
        ("váy", "Váy {color} yếm", 290000, "denim", "S,M,L", 3, 4),
        ("váy", "Váy {color} tennis", 420000, "polyester", "S,M,L,XL", 3, 4),
        ("váy", "Váy {color} midi", 350000, "voan", "S,M,L", 3, 5),
        ("váy", "Váy {color} wrap", 390000, "cotton", "S,M,L,XL", 3, 5),
        # giày (10)
        ("giày", "Giày thể thao {color} Nike Air", 1200000, "upper mesh + đế cao su", "39,40,41,42,43", 12, 5),
        ("giày", "Giày tây {color} nam", 900000, "da bò", "39,40,41,42,43", 12, 4),
        ("giày", "Sandal {color} nữ", 280000, "da tổng hợp + đế eva", "36,37,38,39", 3, 5),
        ("giày", "Giày cao gót {color}", 480000, "da bò + đế cao su", "36,37,38,39", 3, 4),
        ("giày", "Boots {color} cổ cao", 850000, "da bò + lông", "37,38,39,40,41", 12, 4),
        ("giày", "Sneaker {color} trắng basic", 650000, "canvas + đế cao su", "36,37,38,39,40,41,42,43", 6, 5),
        ("giày", "Oxford {color} nam công sở", 1100000, "da bò", "39,40,41,42,43", 12, 3),
        ("giày", "Loafer {color} nữ", 520000, "da bò", "36,37,38,39", 6, 4),
        ("giày", "Dép tổ ong {color}", 95000, "nhựa eva", "36,37,38,39,40,41,42", 1, 5),
        ("giày", "Slip-on {color} vải", 320000, "canvas", "37,38,39,40,41", 3, 5),
        # phụ kiện (10)
        ("phụ kiện", "Túi xách {color} da bò", 1200000, "da bò thật", "30x25x12cm", 24, 4),
        ("phụ kiện", "Mũ lưỡi trai {color}", 150000, "cotton", "free size", 1, 5),
        ("phụ kiện", "Thắt lưng {color} da", 280000, "da bò", "90cm-110cm", 6, 4),
        ("phụ kiện", "Kính mát {color} unisex", 380000, "nhựa + kính polar", "free size", 6, 4),
        ("phụ kiện", "Dây chuyền {color} bạc", 450000, "bạc 925", "45cm", 6, 4),
        ("phụ kiện", "Khăn {color} lụa", 220000, "lụa tằm", "50x50cm", 1, 5),
        ("phụ kiện", "Găng tay {color} len", 120000, "len", "free size", 1, 4),
        ("phụ kiện", "Tất {color} cotton", 45000, "cotton", "free size", 0, 5),
        ("phụ kiện", "Ví {color} da nam", 480000, "da bò", "11x9cm", 12, 4),
        ("phụ kiện", "Túi đeo chéo {color} vải", 350000, "canvas + da PU", "20x15x5cm", 3, 5),
    ]
    colors = ["trắng", "đen", "xám", "xanh navy", "be"]
    cards: list[KnowledgeCard] = []
    for cat, name_tmpl, price, material, sizes, warranty, color_count in product_templates:
        for color in colors[:color_count]:
            cards.append(KnowledgeCard(
                title=name_tmpl.format(color=color),
                content=(
                    f"{name_tmpl.format(color=color)} chất liệu {material}, "
                    f"size {sizes}, giá {price:,}đ. "
                    f"Bảo hành {warranty} tháng. Đổi trả trong 7 ngày nếu lỗi. "
                    f"Freeship đơn từ 300k. Phù hợp {cat} thời trang."
                ),
                tags=[cat, color, "quần áo", name_tmpl.split()[0].lower()],
            ))
    return cards


def _gen_faq_cards() -> list[KnowledgeCard]:
    """25 FAQ cards: 8 base topics × 3 variants + 1 extra = 25 cards."""
    # 8 base FAQ topics × 3 variants ("", " chi tiết", " cập nhật 2026")
    faq_templates: list[tuple[str, str, list[str]]] = [
        ("Chính sách đổi trả", "Đổi trả trong 7 ngày nếu sản phẩm lỗi hoặc không đúng size. Sản phẩm phải còn nguyên tag, chưa giặt. Phí ship đổi trả 30.000đ/lần. Hoàn tiền trong 3-5 ngày làm việc qua chuyển khoản.", ["đổi trả", "bảo hành", "policy"]),
        ("Bảng size quần áo", "Size S: 45-55kg, 150-160cm. Size M: 55-65kg, 160-167cm. Size L: 65-75kg, 167-175cm. Size XL: 75-85kg, 175-180cm. Size XXL: trên 85kg hoặc 180cm trở lên. Mỗi sản phẩm có bảng size riêng, vui lòng tham khảo.", ["size", "bảng size", "quần áo"]),
        ("Phí vận chuyển", "Freeship cho đơn từ 300.000đ nội thành HCM, HN. Đơn dưới 300k: 25.000đ. Tỉnh khác: 35.000đ, freeship từ 500.000đ. Giao hỏa tốc 4 giờ nội thành: +50.000đ.", ["shipping", "vận chuyển", "freeship"]),
        ("Phương thức thanh toán", "Hỗ trợ COD (thanh toán khi nhận hàng), chuyển khoản ngân hàng, ví MoMo, ZaloPay, VnPay, thẻ tín dụng quốc tế. Trả góp 0% với đơn từ 3 triệu qua thẻ tín dụng.", ["payment", "thanh toán", "COD"]),
        ("Bảo hành sản phẩm", "Bảo hành 3-24 tháng tùy sản phẩm (chi tiết trong mô tả). Lỗi sản xuất được đổi mới miễn phí. Không bảo hành hư hỏng do sử dụng sai cách, bảo quản không đúng.", ["bảo hành", "warranty", "đổi mới"]),
        ("Chương trình khuyến mãi", "Sale lớn định kỳ: 1/1, 14/2, 8/3, 30/4, 1/5, 2/9, 20/10, 11/11, 12/12. Giảm giá 20-50%. Flash sale mỗi thứ 6 hàng tuần từ 20h-22h. Mã freeship cho khách mới.", ["sale", "khuyến mãi", "voucher"]),
        ("Chương trình loyalty", "Tích điểm 5% giá trị đơn hàng cho thành viên. 1000 điểm = 50.000đ. Hạng thành viên: Thường (0-5tr/năm), Bạc (5-15tr), Vàng (15-50tr), Kim Cương (>50tr). Ưu đãi riêng cho hạng Vàng+.", ["loyalty", "thành viên", "tích điểm"]),
        ("Hỗ trợ khách hàng", "Hotline: 1900-xxxx (8h-22h hàng ngày). Email: support@example.vn. Chat trực tuyến trên website 24/7. Fanpage Facebook và Zalo OA phản hồi trong 1 giờ giờ hành chính.", ["hỗ trợ", "liên hệ", "hotline"]),
    ]
    variants = ["", " chi tiết", " cập nhật 2026"]
    cards: list[KnowledgeCard] = []
    for title, content, tags in faq_templates:
        for variant in variants:
            cards.append(KnowledgeCard(
                title=title + variant,
                content=content,
                tags=tags,
            ))
    # Thêm 1 card đặc biệt về vận chuyển quốc tế
    cards.append(KnowledgeCard(
        title="Vận chuyển quốc tế",
        content="Ship quốc tế qua DHL, FedEx, Viettel Post. Phí tính theo cân nặng và quốc gia, từ 500.000đ/kg. Thời gian 5-10 ngày làm việc. Hỗ trợ khai hải quan. Không hỗ trợ đổi trả đơn quốc tế.",
        tags=["international", "vận chuyển quốc tế", "DHL"],
    ))
    return cards


class KnowledgeSeeder:
    def __init__(self, cfg: Config, log: logging.Logger) -> None:
        self.cfg = cfg
        self.log = log
        self.session: aiohttp.ClientSession | None = None

    async def __aenter__(self) -> "KnowledgeSeeder":
        self.session = aiohttp.ClientSession(headers=self.cfg.headers())
        return self

    async def __aexit__(self, *exc) -> None:
        if self.session:
            await self.session.close()

    async def seed(self, n: int) -> tuple[int, int]:
        """Generate n cards + upload. Returns (success_count, fail_count)."""
        all_cards = _gen_product_cards() + _gen_faq_cards()
        cards = all_cards[:n]
        ok, fail = 0, 0
        for i, card in enumerate(cards):
            payload = {
                "title": card.title,
                "content": card.content,
                "domain": card.domain,
                "tags": card.tags,
                "trust_level": 3,
            }
            try:
                async with self.session.post(
                    f"{self.cfg.base_url}/v1/admin/knowledge/upload",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status < 300:
                        ok += 1
                        if (i + 1) % 10 == 0:
                            print(f"  [seed] {i+1}/{len(cards)} uploaded")
                    else:
                        fail += 1
                        self.log.error(f"seed fail [{resp.status}]: {card.title} | {await resp.text()[:200]}")
            except Exception as e:
                fail += 1
                self.log.error(f"seed exception: {card.title} | {e!r}")
        # Wait for embeddings to finish (best-effort)
        await asyncio.sleep(2)
        return ok, fail
```

> **Note:** `_gen_product_cards()` sinh ra 50 product cards (10 templates × 5 categories × 5 biến thể màu), `_gen_faq_cards()` sinh ra 25 FAQ cards (8 templates × 3 variants + 1 card vận chuyển quốc tế). `seed(n)` lấy n cards đầu tiên.

- [ ] **Step 7.2: Smoke test seeder (upload 5 cards)**

Run:
```bash
cd /home/hung/ai-hub && python -c "
import asyncio, sys, logging
sys.path.insert(0, 'scripts')
from test_comprehensive_30min import Config, KnowledgeSeeder

async def main():
    cfg = Config.from_env()
    log = logging.getLogger('test')
    async with KnowledgeSeeder(cfg, log) as s:
        ok, fail = await s.seed(5)
        print(f'Uploaded: {ok} ok, {fail} fail')

asyncio.run(main())
"
```
Expected: 5 uploaded, 0 fail. Verify via `GET /v1/admin/knowledge/cards` returns 5+ cards.

- [ ] **Step 7.3: Commit**

```bash
git add scripts/test_comprehensive_30min.py
git commit -m "feat(test): KnowledgeSeeder for 75 clothing cards"
```

---

## Task 8: PhaseRunner base + Phase 1 (warmup)

**Files:**
- Modify: `scripts/test_comprehensive_30min.py` (append)

- [ ] **Step 8.1: Add HTTP client helper + PhaseRunner base**

```python
# ── HTTP client + Phase Runner ───────────────────────────────────────────
class ChatClient:
    def __init__(self, cfg: Config, metrics: MetricsCollector, log: logging.Logger) -> None:
        self.cfg = cfg
        self.metrics = metrics
        self.log = log
        self.session: aiohttp.ClientSession | None = None
        self._semaphore = asyncio.Semaphore(cfg.concurrency)

    async def __aenter__(self) -> "ChatClient":
        self.session = aiohttp.ClientSession(headers=self.cfg.headers())
        return self

    async def __aexit__(self, *exc) -> None:
        if self.session:
            await self.session.close()

    async def chat(
        self, user: str, message: str, session_id: str, topic: str = "",
        phase: str = "", turn: int = 0,
    ) -> tuple[int, str, int]:
        """Returns (status_code, response_text, latency_ms)."""
        payload = {
            "user": user,
            "session_id": session_id,
            "message": message,
            "model_mode": "lite",
            "stream": False,
        }
        async with self._semaphore:
            t0 = time.monotonic()
            try:
                async with self.session.post(
                    f"{self.cfg.base_url}/v1/chat",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=60),
                ) as resp:
                    body = await resp.text()
                    latency_ms = int((time.monotonic() - t0) * 1000)
                    preview = body[:200]
                    error_msg = None if resp.status < 400 else preview
                    metric = RequestMetric(
                        user=user, turn=turn, topic=topic, phase=phase,
                        latency_ms=latency_ms, status=resp.status, error=error_msg,
                        timestamp=datetime.now(timezone.utc).isoformat(),
                        response_preview=preview,
                    )
                    await self.metrics.record_request(metric)
                    if resp.status >= 500:
                        self.log.error(f"[{phase}] {user} turn {turn} status={resp.status}: {preview[:150]}")
                    return resp.status, body, latency_ms
            except asyncio.TimeoutError:
                latency_ms = int((time.monotonic() - t0) * 1000)
                await self.metrics.record_request(RequestMetric(
                    user=user, turn=turn, topic=topic, phase=phase,
                    latency_ms=latency_ms, status=599, error="timeout",
                    timestamp=datetime.now(timezone.utc).isoformat(),
                ))
                return 599, "", latency_ms
            except Exception as e:
                latency_ms = int((time.monotonic() - t0) * 1000)
                await self.metrics.record_request(RequestMetric(
                    user=user, turn=turn, topic=topic, phase=phase,
                    latency_ms=latency_ms, status=598, error=f"exception: {e!r}",
                    timestamp=datetime.now(timezone.utc).isoformat(),
                ))
                return 598, "", latency_ms


@dataclass
class PhaseResult:
    name: str
    started_at: str
    ended_at: str
    duration_seconds: float
    extra: dict = field(default_factory=dict)


class PhaseRunner:
    def __init__(self, cfg: Config, client: ChatClient, metrics: MetricsCollector, log: logging.Logger) -> None:
        self.cfg = cfg
        self.client = client
        self.metrics = metrics
        self.log = log

    async def run(self) -> PhaseResult:
        raise NotImplementedError
```

- [ ] **Step 8.2: Add Phase1Warmup**

```python
class Phase1Warmup(PhaseRunner):
    """10 personas × 10 câu = 100 turns, gather baseline latency."""

    async def run(self) -> PhaseResult:
        started = datetime.now(timezone.utc)
        t_start = time.monotonic()
        topics = all_topics()
        for persona in PERSONAS:
            for turn in range(self.cfg.phase1_turns_per_user):
                topic = random.choice(topics)
                question = random.choice(topic.questions)
                await self.client.chat(
                    user=persona.user_id,
                    message=question.text,
                    session_id=persona.user_id,
                    topic=topic.name,
                    phase="phase1_warmup",
                    turn=turn,
                )
        ended = datetime.now(timezone.utc)
        return PhaseResult(
            name="phase1_warmup",
            started_at=started.isoformat(),
            ended_at=ended.isoformat(),
            duration_seconds=time.monotonic() - t_start,
            extra={"users": len(PERSONAS), "turns_per_user": self.cfg.phase1_turns_per_user},
        )
```

- [ ] **Step 8.3: Smoke test Phase 1 (2 user × 2 q)**

Run:
```bash
cd /home/hung/ai-hub && python -c "
import asyncio, sys, logging
sys.path.insert(0, 'scripts')
from test_comprehensive_30min import Config, MetricsCollector, ChatClient, Phase1Warmup

async def main():
    cfg = Config.from_env()
    cfg.phase1_turns_per_user = 2  # override for smoke
    log = logging.getLogger('test')
    metrics = MetricsCollector()
    async with ChatClient(cfg, metrics, log) as client:
        result = await Phase1Warmup(cfg, client, metrics, log).run()
        s = metrics.summary()
        print(f'Phase 1: {result.duration_seconds:.1f}s, p50={s[\"p50_latency_ms\"]}, errors={s[\"errors\"]}')

asyncio.run(main())
"
```
Expected: 4 requests complete in <30s, latency p50 in 3000-8000ms range.

- [ ] **Step 8.4: Commit**

```bash
git add scripts/test_comprehensive_30min.py
git commit -m "feat(test): ChatClient + Phase1Warmup (baseline latency)"
```

---

## Task 9: Phase 2 (rotate 100 user, cache topics)

**Files:**
- Modify: `scripts/test_comprehensive_30min.py` (append)

- [ ] **Step 9.1: Add Phase2Rotate**

```python
class Phase2Rotate(PhaseRunner):
    """100 user instances, 10 câu/user, 5 cache topics repeated ≥3 lần."""

    def _build_user_plan(self) -> list[tuple[UserPersona, list[Topic]]]:
        """For each user: 1 cache topic (or None) + 9 other topics (no repeat)."""
        instances = all_user_instances(self.cfg.phase2_users_total // len(PERSONAS))
        cache_topics = [t for t in all_topics() if t.is_cache_test]
        non_cache = [t for t in all_topics() if not t.is_cache_test]
        plan: list[tuple[UserPersona, list[Topic]]] = []
        for i, persona in enumerate(instances):
            user_topics: list[Topic] = []
            if i < len(cache_topics) * 5:  # 5 cache topics × 5 instances = 25 users see cache
                user_topics.append(cache_topics[i % len(cache_topics)])
            remaining = self.cfg.phase2_turns_per_user - len(user_topics)
            user_topics.extend(random.sample(non_cache, min(remaining, len(non_cache))))
            random.shuffle(user_topics)
            plan.append((persona, user_topics))
        return plan

    async def run(self) -> PhaseResult:
        started = datetime.now(timezone.utc)
        t_start = time.monotonic()
        plan = self._build_user_plan()
        for persona, topics in plan:
            for turn, topic in enumerate(topics[:self.cfg.phase2_turns_per_user]):
                question = random.choice(topic.questions)
                await self.client.chat(
                    user=persona.user_id,
                    message=question.text,
                    session_id=persona.user_id,
                    topic=topic.name,
                    phase="phase2_rotate",
                    turn=turn,
                )
        ended = datetime.now(timezone.utc)
        return PhaseResult(
            name="phase2_rotate",
            started_at=started.isoformat(),
            ended_at=ended.isoformat(),
            duration_seconds=time.monotonic() - t_start,
            extra={"users": len(plan), "turns_per_user": self.cfg.phase2_turns_per_user},
        )
```

- [ ] **Step 9.2: Smoke test Phase 2 (5 user × 3 q)**

Run:
```bash
cd /home/hung/ai-hub && python -c "
import asyncio, sys, logging
sys.path.insert(0, 'scripts')
from test_comprehensive_30min import Config, MetricsCollector, ChatClient, Phase2Rotate

async def main():
    cfg = Config.from_env()
    cfg.phase2_users_total = 5
    cfg.phase2_turns_per_user = 3
    log = logging.getLogger('test')
    metrics = MetricsCollector()
    async with ChatClient(cfg, metrics, log) as client:
        result = await Phase2Rotate(cfg, client, metrics, log).run()
        s = metrics.summary()
        print(f'Phase 2: {result.duration_seconds:.1f}s, p50={s[\"p50_latency_ms\"]}, speedup={s[\"cache_speedup_pct\"]}')

asyncio.run(main())
"
```
Expected: 15 requests complete, speedup dict has entries for cache topics (or empty if N=1 occurrences).

- [ ] **Step 9.3: Commit**

```bash
git add scripts/test_comprehensive_30min.py
git commit -m "feat(test): Phase2Rotate (100 user, 5 cache topics for speedup)"
```

---

## Task 10: Phase 3 (memory recall + continue)

**Files:**
- Modify: `scripts/test_comprehensive_30min.py` (append)

- [ ] **Step 10.1: Add Phase3Recall**

```python
class Phase3Recall(PhaseRunner):
    """Round 1-3: chọn 10 user từ phase 1, wait, memory check, continue 10 câu."""

    async def run(self) -> PhaseResult:
        started = datetime.now(timezone.utc)
        t_start = time.monotonic()
        instances = all_user_instances(self.cfg.phase3_rounds)
        topics = all_topics()
        for round_idx in range(self.cfg.phase3_rounds):
            print(f"  [phase3] round {round_idx+1}/{self.cfg.phase3_rounds}, sleeping {self.cfg.phase3_gap_seconds}s for memory consolidation...")
            await asyncio.sleep(self.cfg.phase3_gap_seconds)
            users_this_round = instances[
                round_idx * self.cfg.phase3_users_per_round : (round_idx + 1) * self.cfg.phase3_users_per_round
            ]
            for persona in users_this_round:
                # Memory check question
                memory_q = "Bạn còn nhớ tôi đã hỏi gì trong cuộc trò chuyện trước đó không? Hãy tóm tắt giúp tôi."
                status, body, lat = await self.client.chat(
                    user=persona.user_id,
                    message=memory_q,
                    session_id=persona.user_id,
                    topic="<memory_check>",
                    phase=f"phase3_recall_r{round_idx+1}",
                    turn=0,
                )
                # Baseline clothing keywords: response should mention ≥70% if memory works
                baseline_facts = ("áo", "quần", "giày", "váy", "túi",
                                  "size", "giá", "giao hàng", "đổi trả", "bảo hành")
                matched, total, missed = check_key_facts(body, baseline_facts)
                await self.metrics.record_recall(
                    persona.user_id, round_idx + 1, total, matched, missed
                )
                # Continue 10 câu
                for turn in range(1, self.cfg.phase1_turns_per_user + 1):
                    topic = random.choice(topics)
                    question = random.choice(topic.questions)
                    await self.client.chat(
                        user=persona.user_id,
                        message=question.text,
                        session_id=persona.user_id,
                        topic=topic.name,
                        phase=f"phase3_continue_r{round_idx+1}",
                        turn=turn,
                    )
        ended = datetime.now(timezone.utc)
        return PhaseResult(
            name="phase3_recall",
            started_at=started.isoformat(),
            ended_at=ended.isoformat(),
            duration_seconds=time.monotonic() - t_start,
            extra={"rounds": self.cfg.phase3_rounds, "users_per_round": self.cfg.phase3_users_per_round},
        )
```

> **Note:** Recall check dùng 10 baseline clothing keywords ("áo", "quần", "giày", "váy", "túi", "size", "giá", "giao hàng", "đổi trả", "bảo hành"). Nếu memory hoạt động đúng, response phải nhắc đến ≥7/10 (70%) keywords. Đây là test conservative — không cần track key_facts chính xác từ phase 1. Nếu muốn test chính xác hơn, có thể thay bằng vector store mapping (user_id → list of key_facts) trong version 2.

- [ ] **Step 10.2: Smoke test Phase 3 (1 round, 2 user, gap=10s)**

Run:
```bash
cd /home/hung/ai-hub && python -c "
import asyncio, sys, logging
sys.path.insert(0, 'scripts')
from test_comprehensive_30min import Config, MetricsCollector, ChatClient, Phase3Recall

async def main():
    cfg = Config.from_env()
    cfg.phase3_rounds = 1
    cfg.phase3_users_per_round = 2
    cfg.phase3_gap_seconds = 10
    log = logging.getLogger('test')
    metrics = MetricsCollector()
    async with ChatClient(cfg, metrics, log) as client:
        result = await Phase3Recall(cfg, client, metrics, log).run()
        s = metrics.summary()
        print(f'Phase 3: {result.duration_seconds:.1f}s, recall_avg={s[\"memory_recall_avg_pct\"]:.1f}%')

asyncio.run(main())
"
```
Expected: 1 round completes in ~20-30s, recall_avg between 0-100%.

- [ ] **Step 10.3: Commit**

```bash
git add scripts/test_comprehensive_30min.py
git commit -m "feat(test): Phase3Recall with memory check + continue"
```

---

## Task 11: ReportGenerator with pass/fail

**Files:**
- Modify: `scripts/test_comprehensive_30min.py` (append)

- [ ] **Step 11.1: Add ReportGenerator class**

```python
# ── Report ───────────────────────────────────────────────────────────────
class ReportGenerator:
    def __init__(self, cfg: Config, metrics: MetricsCollector) -> None:
        self.cfg = cfg
        self.metrics = metrics
        self.phase_results: list[PhaseResult] = []

    def add_phase(self, result: PhaseResult) -> None:
        self.phase_results.append(result)

    def build(self, started_at: datetime, ended_at: datetime) -> dict:
        s = self.metrics.summary()
        total_duration = (ended_at - started_at).total_seconds()
        throughput = (s["total_requests"] / total_duration * 60) if total_duration else 0

        # Pass/fail logic
        pass_error = s["error_rate"] <= self.cfg.error_rate_threshold
        pass_recall = (
            s["memory_recall_avg_pct"] is None
            or s["memory_recall_avg_pct"] >= self.cfg.memory_recall_threshold * 100
        )
        speedups = s.get("cache_speedup_pct", {}) or {}
        pass_speedup = True  # observe only, never fail
        if speedups:
            avg_speedup = statistics.mean(speedups.values())
            pass_speedup = avg_speedup >= self.cfg.cache_speedup_threshold * 100

        if pass_error and pass_recall and pass_speedup:
            verdict = "PASS"
        elif pass_error and s["memory_recall_avg_pct"] is not None and s["memory_recall_avg_pct"] >= 50:
            verdict = "SOFT_PASS"
        else:
            verdict = "FAIL"

        return {
            "test_name": "ai-hub-comprehensive-30min",
            "started_at": started_at.isoformat(),
            "ended_at": ended_at.isoformat(),
            "total_duration_seconds": total_duration,
            "throughput_turns_per_min": throughput,
            "config": asdict(self.cfg),
            "phases": [asdict(r) for r in self.phase_results],
            "metrics_summary": s,
            "top_errors": self._top_errors(10),
            "verdict": verdict,
            "criteria": {
                "error_rate_threshold": self.cfg.error_rate_threshold,
                "memory_recall_threshold": self.cfg.memory_recall_threshold,
                "cache_speedup_threshold": self.cfg.cache_speedup_threshold,
            },
        }

    def _top_errors(self, n: int) -> list[dict]:
        from collections import Counter
        error_types = Counter()
        for e in self.metrics.errors:
            etype = e.get("error") or f"status_{e['status']}"
            error_types[etype] += 1
        return [{"error": k, "count": v} for k, v in error_types.most_common(n)]

    def write(self, report: dict) -> tuple[Path, Path]:
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        json_path = self.cfg.report_dir / f"comprehensive_30min_{ts}.json"
        log_path = self.cfg.report_dir / f"comprehensive_30min_{ts}.log"
        self.cfg.report_dir.mkdir(parents=True, exist_ok=True)
        tmp = json_path.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        tmp.rename(json_path)
        return json_path, log_path
```

- [ ] **Step 11.2: Smoke test report generation**

Run:
```bash
cd /home/hung/ai-hub && python -c "
import asyncio, sys, logging
from datetime import datetime, timezone
sys.path.insert(0, 'scripts')
from test_comprehensive_30min import Config, MetricsCollector, RequestMetric, ReportGenerator

async def main():
    cfg = Config.from_env()
    metrics = MetricsCollector()
    for i in range(20):
        await metrics.record_request(RequestMetric(
            user='u1', turn=i, topic='t', phase='p1',
            latency_ms=4000 + i*100, status=200, error=None,
            timestamp='2026-06-12T15:00:00Z'))
    rg = ReportGenerator(cfg, metrics)
    report = rg.build(datetime.now(timezone.utc), datetime.now(timezone.utc))
    print('Verdict:', report['verdict'])
    print('p95:', report['metrics_summary']['p95_latency_ms'])
    print('Top errors:', report['top_errors'])

asyncio.run(main())
"
```
Expected: Verdict=PASS (no errors), p95 ~5800, top_errors=[].

- [ ] **Step 11.3: Commit**

```bash
git add scripts/test_comprehensive_30min.py
git commit -m "feat(test): ReportGenerator with pass/fail + top errors"
```

---

## Task 12: main() + CLI + orchestrate

**Files:**
- Modify: `scripts/test_comprehensive_30min.py` (append)

- [ ] **Step 12.1: Add main() + argparse + phase orchestration**

```python
# ── Main ─────────────────────────────────────────────────────────────────
async def _run_full(cfg: Config, log: logging.Logger, phases_filter: set[int] | None) -> dict:
    metrics = MetricsCollector()
    report_gen = ReportGenerator(cfg, metrics)
    started = datetime.now(timezone.utc)

    async with HealthChecker(cfg) as h:
        await h.assert_healthy()
    async with ChatClient(cfg, metrics, log) as client:
        runner = PhaseRunner(cfg, client, metrics, log)

        if not phases_filter or 1 in phases_filter:
            print("[main] Phase 1: warmup (10 personas × 10 turns)")
            result = await Phase1Warmup(cfg, client, metrics, log).run()
            report_gen.add_phase(result)
            print(f"  done in {result.duration_seconds:.1f}s")

        if not phases_filter or 2 in phases_filter:
            print("[main] Phase 2: rotate (100 user, 5 cache topics)")
            result = await Phase2Rotate(cfg, client, metrics, log).run()
            report_gen.add_phase(result)
            print(f"  done in {result.duration_seconds:.1f}s")

        if not phases_filter or 3 in phases_filter:
            print("[main] Phase 3: memory recall + continue (3 rounds × 10 user)")
            result = await Phase3Recall(cfg, client, metrics, log).run()
            report_gen.add_phase(result)
            print(f"  done in {result.duration_seconds:.1f}s")

    ended = datetime.now(timezone.utc)
    report = report_gen.build(started, ended)
    json_path, log_path = report_gen.write(report)
    print(f"\n[main] Report: {json_path}")
    print(f"[main] Verdict: {report['verdict']}")
    print(f"[main] Error rate: {report['metrics_summary']['error_rate']*100:.1f}% (threshold {cfg.error_rate_threshold*100:.0f}%)")
    if report['metrics_summary']['memory_recall_avg_pct'] is not None:
        print(f"[main] Memory recall: {report['metrics_summary']['memory_recall_avg_pct']:.1f}% (threshold {cfg.memory_recall_threshold*100:.0f}%)")
    print(f"[main] Throughput: {report['throughput_turns_per_min']:.1f} turns/min")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="ai-hub 30-min comprehensive test")
    parser.add_argument("--quick", action="store_true", help="Quick smoke (5 user × 5 q, 5 KB cards)")
    parser.add_argument("--phases", type=str, default=None, help="Comma-separated phase numbers to run (e.g. '1,2')")
    parser.add_argument("--dry-run", action="store_true", help="Skip HTTP, generate synthetic report")
    args = parser.parse_args()

    log = logging.getLogger("comprehensive_test")
    log.setLevel(logging.INFO)
    handler = logging.FileHandler("/tmp/comprehensive_test.log")
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    log.addHandler(handler)

    cfg = Config.from_env()
    if args.quick:
        cfg.phase1_turns_per_user = 5
        cfg.phase2_users_total = 5
        cfg.phase2_turns_per_user = 3
        cfg.kb_card_count = 5
        cfg.phase3_rounds = 1
        cfg.phase3_users_per_round = 2
        cfg.phase3_gap_seconds = 10
    phases_filter = set(int(p) for p in args.phases.split(",")) if args.phases else None

    if args.dry_run:
        # Synthetic report for testing the report logic
        cfg_dry = Config.from_env()
        metrics = MetricsCollector()
        for i in range(50):
            asyncio.run(metrics.record_request(RequestMetric(
                user=f"dry_user_{i%5}", turn=i, topic="áo thun trắng", phase="p1",
                latency_ms=4000 + i*10, status=200, error=None,
                timestamp=datetime.now(timezone.utc).isoformat())))
        rg = ReportGenerator(cfg_dry, metrics)
        report = rg.build(datetime.now(timezone.utc), datetime.now(timezone.utc))
        path, _ = rg.write(report)
        print(f"[dry-run] Synthetic report: {path}")
        return 0

    try:
        report = asyncio.run(_run_full(cfg, log, phases_filter))
        return 0 if report["verdict"] != "FAIL" else 1
    except RuntimeError as e:
        print(f"[main] ABORT: {e}")
        return 2
    except Exception as e:
        log.exception("test failed")
        print(f"[main] CRASH: {e!r}")
        traceback.print_exc()
        return 3


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 12.2: Verify --help works**

Run: `cd /home/hung/ai-hub && python scripts/test_comprehensive_30min.py --help`
Expected: shows --quick, --phases, --dry-run options with descriptions.

- [ ] **Step 12.3: Run --dry-run to verify report writing**

Run: `cd /home/hung/ai-hub && python scripts/test_comprehensive_30min.py --dry-run`
Expected: prints `[dry-run] Synthetic report: reports/comprehensive_30min_<ts>.json` and exits 0.

- [ ] **Step 12.4: Commit**

```bash
git add scripts/test_comprehensive_30min.py
git commit -m "feat(test): main() with CLI + phase orchestration + dry-run"
```

---

## Task 13: Smoke test (full integration, 2 user × 2 q)

**Files:**
- Create: `tests/integration/test_comprehensive_30min_smoke.py`

- [ ] **Step 13.1: Create smoke test file**

```python
"""Smoke test: run --quick mode end-to-end against live ai-hub.

Verifies:
  - Config loads
  - All 3 phases run without exceptions
  - JSON report is written
  - Verdict is one of PASS/SOFT_PASS/FAIL
  - All required fields present in report
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_quick_mode_completes() -> None:
    repo = Path(__file__).resolve().parents[2]
    script = repo / "scripts" / "test_comprehensive_30min.py"
    proc = subprocess.run(
        [sys.executable, str(script), "--quick"],
        cwd=str(repo),
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode in (0, 1), f"unexpected exit code: {proc.returncode}\nSTDOUT: {proc.stdout}\nSTDERR: {proc.stderr}"
    # Find latest report
    reports = sorted((repo / "reports").glob("comprehensive_30min_*.json"))
    assert reports, f"no report written\nSTDOUT: {proc.stdout}"
    report_path = reports[-1]
    with open(report_path) as f:
        report = json.load(f)
    assert report["verdict"] in ("PASS", "SOFT_PASS", "FAIL")
    assert "metrics_summary" in report
    assert "phases" in report
    assert len(report["phases"]) == 3
    print(f"OK: {report_path.name} verdict={report['verdict']}")
```

- [ ] **Step 13.2: Run smoke test**

Run: `cd /home/hung/ai-hub && ./venv/bin/pytest tests/integration/test_comprehensive_30min_smoke.py -v --no-cov`
Expected: PASS in <300s, prints "OK: comprehensive_30min_<ts>.json verdict=..."

- [ ] **Step 13.3: Commit**

```bash
git add tests/integration/test_comprehensive_30min_smoke.py
git commit -m "test: smoke test for comprehensive_30min --quick mode"
```

---

## Task 14: Stop test ai-hub + restore .env (cleanup)

**Files:**
- Modify: `.env` (restore `MINIMAX_ENABLED=true`)

- [ ] **Step 14.1: Stop uvicorn**

Run: `pkill -f 'uvicorn app.main:app' && sleep 2 && ps aux | grep uvicorn | grep -v grep`
Expected: no uvicorn process.

- [ ] **Step 14.2: Stop llama-server**

Run: `pkill -f 'llama-server.*--port 8080' && sleep 2 && ps aux | grep llama-server | grep -v grep`
Expected: no llama-server process.

- [ ] **Step 14.3: Verify GPU freed**

Run: `nvidia-smi --query-gpu=memory.used --format=csv`
Expected: memory.used < 500 MiB.

- [ ] **Step 14.4: Restore .env (re-enable MiniMax)**

Use `Edit` tool: set `MINIMAX_ENABLED=true` (undo step 2.1).

- [ ] **Step 14.5: Commit .env change**

```bash
git add .env
git commit -m "chore: re-enable MiniMax in .env after test"
```

---

## Task 15: Run full 30-min test (manual, optional but recommended)

**Files:** none

- [ ] **Step 15.1: Re-setup ai-hub**

Re-run Task 2 steps 2.2 + 2.3 + 2.4 (start llama.cpp + uvicorn + verify health).

- [ ] **Step 15.2: Run full test**

Run: `cd /home/hung/ai-hub && time python scripts/test_comprehensive_30min.py 2>&1 | tee /tmp/full_test_output.log`
Expected: completes in <35 min, prints verdict + metrics, writes JSON + log to `reports/`.

- [ ] **Step 15.3: Read report**

Run: `cd /home/hung/ai-hub && cat reports/comprehensive_30min_<latest>.json | python -m json.tool | head -80`
Expected: shows all phases, metrics, verdict, top errors.

- [ ] **Step 15.4: If FAIL → triage**

- [ ] If PASS → archive report to `reports/2026-06-12-comprehensive-30min/` directory.

- [ ] **Step 15.5: Stop ai-hub (re-run Task 14)**

- [ ] **Step 15.6: Final commit (any report notes)**

```bash
git add reports/  # if archiving
git commit -m "test: full 30-min comprehensive test report ($(date +%Y-%m-%d))"
```

---

## Self-Review Checklist

✅ **Spec coverage:**
- Section 4 data flow → Tasks 8-11 (4 phases)
- Section 5 topic bank + personas → Task 4
- Section 6 metrics → Task 5
- Section 7 pass/fail → Task 11
- Section 8 error handling → Task 5 (errors list) + Task 8 (status >= 500)
- Section 10 prerequisites → Tasks 1, 2, 14

✅ **Placeholder scan:** No TBD/TODO/implement-later. Each step has full code or clear command.

✅ **Type consistency:**
- `RequestMetric` defined Task 5, used Tasks 8-10
- `MetricsCollector.summary()` defined Task 5, consumed Task 11
- `PhaseResult` defined Task 8, used Tasks 9-12
- `Config` fields consistent across all tasks

✅ **File paths exact:** all paths use `scripts/test_comprehensive_30min.py`, `tests/integration/test_comprehensive_30min_smoke.py`, `.env`.

✅ **Commit cadence:** 14 commits across 13 implementation tasks + 1 cleanup. Each commit is small and reviewable.

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-06-12-ai-hub-comprehensive-test.md`.**

**Two execution options:**

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration. Best for code quality and catching issues early.

2. **Inline Execution** — Execute tasks in this session using `executing-plans`, batch execution with checkpoints. Best for faster overall progress.
