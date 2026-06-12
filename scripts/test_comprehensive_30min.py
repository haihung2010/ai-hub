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
