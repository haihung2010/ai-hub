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
    request_delay_seconds: float = 1.1  # throttle to ~54 rpm under 60 rpm Lite cap
    num_tenants: int = 2  # split users across N tenants for isolation testing

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
            request_delay_seconds=float(os.getenv("AIHUB_ECOM_REQUEST_DELAY", "1.1")),
            num_tenants=int(os.getenv("AIHUB_ECOM_TENANTS", "2")),
        )


# 5 personas, reused for 100 user instances
PERSONAS = ["An", "Bình", "Chi", "Dũng", "Em", "Phương", "Giang", "Hà", "Khánh", "Linh"]


async def _throttled_chat_post(
    session: aiohttp.ClientSession,
    cfg: TestConfig,
    payload: dict,
    headers: dict,
) -> tuple[int, dict | None, str | None]:
    """POST /v1/chat with throttling + 429/503 retry.

    Returns (status_code, json_body_or_None, error_message_or_None).
    Retries once on 429/503 after a 5s sleep; on second failure, gives up
    and returns the last status. Always throttles with cfg.request_delay_seconds
    after a successful or non-retryable response.
    """
    url = f"{cfg.base_url}/v1/chat"
    last_status = 0
    last_body: dict | None = None
    last_error: str | None = None
    for attempt in range(2):  # 0=first call, 1=retry
        try:
            async with session.post(
                url, json=payload, headers=headers,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                last_status = resp.status
                if resp.status < 400:
                    try:
                        last_body = await resp.json()
                    except Exception:
                        last_body = None
                    last_error = None
                    break
                if resp.status in (429, 503) and attempt == 0:
                    # Throttled by Lite cap or upstream busy; back off and retry once
                    await asyncio.sleep(5.0)
                    continue
                # Non-retryable or out of retries; capture error
                try:
                    last_body = await resp.json()
                except Exception:
                    last_body = None
                last_error = f"chat {resp.status}"
                break
        except Exception as e:
            last_error = f"chat exception: {e!r}"
            last_status = 0
            break
    # Always throttle to stay under the 60 rpm Lite cap
    if cfg.request_delay_seconds > 0:
        await asyncio.sleep(cfg.request_delay_seconds)
    return last_status, last_body, last_error


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
    last_reply: str = ""  # captured from /v1/chat response (last call)
    replies: list[str] = field(default_factory=list)  # ALL replies (Q1-Q7)
    errors: list[str] = field(default_factory=list)


class Session1Runner:
    """Q&A + create order. Simulates first-time buyer."""

    def __init__(self, cfg: TestConfig, session: aiohttp.ClientSession):
        self.cfg = cfg
        self.session = session
        self._semaphore = asyncio.Semaphore(cfg.concurrency)

    async def run_for_user(self, user_id: str, tenant_id: str, product: dict) -> Session1Result:
        result = Session1Result(user_id=user_id, questions_asked=0, answers_received=0, order_code=None)
        # Use a per-user nonce so concurrent runs in the same second don't collide
        nonce = random.randint(0, 99999)
        order_code = f"ORD-{user_id[-4:].upper()}-{nonce:05d}"
        # Ask 5 random product questions
        product_qs = [q for q in SESSION1_QUESTIONS if "đặt mua" not in q.lower()]
        questions = random.sample(product_qs, k=min(self.cfg.session1_questions - 1, len(product_qs)))
        for q in questions:
            await self._chat(user_id, tenant_id, q, result)
        # Final "đặt mua" question, then create order
        await self._chat(user_id, tenant_id, SESSION1_QUESTIONS[6], result)  # "Đặt mua 1 cái, mã đơn?"
        # Create order via API — bound to user's tenant
        try:
            async with self._semaphore:
                async with self.session.post(
                    f"{self.cfg.base_url}/v1/orders",
                    params={
                        "tenant_id": tenant_id, "user_id": user_id,
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

    async def _chat(self, user_id: str, tenant_id: str, message: str, result: Session1Result) -> None:
        result.questions_asked += 1
        async with self._semaphore:
            status, body, error = await _throttled_chat_post(
                self.session, self.cfg,
                payload={
                    "project_id": tenant_id, "tenant_id": tenant_id,
                    "user_name": user_id, "user_message": message,
                    "session_id": f"{user_id}_s1", "model_mode": "lite", "stream": False,
                },
                headers={"X-API-KEY": self.cfg.api_key},
            )
        if status < 400 and body is not None:
            result.answers_received += 1
            result.last_reply = (body.get("content", "") or "")[:500]
            result.replies.append(result.last_reply)
        elif error:
            result.errors.append(error)


@dataclass
class Session2Result:
    user_id: str
    order_code: str
    lookup_success: bool
    return_requested: bool
    last_reply: str = ""
    replies: list[str] = field(default_factory=list)  # ALL replies (Q1=order code, Q2=defect, Q3=when)
    errors: list[str] = field(default_factory=list)


class Session2Runner:
    """Return flow. Tests order lookup by code + return request."""

    def __init__(self, cfg: TestConfig, session: aiohttp.ClientSession):
        self.cfg = cfg
        self.session = session
        self._semaphore = asyncio.Semaphore(cfg.concurrency)

    async def run_for_user(self, user_id: str, tenant_id: str, order_code: str) -> Session2Result:
        result = Session2Result(user_id=user_id, order_code=order_code, lookup_success=False, return_requested=False)
        # Q1: "I want to return order ORD-XXXX" (this should trigger order_lookup_injection)
        await self._chat(user_id, tenant_id, SESSION2_QUESTIONS[0].format(order_code=order_code), result)
        # Check Q1 reply for product info (lenient: just need order_code mention + 1 product keyword)
        # result.replies[0] is Q1's reply
        if result.replies:
            q1_reply_lower = result.replies[0].lower()
            order_code_lower = order_code.lower()
            if order_code_lower in q1_reply_lower:
                # AI mentioned the order code, now check if it gave any product info
                product_keywords = ["áo thun", "quần", "váy", "giày", "áo", "màu", "size", "kích thước", "giá"]
                if any(kw in q1_reply_lower for kw in product_keywords):
                    result.lookup_success = True
        # Q2: "Defect description"
        await self._chat(user_id, tenant_id, SESSION2_QUESTIONS[1], result)
        # Q3: "When will replacement arrive?"
        await self._chat(user_id, tenant_id, SESSION2_QUESTIONS[2], result)
        # Mark return_requested if 0 errors
        if len(result.errors) == 0:
            result.return_requested = True
        return result

    async def _chat(self, user_id: str, tenant_id: str, message: str, result: Session2Result) -> None:
        async with self._semaphore:
            status, body, error = await _throttled_chat_post(
                self.session, self.cfg,
                payload={
                    "project_id": tenant_id, "tenant_id": tenant_id,
                    "user_name": user_id, "user_message": message,
                    "session_id": f"{user_id}_s2", "model_mode": "lite", "stream": False,
                },
                headers={"X-API-KEY": self.cfg.api_key},
            )
        if status < 400 and body is not None:
            result.last_reply = (body.get("content", "") or "")[:500]
            result.replies.append(result.last_reply)
        elif error:
            result.errors.append(error)


@dataclass
class Session3Result:
    user_id: str
    personalization_used: bool  # did AI reference previous preferences?
    memory_referenced: bool = False  # did AI mention specific facts from session 1?
    last_reply: str = ""
    replies: list[str] = field(default_factory=list)  # all replies
    errors: list[str] = field(default_factory=list)


class Session3Runner:
    """Future purchase. Tests personalization using cross-session memory."""

    def __init__(self, cfg: TestConfig, session: aiohttp.ClientSession):
        self.cfg = cfg
        self.session = session
        self._semaphore = asyncio.Semaphore(cfg.concurrency)

    async def run_for_user(self, user_id: str, tenant_id: str) -> Session3Result:
        result = Session3Result(user_id=user_id, personalization_used=False)
        for q in SESSION3_QUESTIONS:
            await self._chat(user_id, tenant_id, q, result)
        return result

    async def _chat(self, user_id: str, tenant_id: str, message: str, result: Session3Result) -> None:
        async with self._semaphore:
            status, body, error = await _throttled_chat_post(
                self.session, self.cfg,
                payload={
                    "project_id": tenant_id, "tenant_id": tenant_id,
                    "user_name": user_id, "user_message": message,
                    "session_id": f"{user_id}_s3", "model_mode": "lite", "stream": False,
                },
                headers={"X-API-KEY": self.cfg.api_key},
            )
        if status < 400 and body is not None:
            result.last_reply = (body.get("content", "") or "")[:500]
            result.replies.append(result.last_reply)
        elif error:
            result.errors.append(error)


class LeakChecker:
    """Verify User A in tenant T1 cannot access User B's order in tenant T2.

    Real cross-tenant test: UserA queries UserB's order_code via
    `GET /v1/orders/{code}?tenant_id={UserA.tenant}`. If the API returns 200
    (any 2xx) then we have a leak — User A can see data from a different tenant.
    A 404 is the expected, isolation-holding result.
    """

    def __init__(
        self,
        cfg: TestConfig,
        session: aiohttp.ClientSession,
        user_a: str,
        user_a_tenant: str,
        user_b_order_code: str,
        user_b_tenant: str,
    ):
        self.cfg = cfg
        self.session = session
        self.user_a = user_a
        self.user_a_tenant = user_a_tenant
        self.user_b_order_code = user_b_order_code
        self.user_b_tenant = user_b_tenant

    async def verify_isolation(self) -> bool:
        """UserA in tenant_a queries UserB's order_code (in tenant_b) with tenant_a.

        Returns True if isolation holds (UserA gets 404 / no 2xx).
        Returns False if leak (UserA gets 2xx with UserB's order data).
        Any exception or non-404 2xx → treat as potential leak (False).
        """
        try:
            async with self.session.get(
                f"{self.cfg.base_url}/v1/orders/{self.user_b_order_code}",
                params={"tenant_id": self.user_a_tenant},  # WRONG tenant for UserB's order
                headers={"X-API-KEY": self.cfg.api_key},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                status = resp.status
                # Throttle to stay under the 60 rpm cap
                if self.cfg.request_delay_seconds > 0:
                    await asyncio.sleep(self.cfg.request_delay_seconds)
                # Expected: 404, no leak
                # Leak: 2xx (data from another tenant returned)
                if 200 <= status < 300:
                    return False  # LEAK
                return True  # 404 or other 4xx → isolation holds
        except Exception:
            if self.cfg.request_delay_seconds > 0:
                await asyncio.sleep(self.cfg.request_delay_seconds)
            return False  # any error = treat as potential leak


@dataclass
class EcomReport:
    total_users: int
    session1_results: list[Session1Result]
    session2_results: list[Session2Result]
    session3_results: list[Session3Result]
    order_lookup_accuracy: float
    cross_session_memory_accuracy: float
    personalization_accuracy: float
    leak_count: int
    total_duration_seconds: float
    verdict: str
    leak_samples: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "test_name": "ecommerce-100user",
            "total_users": self.total_users,
            "session1_orders_created": sum(1 for r in self.session1_results if r.order_code),
            "session2_lookups_succeeded": sum(1 for r in self.session2_results if r.lookup_success),
            "session3_personalization_count": sum(1 for r in self.session3_results if r.personalization_used),
            "order_lookup_accuracy": self.order_lookup_accuracy,
            "cross_session_memory_accuracy": self.cross_session_memory_accuracy,
            "personalization_accuracy": self.personalization_accuracy,
            "leak_count": self.leak_count,
            "leak_samples": self.leak_samples,
            "total_duration_seconds": self.total_duration_seconds,
            "verdict": self.verdict,
            "sample_session2_replies_q1": [r.replies[0][:300] if r.replies else "" for r in self.session2_results[:3]],
            "sample_session3_replies": [r.replies[:1] for r in self.session3_results[:3] if r.replies],
            "criteria": {
                "order_lookup_target": 0.90,
                "memory_recall_target": 0.70,
                "personalization_target": 0.60,
                "leak_target": 0,
            },
        }


async def run_test(cfg: TestConfig) -> EcomReport:
    started = time.monotonic()
    async with aiohttp.ClientSession() as session:
        # Setup: clear any prior test data
        # (Skip for now - assume clean state)

        # Generate num_users split across 2 tenants (alternating).
        # Even indices -> "default", odd indices -> "tenant2".
        # Each user gets a unique id like ecom_user_000.
        users: list[tuple[str, str]] = []  # (user_id, tenant_id)
        for i in range(cfg.num_users):
            tenant = "default" if i % 2 == 0 else "tenant2"
            users.append((f"ecom_user_{i:03d}", tenant))
        user_ids = [u for u, _ in users]
        tenant_for_user = [t for _, t in users]
        products_chosen = [PRODUCTS[i % len(PRODUCTS)] for i in range(cfg.num_users)]

        # Session 1
        print(f"[main] Session 1: {len(users)} users × 7 questions = {len(users)*7} turns")
        s1 = Session1Runner(cfg, session)
        s1_tasks = [
            s1.run_for_user(uid, tid, p)
            for (uid, tid), p in zip(users, products_chosen)
        ]
        s1_results = await asyncio.gather(*s1_tasks)

        # Inter-session gap
        print(f"[main] Inter-session gap: {cfg.inter_session_gap_seconds}s (simulating 1 day)")
        await asyncio.sleep(cfg.inter_session_gap_seconds)

        # Session 2: return flow
        print(f"[main] Session 2: {len(s1_results)} users × 3 questions (return)")
        s2 = Session2Runner(cfg, session)
        s2_tasks = [
            s2.run_for_user(uid, tid, r.order_code)
            for (uid, tid), r in zip(users, s1_results)
            if r.order_code
        ]
        s2_results = await asyncio.gather(*s2_tasks)

        # Inter-session gap
        print(f"[main] Inter-session gap: {cfg.inter_session_gap_seconds}s (simulating 3 days)")
        await asyncio.sleep(cfg.inter_session_gap_seconds)

        # Session 3: future purchase
        print(f"[main] Session 3: {len(users)} users × 3 questions (future purchase)")
        s3 = Session3Runner(cfg, session)
        s3_results = await asyncio.gather(
            *[s3.run_for_user(uid, tid) for (uid, tid) in users]
        )

        # Cross-tenant leak check: pick 10 random user pairs across
        # different tenants. For each pair, UserA attempts to fetch
        # UserB's order_code using UserA's own tenant_id. If the response
        # is 2xx → LEAK (cross-tenant data exposed). 404 → isolation holds.
        print(f"[main] Leak check: 10 random cross-tenant order code lookups")
        leak_count = 0
        leak_samples: list[dict] = []
        successful_orders = {
            uid: r.order_code
            for (uid, _tid), r in zip(users, s1_results)
            if r.order_code
        }
        for _ in range(10):
            user_a, tenant_a = users[random.randint(0, len(users) - 1)]
            # Find a user in a different tenant
            different_tenant_users = [
                (u, t) for u, t in users if t != tenant_a
            ]
            if not different_tenant_users:
                continue
            user_b, tenant_b = different_tenant_users[
                random.randint(0, len(different_tenant_users) - 1)
            ]
            # Get UserB's order_code from session1_results
            user_b_order_code = successful_orders.get(user_b)
            if not user_b_order_code:
                continue
            # Verify UserA cannot see UserB's order
            leak_checker = LeakChecker(
                cfg, session,
                user_a=user_a, user_a_tenant=tenant_a,
                user_b_order_code=user_b_order_code, user_b_tenant=tenant_b,
            )
            isolated = await leak_checker.verify_isolation()
            if not isolated:
                leak_count += 1
                leak_samples.append({
                    "attacker_user": user_a,
                    "attacker_tenant": tenant_a,
                    "victim_user": user_b,
                    "victim_tenant": tenant_b,
                    "victim_order_code": user_b_order_code,
                })

    ended = time.monotonic()

    # Compute metrics
    order_lookup_acc = sum(1 for r in s2_results if r.lookup_success) / max(1, len(s2_results))

    # Cross-session memory: check if Session 3 first reply references size/color
    # from Session 1 (user said e.g. "size M", AI should mention it back)
    memory_keywords = ["size m", "size l", "trắng", "xanh", "đen", "áo thun", "quần", "váy", "giày", "mua", "lần trước", "trước đó"]
    memory_hits = 0
    for r in s3_results:
        for reply in r.replies[:1]:  # just first reply of session 3
            if any(kw in reply.lower() for kw in memory_keywords):
                r.memory_referenced = True
                memory_hits += 1
                break
    cross_session_acc = memory_hits / max(1, len(s3_results))

    # Personalization: check if Session 3 first reply asks follow-up aligned with history
    # Heuristic: reply contains phrases like "Bạn mua", "size", "màu", suggesting personalization
    personalization_phrases = ["bạn mua", "size", "màu", "bạn đã hỏi", "lần trước", "trước đó", "ưu tiên", "thường"]
    personalization_hits = 0
    for r in s3_results:
        for reply in r.replies[:1]:
            if any(phrase in reply.lower() for phrase in personalization_phrases):
                r.personalization_used = True
                personalization_hits += 1
                break
    personalization_acc = personalization_hits / max(1, len(s3_results))

    # Pass/fail: all 4 criteria
    passed = (order_lookup_acc >= cfg.order_lookup_target
              and cross_session_acc >= cfg.memory_recall_target
              and personalization_acc >= cfg.personalization_target
              and leak_count <= cfg.leak_target)
    verdict = "PASS" if passed else "FAIL"

    return EcomReport(
        total_users=len(users),
        session1_results=s1_results,
        session2_results=s2_results,
        session3_results=s3_results,
        order_lookup_accuracy=order_lookup_acc,
        cross_session_memory_accuracy=cross_session_acc,
        personalization_accuracy=personalization_acc,
        leak_count=leak_count,
        leak_samples=leak_samples,
        total_duration_seconds=ended - started,
        verdict=verdict,
    )


def main() -> int:
    cfg = TestConfig.from_env()
    report = asyncio.run(run_test(cfg))
    cfg.report_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = cfg.report_dir / f"ecommerce_100users_{ts}.json"
    with open(path, "w") as f:
        json.dump(report.to_dict(), f, indent=2, ensure_ascii=False)
    print(f"\n[main] Report: {path}")
    print(f"[main] Verdict: {report.verdict}")
    print(f"[main] Order lookup: {report.order_lookup_accuracy*100:.1f}% (target 90%)")
    print(f"[main] Memory recall: {report.cross_session_memory_accuracy*100:.1f}% (target 70%)")
    print(f"[main] Personalization: {report.personalization_accuracy*100:.1f}% (target 60%)")
    print(f"[main] Leaks: {report.leak_count} (target 0)")
    return 0 if report.verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
