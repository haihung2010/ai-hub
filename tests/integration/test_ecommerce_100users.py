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
