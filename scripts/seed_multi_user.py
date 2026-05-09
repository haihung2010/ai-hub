#!/usr/bin/env python3
"""Seed multi-user/project/tenant data into AI Hub for admin UI demo.

Each user sends 2-3 short chat turns to a project. Multiple users per project,
multiple projects per tenant, multiple tenants. RAG cards seeded too.

Usage:  ./venv/bin/python scripts/seed_multi_user.py
"""
from __future__ import annotations

import asyncio
import os
import random
from pathlib import Path

import aiohttp

ROOT = Path(__file__).resolve().parents[1]
ENV = ROOT / ".env"
API = "http://localhost:8000"

API_KEY = ""
for line in ENV.read_text().splitlines():
    if line.startswith("API_KEY="):
        API_KEY = line.split("=", 1)[1].strip()
        break

HEADERS = {"X-API-KEY": API_KEY, "Content-Type": "application/json"}

TENANTS = {
    "acme":    ["sales_bot",  "support_bot",  "research_lab"],
    "globex":  ["customer_q", "internal_wiki"],
    "initech": ["medical_q",  "legal_advisor"],
    "default": ["test",       "playground"],
}

USERS_PER_PROJECT = {
    "sales_bot":     ["alice", "bob", "carol", "dan"],
    "support_bot":   ["eve", "frank"],
    "research_lab":  ["grace", "heidi", "ivan"],
    "customer_q":    ["judy", "ken", "leo"],
    "internal_wiki": ["mallory", "niaj"],
    "medical_q":     ["dr_olivia", "dr_peggy", "dr_quentin"],
    "legal_advisor": ["rupert", "sybil"],
    "test":          ["hung", "demo_user"],
    "playground":    ["tester1", "tester2", "tester3"],
}

QUESTIONS = [
    "Xin chào, bạn là ai?",
    "Cho tôi tóm tắt chức năng của hệ thống.",
    "Làm sao để gọi API chat?",
    "Giải thích PostgreSQL index hoạt động ra sao.",
    "Viết hàm Python sort danh sách dict theo key.",
    "REST khác GraphQL ra sao?",
    "Khi nào dùng Redis pub/sub thay vì queue?",
    "So sánh JWT và session cookie.",
    "Hướng dẫn deploy app FastAPI bằng Docker.",
    "Tốc độ inference llama.cpp Q4 vs Q8?",
    "Làm thế nào để tối ưu vector search?",
    "Cho tôi biết các best practice về logging.",
    "Cách thiết kế multi-tenant database schema?",
    "Embedding model nào tốt cho tiếng Việt?",
    "Rate limit nên dùng token bucket hay sliding window?",
]

RAG_CARDS = [
    ("acme",    "sales_bot",     "general", "Pricing tier overview",
     "AI Hub có 3 tier: Free (60 RPM), Pro ($29/mo, 600 RPM), Enterprise (custom)."),
    ("acme",    "support_bot",   "general", "Refund policy",
     "Refund được chấp nhận trong 14 ngày kể từ ngày mua. Liên hệ support@acme.dev."),
    ("globex",  "customer_q",    "general", "Onboarding flow",
     "Khách hàng mới đăng ký → verify email → chọn workspace → mời team → done."),
    ("initech", "medical_q",     "medical", "Symptom triage",
     "Khi triệu chứng kéo dài >72h hoặc có sốt cao >39C, đề xuất gặp bác sĩ ngay."),
    ("initech", "legal_advisor", "legal",   "GDPR data deletion",
     "Người dùng EU có quyền yêu cầu xoá dữ liệu cá nhân trong 30 ngày."),
    ("default", "test",          "tech",    "Cache strategy",
     "Dùng Redis với TTL 5 phút cho hot key. Fallback in-memory nếu Redis down."),
]


async def chat_turn(session, tenant, project, user, message):
    payload = {
        "project_id": project,
        "tenant_id": tenant,
        "user_name": user,
        "user_message": message,
        "model_mode": "lite",
        "enable_search": False,
    }
    try:
        async with session.post(f"{API}/v1/chat", json=payload, headers=HEADERS,
                                timeout=aiohttp.ClientTimeout(total=90)) as r:
            ok = r.status == 200
            await r.text()
            return ok, r.status
    except Exception as e:
        return False, str(e)[:80]


async def user_session(session, tenant, project, user, n_turns):
    print(f"  [{tenant}/{project}] {user} → {n_turns} turn(s)", flush=True)
    for i in range(n_turns):
        q = random.choice(QUESTIONS)
        ok, status = await chat_turn(session, tenant, project, user, q)
        marker = "✓" if ok else "✗"
        print(f"    {marker} {user}.{i+1} status={status} q={q[:40]!r}", flush=True)
        await asyncio.sleep(0.2)


async def seed_rag(session):
    print("\n=== Seeding RAG cards ===", flush=True)
    for tenant, project, domain, title, content in RAG_CARDS:
        body = {"project_id": project, "tenant_id": tenant,
                "domain": domain, "title": title, "content": content}
        try:
            async with session.post(f"{API}/v1/admin/knowledge/upload",
                                    json=body, headers=HEADERS,
                                    timeout=aiohttp.ClientTimeout(total=30)) as r:
                ok = r.status == 200
                print(f"  {'✓' if ok else '✗'} [{tenant}/{project}] {title} ({r.status})", flush=True)
        except Exception as e:
            print(f"  ✗ [{tenant}/{project}] {title} → {e}", flush=True)


async def seed_keys(session):
    print("\n=== Seeding tenant keys ===", flush=True)
    for tenant in TENANTS:
        if tenant == "default":
            continue
        body = {"name": f"{tenant}-app", "tenant_id": tenant,
                "is_admin": False, "rpm_limit": random.choice([30, 60, 120])}
        try:
            async with session.post(f"{API}/v1/admin/keys",
                                    json=body, headers=HEADERS,
                                    timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status == 200:
                    d = await r.json()
                    print(f"  ✓ [{tenant}] {body['name']} id={d['id']} rpm={body['rpm_limit']}", flush=True)
                else:
                    print(f"  ✗ [{tenant}] HTTP {r.status}", flush=True)
        except Exception as e:
            print(f"  ✗ [{tenant}] {e}", flush=True)


async def main():
    print(f"=== AI Hub multi-user seed ===")
    print(f"Tenants: {list(TENANTS.keys())}")
    total_users = sum(len(USERS_PER_PROJECT[p]) for projs in TENANTS.values() for p in projs)
    print(f"Total users: {total_users}\n")

    timeout = aiohttp.ClientTimeout(total=120)
    connector = aiohttp.TCPConnector(limit=4)
    async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
        await seed_keys(session)
        await seed_rag(session)

        print("\n=== Seeding chat sessions ===", flush=True)
        tasks = []
        for tenant, projects in TENANTS.items():
            for project in projects:
                for user in USERS_PER_PROJECT[project]:
                    n_turns = random.randint(2, 4)
                    tasks.append(user_session(session, tenant, project, user, n_turns))
        # Limit concurrency
        sem = asyncio.Semaphore(4)
        async def bound(t):
            async with sem:
                await t
        await asyncio.gather(*[bound(t) for t in tasks])

    print("\n=== Done. Open http://localhost:8000/admin.html ===")


if __name__ == "__main__":
    asyncio.run(main())
