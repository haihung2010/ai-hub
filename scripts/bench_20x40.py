#!/usr/bin/env python3
"""20x40 benchmark with summary check - 800 requests, 40 concurrency"""
import base64
import concurrent.futures as cf
import json
import os
import statistics
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE_URL = os.environ.get("AIHUB_URL", "http://127.0.0.1:8000")
API_KEY = os.environ["API_KEY"]
CONCURRENCY = int(os.environ.get("AIHUB_TEST_CONCURRENCY", "40"))
TIMEOUT = int(os.environ.get("AIHUB_TEST_TIMEOUT", "180"))

# 20 categories × 40 questions each
categories = {
    "general_vi": [f"VI Question {i}: " + ["Chào bạn, giới thiệu ngắn hệ thống AI Hub.",
        "Tóm tắt lợi ích của chatbot nội bộ cho doanh nghiệp nhỏ.",
        "Viết 5 gạch đầu dòng về ưu điểm dùng local LLM.",
        "Giải thích Redis dùng làm gì trong hệ thống này.",
        "Giải thích PostgreSQL khác SQLite ở điểm nào."][i % 5] for i in range(40)],
    "technical": [f"TECH Question {i}: " + ["FastAPI middleware thường dùng để làm gì?",
        "SSE streaming hoạt động thế nào trong chat API?",
        "So sánh rate limit Redis sliding window với fixed window.",
        "Vì sao cần connection pool cho PostgreSQL?",
        "RAG hybrid search semantic + token overlap có lợi gì?"][i % 5] for i in range(40)],
    "coding": [f"CODE Question {i}: " + ["Viết Python function validate email đơn giản.",
        "Viết SQL query đếm message theo user_id.",
        "Viết JS debounce function ngắn.",
        "Viết FastAPI route /ping trả pong.",
        "Viết pytest cho hàm add(a,b)."][i % 5] for i in range(40)],
    "reasoning": [f"REASON Question {i}: " + ["Nếu 3 máy mỗi máy xử lý 4 request/phút, 120 request mất tối thiểu bao lâu?",
        "Một queue capacity 16, active 12, waiting 20. Hệ thống có nghẽn không? Vì sao?",
        "Nếu p50 latency 5s và p95 20s, nên tối ưu gì trước?",
        "Có 2 model: nhanh kém và chậm tốt. Đề xuất routing policy.",
        "Nếu Redis down nhưng fallback memory chạy, rủi ro gì?"][i % 5] for i in range(40)],
    "business": [f"BIZ Question {i}: " + ["Đề xuất 5 tính năng cho SaaS chatbot bán hàng.",
        "Viết pitch 30 giây cho AI Hub.",
        "Tạo bảng giá 3 gói dịch vụ AI chatbot.",
        "Gợi ý KPI cho chatbot CSKH.",
        "Viết email follow-up sau demo AI."][i % 5] for i in range(40)],
    "finance_stock": [f"FIN Question {i}: " + ["Giải thích P/E cho người mới đầu tư.",
        "So sánh phân tích cơ bản và phân tích kỹ thuật.",
        "Nêu 5 rủi ro khi mua cổ phiếu tăng nóng.",
        "Viết disclaimer ngắn cho báo cáo đầu tư.",
        "Nếu doanh thu tăng nhưng lợi nhuận giảm, có thể do gì?"][i % 5] for i in range(40)],
    "security": [f"SEC Question {i}: " + ["API key nên lưu và truyền thế nào an toàn?",
        "CORS sai có thể gây rủi ro gì?",
        "Nêu biện pháp chống brute force API key.",
        "Vì sao không nên log raw token?",
        "Giải thích principle of least privilege."][i % 5] for i in range(40)],
    "creative": [f"CREATIVE Question {i}: " + ["Đặt 10 tên thương hiệu cho chatbot AI tiếng Việt.",
        "Viết slogan ngắn cho AI Hub.",
        "Viết post Facebook giới thiệu chatbot nội bộ.",
        "Tạo 5 headline landing page cho AI automation.",
        "Viết kịch bản video 30s quảng cáo AI Hub."][i % 5] for i in range(40)],
    "vietnamese_customer": [f"VI_CUST Question {i}: " + ["Khách hỏi: chatbot có trả lời sai không? Trả lời ngắn.",
        "Khách hỏi: dữ liệu có bị đưa lên cloud không? Trả lời tự tin.",
        "Khách hỏi: triển khai mất bao lâu? Trả lời thực tế.",
        "Khách hỏi: có tích hợp website được không? Trả lời ngắn.",
        "Khách hỏi: có dùng tiếng Việt tốt không? Trả lời."][i % 5] for i in range(40)],
    "edge_cases": [f"EDGE Question {i}: " + ["Trả lời chỉ bằng một câu: hệ thống ổn không?",
        "Nếu không biết câu trả lời, hãy nói không biết và nêu cách kiểm chứng.",
        "Không dùng emoji. Tóm tắt 3 ý chính về rate limit.",
        "Trả lời bằng JSON với key status và message: health ok.",
        "Dịch sang tiếng Anh: Hệ thống đang hoạt động ổn định."][i % 5] for i in range(40)],
    "long_context": [f"LONG Question {i}: " + ["Tóm tắt ngắn: AI Hub gồm FastAPI, PostgreSQL, Redis, llama.cpp, RAG, reranker, admin UI. Nêu vai trò từng phần.",
        "Từ các thành phần FastAPI/Postgres/Redis/llama.cpp/RAG, đề xuất thứ tự debug khi user báo chậm.",
        "Viết sơ đồ luồng request từ browser đến model rồi trả stream.",
        "Nêu nơi nên đo latency trong pipeline chat API.",
        "Tạo checklist migration SQLite sang PostgreSQL."][i % 5] for i in range(40)],
    "system_admin": [f"SYS Question {i}: " + ["Liệt kê các log file quan trọng trong AI Hub.",
        "Cách restart llama.cpp server khi nó freeze.",
        "Làm sao check queue depth qua API?",
        "Cách force restart uvicorn mà không mất request.",
        "Giải thích các thông số GPU trong nvidia-smi output."][i % 5] for i in range(40)],
    "database": [f"DB Question {i}: " + ["Cách backup PostgreSQL cho AI Hub.",
        "Làm sao check xem có bao nhiêu messages trong DB?",
        "SQL để đếm messages theo tenant_id.",
        "Cách optimize PostgreSQL query cho chat history.",
        "Index nào cần tạo cho bảng messages?"][i % 5] for i in range(40)],
    "performance": [f"PERF Question {i}: " + ["Cách đo latency của một request?",
        "Làm sao biết GPU bị bottleneck?",
        "So sánh p50 và p95 latency.",
        "Khi nào nên tăng ctx size vs parallel?",
        "Cách debug slow request trong AI Hub?"][i % 5] for i in range(40)],
    "integration": [f"INT Question {i}: " + ["Cách kết nối Facebook webhook với AI Hub?",
        "Làm sao lấy access token cho Facebook page?",
        "Cách test webhook locally?",
        "Sự khác nhau giữa GET và POST webhook endpoint?",
        "Cách handle webhook retry từ Facebook?"][i % 5] for i in range(40)],
    "quality": [f"QUAL Question {i}: " + ["Làm sao đánh giá chat response quality?",
        "Cách so sánh 2 model response?",
        "Metric nào dùng để đo chat quality?",
        "Khi nào nên dùng cloud thay vì local?",
        "Cách benchmark AI Hub performance?"][i % 5] for i in range(40)],
    "memory": [f"MEM Question {i}: " + ["Cách check memory summary trong AI Hub?",
        "Khi nào conversation được summarize?",
        "Làm sao xem structured memory của user?",
        "Cách clear memory mà không xóa history?",
        "Sự khác nhau giữa summary và structmem?"][i % 5] for i in range(40)],
    "knowledge": [f"KB Question {i}: " + ["Cách upload knowledge card vào AI Hub?",
        "Làm sao check RAG search quality?",
        "Hybrid search semantic + token overlap hoạt động thế nào?",
        "Cách đo recall của RAG system?",
        "Reranker cross-encoder khác embedding search thế nào?"][i % 5] for i in range(40)],
    "api": [f"API Question {i}: " + ["Cách lấy API key cho AI Hub?",
        "Rate limit của AI Hub là bao nhiêu?",
        "Cách check usage qua API?",
        "Endpoint nào để quản lý API keys?",
        "Cách monitor queue depth?"][i % 5] for i in range(40)],
    "troubleshooting": [f"TSHOOT Question {i}: " + ["Bot trả lời sai thường do nguyên nhân gì?",
        "Khi model trả rỗng, check gì trước?",
        "API 500 error thường do đâu?",
        "Làm sao debug khi RAG không trả kết quả?",
        "Khi queue bị nghẽn, xử lý thế nào?"][i % 5] for i in range(40)],
}

jobs = []
for cat, qs in categories.items():
    for idx, q in enumerate(qs, 1):
        payload = {
            "tenant_id": "bench",
            "project_id": "test",
            "user_name": f"bench_{cat}_{idx}",
            "user_message": q,
            "stream": False,
            "model_mode": "lite",
        }
        jobs.append((cat, idx, q, payload))

print(f"Total jobs: {len(jobs)} (20 categories × 40 questions)")

def post(job):
    cat, idx, q, payload = job
    t0 = time.perf_counter()
    req = urllib.request.Request(
        f"{BASE_URL}/v1/chat",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "X-API-KEY": API_KEY},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            body = r.read().decode(errors="replace")
            data = json.loads(body)
            lat = (time.perf_counter() - t0) * 1000
            return {
                "cat": cat, "idx": idx, "ok": True, "http": r.status,
                "latency_ms_client": round(lat, 1),
                "latency_ms_api": data.get("latency_ms"),
                "model": data.get("model"), "route": data.get("route"),
                "content_len": len(data.get("content") or ""),
                "preview": (data.get("content") or "")[:100],
            }
    except urllib.error.HTTPError as e:
        lat = (time.perf_counter() - t0) * 1000
        return {"cat": cat, "idx": idx, "ok": False, "http": e.code, "latency_ms_client": round(lat, 1), "error": e.read().decode(errors="replace")[:300]}
    except Exception as e:
        lat = (time.perf_counter() - t0) * 1000
        return {"cat": cat, "idx": idx, "ok": False, "http": None, "latency_ms_client": round(lat, 1), "error": f"{type(e).__name__}: {e}"}

start = time.perf_counter()
results = []
with cf.ThreadPoolExecutor(max_workers=CONCURRENCY) as ex:
    futs = [ex.submit(post, job) for job in jobs]
    for i, fut in enumerate(cf.as_completed(futs), 1):
        r = fut.result()
        results.append(r)
        if i % 100 == 0 or i == len(jobs):
            print(f"Progress: {i}/{len(jobs)} ok={sum(1 for x in results if x['ok'])} fail={sum(1 for x in results if not x['ok'])}", flush=True)

dur = time.perf_counter() - start
oks = [r for r in results if r["ok"]]
fails = [r for r in results if not r["ok"]]
lat = [r["latency_ms_client"] for r in oks]
by_cat = {}
for r in results:
    by_cat.setdefault(r["cat"], {"ok":0,"fail":0,"lat":[]})
    if r["ok"]:
        by_cat[r["cat"]]["ok"] += 1
        by_cat[r["cat"]]["lat"].append(r["latency_ms_client"])
    else:
        by_cat[r["cat"]]["fail"] += 1

summary = {
    "total": len(results), "ok": len(oks), "fail": len(fails),
    "success_rate": round(len(oks)/len(results)*100, 2),
    "duration_s": round(dur, 2), "throughput_rps": round(len(results)/dur, 3),
    "concurrency": CONCURRENCY,
    "latency_client_ms": {
        "avg": round(statistics.mean(lat), 1) if lat else None,
        "p50": round(statistics.median(lat), 1) if lat else None,
        "p95": round(sorted(lat)[int(len(lat)*0.95)-1], 1) if lat else None,
        "p99": round(sorted(lat)[int(len(lat)*0.99)-1], 1) if lat else None,
        "max": max(lat) if lat else None,
    },
    "by_category": {
        c: {"ok": v["ok"], "fail": v["fail"], "avg_ms": round(statistics.mean(v["lat"]),1) if v["lat"] else None, "p95_ms": round(sorted(v["lat"])[int(len(v["lat"])*0.95)-1],1) if v["lat"] else None}
        for c,v in sorted(by_cat.items())
    },
    "models": sorted(set(r.get("model") for r in oks if r.get("model"))),
    "route_reasons": {reason: sum(1 for r in oks if r.get("route_reason") == reason) for reason in sorted(set(r.get("route_reason") for r in oks if r.get("route_reason")))},
}
out = {"summary": summary, "results": sorted(results, key=lambda r: (r["cat"], r["idx"]))}
Path("/tmp/aihub_20x40_report.json").write_text(json.dumps(out, ensure_ascii=False, indent=2))

print(f"\nSUMMARY:")
print(f"  Total: {summary['total']} | OK: {summary['ok']} | Fail: {summary['fail']} | Rate: {summary['success_rate']}%")
print(f"  Duration: {summary['duration_s']}s | Throughput: {summary['throughput_rps']} req/s")
print(f"  Latency: avg={summary['latency_client_ms']['avg']}ms p50={summary['latency_client_ms']['p50']}ms p95={summary['latency_client_ms']['p95']}ms p99={summary['latency_client_ms']['p99']}ms max={summary['latency_client_ms']['max']}ms")
print(f"  Models: {summary['models']}")
print(f"  Route reasons: {summary['route_reasons']}")
print(f"\nBY CATEGORY:")
for c, v in summary['by_category'].items():
    print(f"  {c:25s} ok={v['ok']:2d} fail={v['fail']:2d} avg={v['avg_ms']}ms p95={v['p95_ms']}ms")
print(f"\nREPORT=/tmp/aihub_20x40_report.json")
