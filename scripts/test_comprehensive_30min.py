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
@dataclass
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
    is_cache_test: bool = False

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
                # Mark is_cache_test by looking up the global topic bank
                if not occ.is_cache_test:
                    occ.is_cache_test = any(t.name == m.topic and t.is_cache_test for t in all_topics())
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
        for color in colors[:5]:  # always 5 colors → 50 product cards total
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
        # CSRF middleware requires cookie + matching X-CSRF-Token header.
        # unsafe=True allows cookie storage on 127.0.0.1 (otherwise jar is empty).
        jar = aiohttp.CookieJar(unsafe=True)
        self.session = aiohttp.ClientSession(headers=self.cfg.headers(), cookie_jar=jar)
        return self

    async def __aexit__(self, *exc) -> None:
        if self.session:
            await self.session.close()

    async def seed(self, n: int) -> tuple[int, int]:
        """Generate n cards + upload. Returns (success_count, fail_count).

        Combines 50 product cards + 25 FAQ cards (75 total available).
        Uses n if smaller, else all 75.
        """
        all_cards = _gen_product_cards() + _gen_faq_cards()
        # Plan calls for 50 product + 25 FAQ = 75 total. Trim products to 50 if needed.
        products = all_cards[:50]
        faqs = all_cards[50:75]
        cards = (products + faqs)[:n]
        # Bootstrap CSRF: do a GET to mint the cookie, capture token, send on POST
        try:
            async with self.session.get(
                f"{self.cfg.base_url}/v1/admin/knowledge/cards",
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                csrf_morsel = r.cookies.get("csrf_token")
                csrf_token = csrf_morsel.value if csrf_morsel else None
        except Exception as e:
            self.log.warning(f"csrf bootstrap failed: {e!r}")
            csrf_token = None

        ok, fail = 0, 0
        for i, card in enumerate(cards):
            payload = {
                "project_id": "playground",
                "tenant_id": "default",
                "title": card.title,
                "content": card.content,
                "domain": card.domain,
                "tags": card.tags,
            }
            headers = {}
            if csrf_token:
                headers["X-CSRF-Token"] = csrf_token
            try:
                async with self.session.post(
                    f"{self.cfg.base_url}/v1/admin/knowledge/upload",
                    json=payload,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status < 300:
                        ok += 1
                        if (i + 1) % 10 == 0:
                            print(f"  [seed] {i+1}/{len(cards)} uploaded")
                    else:
                        fail += 1
                        body_text = await resp.text()
                        self.log.error(f"seed fail [{resp.status}]: {card.title} | {body_text[:200]}")
            except Exception as e:
                fail += 1
                self.log.error(f"seed exception: {card.title} | {e!r}")
        # Wait for embeddings to finish (best-effort)
        await asyncio.sleep(2)
        return ok, fail


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
            "project_id": "playground",
            "tenant_id": "default",
            "user_name": user,
            "user_message": message,
            "session_id": session_id,
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
