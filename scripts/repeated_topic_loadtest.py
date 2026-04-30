#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import os
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from statistics import mean, median
from urllib import request as urlrequest
from urllib.error import HTTPError, URLError

BASE_URL = os.getenv("AIHUB_LOADTEST_URL", "http://localhost:8000")
PROJECT_ID = os.getenv("AIHUB_LOADTEST_PROJECT", "test")
TENANT_ID = os.getenv("AIHUB_LOADTEST_TENANT", "hybridrepeat")
TIMEOUT_SECONDS = float(os.getenv("AIHUB_LOADTEST_TIMEOUT", "180"))
USER_COUNT = max(1, int(os.getenv("AIHUB_LOADTEST_USERS", "20")))
NUM_QUESTIONS = max(2, int(os.getenv("AIHUB_LOADTEST_QUESTIONS", "50")))
EXTRA_QUESTIONS_USERS = max(0, int(os.getenv("AIHUB_LOADTEST_EXTRA_QUESTIONS_USERS", "0")))
EXTRA_QUESTIONS = max(0, int(os.getenv("AIHUB_LOADTEST_EXTRA_QUESTIONS", "0")))
MAX_CONCURRENCY = max(1, int(os.getenv("AIHUB_LOADTEST_MAX_CONCURRENCY", str(USER_COUNT))))
REPORT_NAME = os.getenv("AIHUB_LOADTEST_REPORT", "repeated_topic_20u50")
ALLOW_EXTERNAL = os.getenv("AIHUB_LOADTEST_ALLOW_EXTERNAL", "true").lower() == "true"
MODEL_MODE = os.getenv("AIHUB_LOADTEST_MODEL_MODE", "lite")
ANSWER_STYLE = os.getenv("AIHUB_LOADTEST_ANSWER_STYLE", "brief")
SEED = int(os.getenv("AIHUB_LOADTEST_SEED", "20260430"))
API_KEY = os.getenv("AIHUB_API_KEY") or os.getenv("API_KEY")

SHARED_REPEAT_QUESTIONS = [
    "Thời tiết hôm nay ở TP.HCM như thế nào? Trả lời ngắn và nói nếu không có dữ liệu realtime.",
    "Thời tiết hôm nay ở Hà Nội như thế nào? Trả lời ngắn và nói nếu không có dữ liệu realtime.",
    "Giá VNINDEX hôm nay ra sao? Nếu không có dữ liệu realtime thì nói rõ.",
    "Cổ phiếu VCB hôm nay tăng hay giảm? Nếu không có dữ liệu realtime thì nói rõ.",
    "Vehix hiện có những dòng xe nào phù hợp gia đình?",
    "Vehix có xe điện nào đáng mua không? So sánh ngắn.",
    "Giá xe sedan hạng B hiện khoảng bao nhiêu?",
    "SUV 7 chỗ nào phù hợp đi gia đình và tiết kiệm nhiên liệu?",
    "RAG database giúp chatbot trả lời câu hỏi lặp lại như thế nào?",
    "Vector database khác keyword search ở điểm nào?",
]

TOPIC_BANK = {
    "weather": [
        "Nếu nhiều người cùng hỏi thời tiết hôm nay, hệ thống nên cache câu trả lời thế nào?",
        "Dự báo mưa nên trình bày cho người dùng cuối ra sao nếu dữ liệu realtime thiếu?",
        "Thời tiết cuối tuần ở Đà Nẵng có nên đi du lịch không? Nếu không có realtime thì nêu nguyên tắc đánh giá.",
        "Khi chatbot không có dữ liệu thời tiết realtime, nên trả lời sao để không hallucinate?",
    ],
    "vehix": [
        "Vehix nên tư vấn xe theo ngân sách 700 triệu như thế nào?",
        "Người dùng hỏi giá xe lặp lại thì RAG nên lấy dữ liệu từ bảng nào?",
        "So sánh sedan và SUV cho khách mua xe lần đầu.",
        "Khách cần xe chạy dịch vụ thì nên hỏi thêm thông tin gì?",
        "Xe hybrid có lợi thế gì ở đô thị Việt Nam?",
    ],
    "stocks": [
        "Nếu người dùng hỏi giá cổ phiếu realtime, chatbot cần cảnh báo gì?",
        "P/E và P/B nên dùng ra sao khi so sánh ngân hàng?",
        "VNINDEX giảm mạnh thì nhà đầu tư mới nên làm gì?",
        "Dòng tiền thị trường chứng khoán nên được giải thích thế nào cho người mới?",
        "Rủi ro khi dùng AI để tư vấn đầu tư là gì?",
    ],
    "rag_ai": [
        "RAG pipeline gồm những bước nào?",
        "Embedding nên được cập nhật khi dữ liệu nguồn thay đổi ra sao?",
        "Chunk size ảnh hưởng thế nào đến chất lượng truy xuất?",
        "Hybrid search BM25 + vector có lợi gì?",
        "Làm sao đo hallucination trong hệ thống RAG?",
    ],
    "web_support": [
        "Chatbot CSKH nên nhớ lịch sử hội thoại theo user như thế nào?",
        "Rate limit nên thiết kế thế nào khi 20 user đồng thời?",
        "Khi local model quá tải thì cloud fallback nên hoạt động ra sao?",
        "Stream response giúp UX chatbot tốt hơn như thế nào?",
        "API gateway giúp bảo vệ chatbot service ra sao?",
    ],
    "finance_general": [
        "Lãi suất tăng ảnh hưởng gì đến thị trường chứng khoán?",
        "DCA là gì và phù hợp với ai?",
        "Quản trị rủi ro danh mục nên bắt đầu từ đâu?",
        "Trái phiếu doanh nghiệp có rủi ro gì?",
        "ETF khác quỹ chủ động thế nào?",
    ],
}

MEMORY_CHECK = "Hãy tóm tắt lại các chủ đề chính tôi đã hỏi trong cuộc trò chuyện này, nhớ các câu hỏi bị lặp lại nếu có."


@dataclass
class UserResult:
    user: str
    topic_mix: list[str]
    ok: int = 0
    errors: int = 0
    latencies_s: list[float] = field(default_factory=list)
    providers: dict[str, int] = field(default_factory=dict)
    models: dict[str, int] = field(default_factory=dict)
    routes: dict[str, int] = field(default_factory=dict)
    fallback_used: int = 0
    repeated_questions_seen: int = 0
    final_memory_ok: bool = False
    error_messages: list[str] = field(default_factory=list)


def question_count_for_user(user_index: int) -> int:
    if user_index <= EXTRA_QUESTIONS_USERS:
        return NUM_QUESTIONS + EXTRA_QUESTIONS
    return NUM_QUESTIONS


def build_questions(user_index: int, rng: random.Random) -> tuple[list[str], list[str], int]:
    topics = rng.sample(list(TOPIC_BANK), k=3)
    questions: list[str] = []
    repeat_count = 0
    target_questions = question_count_for_user(user_index)

    while len(questions) < target_questions - 1:
        if rng.random() < 0.38:
            questions.append(rng.choice(SHARED_REPEAT_QUESTIONS))
            repeat_count += 1
            continue
        topic = rng.choice(topics)
        questions.append(rng.choice(TOPIC_BANK[topic]))

    questions.append(MEMORY_CHECK)
    return questions, topics, repeat_count


def post_chat(payload: dict) -> tuple[int, dict | str]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if API_KEY:
        headers["X-API-KEY"] = API_KEY
    req = urlrequest.Request(f"{BASE_URL}/v1/chat", data=data, headers=headers, method="POST")
    try:
        with urlrequest.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return resp.status, json.loads(body)
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return exc.code, body
    except (TimeoutError, URLError) as exc:
        return 0, str(exc)


async def run_user(user_index: int, sem: asyncio.Semaphore) -> UserResult:
    rng = random.Random(SEED + user_index)
    user = f"hybrid_user_{user_index:02d}"
    questions, topics, repeat_count = build_questions(user_index, rng)
    result = UserResult(user=user, topic_mix=topics, repeated_questions_seen=repeat_count)
    session_id: str | None = None

    for index, question in enumerate(questions, start=1):
        prompt = question
        if ANSWER_STYLE == "brief" and index < len(questions):
            prompt = f"{question}\n\nYêu cầu trả lời ngắn gọn dưới 5 câu."

        payload = {
            "project_id": PROJECT_ID,
            "tenant_id": TENANT_ID,
            "user_name": user,
            "user_message": prompt,
            "model_mode": MODEL_MODE,
            "allow_external": ALLOW_EXTERNAL,
        }
        if session_id:
            payload["session_id"] = session_id

        started = time.perf_counter()
        async with sem:
            status, response = await asyncio.to_thread(post_chat, payload)
        latency = time.perf_counter() - started
        result.latencies_s.append(round(latency, 3))

        if status != 200 or not isinstance(response, dict):
            result.errors += 1
            result.error_messages.append(str(response)[:300])
            continue

        result.ok += 1
        session_id = str(response.get("session_id") or session_id or "")
        provider = str(response.get("provider") or "unknown")
        model = str(response.get("model") or "unknown")
        route = str(response.get("route") or "unknown")
        result.providers[provider] = result.providers.get(provider, 0) + 1
        result.models[model] = result.models.get(model, 0) + 1
        result.routes[route] = result.routes.get(route, 0) + 1
        if response.get("fallback_used"):
            result.fallback_used += 1
        if index == len(questions):
            content = str(response.get("content") or "")
            result.final_memory_ok = any(q.split("?")[0][:30] in content for q in SHARED_REPEAT_QUESTIONS[:4]) or "chủ đề" in content.lower()

    return result


def pct(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    index = min(len(values) - 1, int(round((len(values) - 1) * percentile)))
    return values[index]


async def main() -> None:
    print("=" * 70)
    print("REPEATED TOPIC HYBRID LOAD TEST")
    print("=" * 70)
    print(f"BASE_URL    : {BASE_URL}")
    print(f"PROJECT     : {PROJECT_ID}")
    print(f"TENANT      : {TENANT_ID}")
    print(f"MODEL_MODE  : {MODEL_MODE}")
    print(f"ALLOW_CLOUD : {ALLOW_EXTERNAL}")
    print(f"Users       : {USER_COUNT}")
    print(f"Qs/user     : {NUM_QUESTIONS}")
    if EXTRA_QUESTIONS_USERS and EXTRA_QUESTIONS:
        print(f"Extra Qs    : first {EXTRA_QUESTIONS_USERS} users +{EXTRA_QUESTIONS}")
    print(f"Concurrency : {MAX_CONCURRENCY}")

    sem = asyncio.Semaphore(MAX_CONCURRENCY)
    started = time.perf_counter()
    results = await asyncio.gather(*(run_user(i, sem) for i in range(1, USER_COUNT + 1)))
    wall = time.perf_counter() - started

    all_latencies = [lat for result in results for lat in result.latencies_s]
    providers: dict[str, int] = {}
    models: dict[str, int] = {}
    routes: dict[str, int] = {}
    for result in results:
        for key, value in result.providers.items():
            providers[key] = providers.get(key, 0) + value
        for key, value in result.models.items():
            models[key] = models.get(key, 0) + value
        for key, value in result.routes.items():
            routes[key] = routes.get(key, 0) + value

    total_questions = sum(question_count_for_user(i) for i in range(1, USER_COUNT + 1))
    total_ok = sum(result.ok for result in results)
    total_errors = sum(result.errors for result in results)
    total_fallback = sum(result.fallback_used for result in results)
    memory_ok = sum(1 for result in results if result.final_memory_ok)

    print("\n" + "=" * 70)
    print("FINAL REPORT")
    print("=" * 70)
    print(f"Total questions : {total_questions}")
    print(f"Total OK        : {total_ok}/{total_questions}")
    print(f"Total errors    : {total_errors}")
    print(f"Wall time       : {wall:.1f}s")
    print(f"Latency p50/p95 : {median(all_latencies):.3f}s / {pct(all_latencies, 0.95):.3f}s")
    print(f"Latency p99/max : {pct(all_latencies, 0.99):.3f}s / {max(all_latencies, default=0):.3f}s")
    print(f"Providers       : {providers}")
    print(f"Models          : {models}")
    print(f"Routes          : {routes}")
    print(f"Fallback used   : {total_fallback}")
    print(f"Memory checks   : {memory_ok}/{USER_COUNT}")

    print("\nPer-user:")
    for result in results:
        avg_latency = mean(result.latencies_s) if result.latencies_s else 0.0
        print(
            f"{result.user} topics={','.join(result.topic_mix)} ok={result.ok} err={result.errors} "
            f"avg={avg_latency:.3f}s repeated={result.repeated_questions_seen} providers={result.providers}"
        )

    report = {
        "config": {
            "base_url": BASE_URL,
            "project_id": PROJECT_ID,
            "tenant_id": TENANT_ID,
            "model_mode": MODEL_MODE,
            "allow_external": ALLOW_EXTERNAL,
            "user_count": USER_COUNT,
            "num_questions": NUM_QUESTIONS,
            "extra_questions_users": EXTRA_QUESTIONS_USERS,
            "extra_questions": EXTRA_QUESTIONS,
            "max_concurrency": MAX_CONCURRENCY,
            "seed": SEED,
            "shared_repeat_questions": SHARED_REPEAT_QUESTIONS,
        },
        "summary": {
            "total_questions": total_questions,
            "total_ok": total_ok,
            "total_errors": total_errors,
            "wall_time_s": round(wall, 3),
            "latency_p50_s": round(median(all_latencies), 3) if all_latencies else 0,
            "latency_p95_s": round(pct(all_latencies, 0.95), 3),
            "latency_p99_s": round(pct(all_latencies, 0.99), 3),
            "latency_max_s": round(max(all_latencies, default=0), 3),
            "providers": providers,
            "models": models,
            "routes": routes,
            "fallback_used": total_fallback,
            "memory_checks_ok": memory_ok,
        },
        "users": [result.__dict__ for result in results],
    }
    report_path = Path("reports") / REPORT_NAME
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\nJSON report saved → {report_path}")


if __name__ == "__main__":
    asyncio.run(main())
