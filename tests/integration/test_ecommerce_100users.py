"""E-commerce 100-user stress test.

Simulates 100 customers × 3 sessions (Q&A, return, future purchase) over
5 days (compressed to ~25 min for the test). Verifies 4 success criteria:
  1. Order lookup by code: 90%+
  2. Cross-session memory: 70%+
  3. Personalization: 60%+
  4. Multi-tenant isolation: 0 leaks
"""
from __future__ import annotations

import asyncio
import json
import os
import random
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import aiohttp


@dataclass
class TestConfig:
    base_url: str
    api_key: str
    concurrency: int
    num_users: int
    session1_questions: int
    session2_questions: int
    session3_questions: int
    inter_session_gap_seconds: int
    report_dir: Path
    order_lookup_target: float
    memory_recall_target: float
    personalization_target: float
    leak_target: int

    @classmethod
    def from_env(cls) -> "TestConfig":
        api_key = ""
        env_path = Path(__file__).resolve().parents[2] / ".env"
        with open(env_path) as f:
            for line in f:
                if line.startswith("API_KEY="):
                    api_key = line.split("=", 1)[1].strip().strip('"')
                    break
        return cls(
            base_url=os.getenv("AIHUB_ECOM_BASE_URL", "http://127.0.0.1:8000"),
            api_key=api_key,
            concurrency=int(os.getenv("AIHUB_ECOM_CONCURRENCY", "4")),
            num_users=int(os.getenv("AIHUB_ECOM_USERS", "100")),
            session1_questions=7,
            session2_questions=3,
            session3_questions=3,
            inter_session_gap_seconds=int(os.getenv("AIHUB_ECOM_GAP", "5")),
            report_dir=Path(os.getenv("AIHUB_ECOM_REPORT_DIR", "reports")),
            order_lookup_target=0.90,
            memory_recall_target=0.70,
            personalization_target=0.60,
            leak_target=0,
        )


# 5 personas, reused for 100 user instances
PERSONAS = ["An", "Bình", "Chi", "Dũng", "Em", "Phương", "Giang", "Hà", "Khánh", "Linh"]

# Session 1: 7 questions about product (random subset)
SESSION1_QUESTIONS = [
    "Có áo thun trắng size M không?",
    "Giá bao nhiêu?",
    "Có màu khác không? Đen, xám, xanh?",
    "Chất liệu vải gì? Cotton?",
    "Có co giãn không?",
    "Bảo hành bao lâu?",
    "Đặt mua 1 cái, mã đơn?",
]

# Session 2: 3 questions about return
SESSION2_QUESTIONS = [
    "Tôi muốn đổi trả đơn {order_code}",
    "Áo bị lỗi chỉ may",
    "Khi nào có hàng đổi?",
]

# Session 3: 3 questions about future purchase
SESSION3_QUESTIONS = [
    "Tôi muốn mua thêm áo thun",
    "Có size L không?",
    "Màu xanh navy có không?",
]

# 4 products to seed (RAG)
PRODUCTS = [
    {"name": "Áo thun trắng basic", "size": "M", "color": "trắng", "price": 250000, "warranty": "3 tháng", "material": "100% cotton"},
    {"name": "Quần jean xanh", "size": "L", "color": "xanh", "price": 450000, "warranty": "6 tháng", "material": "denim"},
    {"name": "Váy maxi hoa", "size": "M", "color": "trắng", "price": 350000, "warranty": "3 tháng", "material": "voan"},
    {"name": "Giày thể thao", "size": "42", "color": "đen", "price": 800000, "warranty": "12 tháng", "material": "mesh"},
]


@dataclass
class Session1Result:
    user_id: str
    questions_asked: int
    answers_received: int
    order_code: str | None
    errors: list[str] = field(default_factory=list)


class Session1Runner:
    """Q&A + create order. Simulates first-time buyer."""

    def __init__(self, cfg: TestConfig, session: aiohttp.ClientSession):
        self.cfg = cfg
        self.session = session
        self._semaphore = asyncio.Semaphore(cfg.concurrency)

    async def run_for_user(self, user_id: str, product: dict) -> Session1Result:
        result = Session1Result(user_id=user_id, questions_asked=0, answers_received=0, order_code=None)
        order_code = f"ORD-{user_id[-4:].upper()}-{int(time.time()) % 100000}"
        # Ask 5 random product questions
        product_qs = [q for q in SESSION1_QUESTIONS if "đặt mua" not in q.lower()]
        questions = random.sample(product_qs, k=min(self.cfg.session1_questions - 1, len(product_qs)))
        for q in questions:
            await self._chat(user_id, q, result)
        # Final "đặt mua" question, then create order
        await self._chat(user_id, SESSION1_QUESTIONS[6], result)  # "Đặt mua 1 cái, mã đơn?"
        # Create order via API
        try:
            async with self._semaphore:
                async with self.session.post(
                    f"{self.cfg.base_url}/v1/orders",
                    params={
                        "tenant_id": "default", "user_id": user_id,
                        "order_code": order_code, "product_name": product["name"],
                        "size": product["size"], "color": product["color"], "price": product["price"],
                    },
                    headers={"X-API-KEY": self.cfg.api_key},
                ) as resp:
                    if resp.status < 300:
                        result.order_code = order_code
                    else:
                        result.errors.append(f"create_order {resp.status}")
        except Exception as e:
            result.errors.append(f"create_order exception: {e!r}")
        return result

    async def _chat(self, user_id: str, message: str, result: Session1Result) -> None:
        result.questions_asked += 1
        try:
            async with self._semaphore:
                async with self.session.post(
                    f"{self.cfg.base_url}/v1/chat",
                    json={
                        "project_id": "default", "tenant_id": "default",
                        "user_name": user_id, "user_message": message,
                        "session_id": f"{user_id}_s1", "model_mode": "lite", "stream": False,
                    },
                    headers={"X-API-KEY": self.cfg.api_key},
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status < 400:
                        result.answers_received += 1
                    else:
                        result.errors.append(f"chat {resp.status}")
        except Exception as e:
            result.errors.append(f"chat exception: {e!r}")


@dataclass
class Session2Result:
    user_id: str
    order_code: str
    lookup_success: bool
    return_requested: bool
    errors: list[str] = field(default_factory=list)


class Session2Runner:
    """Return flow. Tests order lookup by code + return request."""

    def __init__(self, cfg: TestConfig, session: aiohttp.ClientSession):
        self.cfg = cfg
        self.session = session
        self._semaphore = asyncio.Semaphore(cfg.concurrency)

    async def run_for_user(self, user_id: str, order_code: str) -> Session2Result:
        result = Session2Result(user_id=user_id, order_code=order_code, lookup_success=False, return_requested=False)
        # Q1: "I want to return order ORD-XXXX"
        await self._chat(user_id, SESSION2_QUESTIONS[0].format(order_code=order_code), result)
        # Check if AI mentioned product name (proxy for lookup success)
        # We'll check this in ReportGenerator, here just track if response was 200
        # Q2: "Defect description"
        await self._chat(user_id, SESSION2_QUESTIONS[1], result)
        # Q3: "When will replacement arrive?"
        await self._chat(user_id, SESSION2_QUESTIONS[2], result)
        # Mark lookup success if we got 3 200s (proxy; real check in report)
        if len(result.errors) == 0:
            result.lookup_success = True
            result.return_requested = True
        return result

    async def _chat(self, user_id: str, message: str, result: Session2Result) -> None:
        try:
            async with self._semaphore:
                async with self.session.post(
                    f"{self.cfg.base_url}/v1/chat",
                    json={
                        "project_id": "default", "tenant_id": "default",
                        "user_name": user_id, "user_message": message,
                        "session_id": f"{user_id}_s2", "model_mode": "lite", "stream": False,
                    },
                    headers={"X-API-KEY": self.cfg.api_key},
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status >= 400:
                        result.errors.append(f"chat {resp.status}")
        except Exception as e:
            result.errors.append(f"chat exception: {e!r}")
