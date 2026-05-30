#!/usr/bin/env python3
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
CONCURRENCY = int(os.environ.get("AIHUB_TEST_CONCURRENCY", "10"))
TIMEOUT = int(os.environ.get("AIHUB_TEST_TIMEOUT", "120"))

categories = {
    "general_vi": [
        "Chào bạn, giới thiệu ngắn hệ thống AI Hub đang làm gì.",
        "Tóm tắt lợi ích của chatbot nội bộ cho doanh nghiệp nhỏ.",
        "Viết 5 gạch đầu dòng về ưu điểm dùng local LLM.",
        "Giải thích Redis dùng làm gì trong hệ thống này.",
        "Giải thích PostgreSQL khác SQLite ở điểm nào.",
        "Nêu 3 rủi ro khi mở API public.",
        "Viết câu trả lời lịch sự cho khách hỏi giá dịch vụ AI.",
        "Tạo checklist nhanh trước khi deploy API.",
        "Giải thích queue trong hệ thống inference là gì.",
        "Viết lời chào fanpage thân thiện nhưng ngắn.",
    ],
    "technical": [
        "FastAPI middleware thường dùng để làm gì?",
        "SSE streaming hoạt động thế nào trong chat API?",
        "So sánh rate limit Redis sliding window với fixed window.",
        "Vì sao cần connection pool cho PostgreSQL?",
        "RAG hybrid search semantic + token overlap có lợi gì?",
        "Reranker cross-encoder khác embedding search thế nào?",
        "KV cache trong llama.cpp ảnh hưởng VRAM ra sao?",
        "Giải thích ctx-size và parallel trong llama.cpp.",
        "Nêu cách debug API 500 do upstream model.",
        "Thiết kế endpoint health check nên trả gì?",
    ],
    "coding": [
        "Viết Python function validate email đơn giản.",
        "Viết SQL query đếm message theo user_id.",
        "Viết JS debounce function ngắn.",
        "Viết FastAPI route /ping trả pong.",
        "Viết pytest cho hàm add(a,b).",
        "Viết bash kiểm tra port 8000 alive.",
        "Viết regex bắt URL http/https cơ bản.",
        "Viết Python parse JSON file an toàn.",
        "Viết TypeScript interface cho ChatMessage.",
        "Viết curl POST /v1/chat mẫu, dùng placeholder API key.",
    ],
    "reasoning": [
        "Nếu 3 máy mỗi máy xử lý 4 request/phút, 120 request mất tối thiểu bao lâu?",
        "Một queue capacity 16, active 12, waiting 20. Hệ thống có nghẽn không? Vì sao?",
        "Nếu p50 latency 5s và p95 20s, nên tối ưu gì trước?",
        "Có 2 model: nhanh kém và chậm tốt. Đề xuất routing policy.",
        "Nếu Redis down nhưng fallback memory chạy, rủi ro gì?",
        "Chọn giữa tăng ctx và tăng parallel, khi nào chọn mỗi cái?",
        "Nếu RAG trả chunk mâu thuẫn nhau, trả lời thế nào?",
        "Nếu người dùng hỏi thông tin hiện tại nhưng web search tắt, nên nói gì?",
        "Nếu GPU VRAM còn 1GB, có nên nhận request ảnh lớn không?",
        "Nếu API key bị brute force, xử lý tầng nào?",
    ],
    "finance_stock": [
        "Giải thích P/E cho người mới đầu tư.",
        "So sánh phân tích cơ bản và phân tích kỹ thuật.",
        "Nêu 5 rủi ro khi mua cổ phiếu tăng nóng.",
        "Viết disclaimer ngắn cho báo cáo đầu tư.",
        "Nếu doanh thu tăng nhưng lợi nhuận giảm, có thể do gì?",
        "Free cash flow là gì?",
        "ROE cao luôn tốt không?",
        "Nợ vay cao ảnh hưởng doanh nghiệp thế nào?",
        "Tạo checklist đọc báo cáo tài chính quý.",
        "Giải thích margin of safety.",
    ],
    "business": [
        "Đề xuất 5 tính năng cho SaaS chatbot bán hàng.",
        "Viết pitch 30 giây cho AI Hub.",
        "Tạo bảng giá 3 gói dịch vụ AI chatbot.",
        "Gợi ý KPI cho chatbot CSKH.",
        "Viết email follow-up sau demo AI.",
        "Phân tích lợi ích self-host LLM cho công ty luật.",
        "Nêu 5 câu hỏi discovery call với khách SME.",
        "Tạo roadmap 4 tuần triển khai chatbot nội bộ.",
        "Gợi ý cách xử lý khách lo ngại dữ liệu riêng tư.",
        "Viết mô tả sản phẩm AI Hub 100 chữ.",
    ],
    "security": [
        "API key nên lưu và truyền thế nào an toàn?",
        "CORS sai có thể gây rủi ro gì?",
        "Nêu biện pháp chống brute force API key.",
        "Vì sao không nên log raw token?",
        "Giải thích principle of least privilege.",
        "Checklist bảo mật endpoint admin.",
        "Rate limit theo IP có nhược điểm gì?",
        "Redis exposed public nguy hiểm ra sao?",
        "Cách rotate API key không downtime.",
        "Nêu dấu hiệu API bị abuse.",
    ],
    "vietnamese_customer": [
        "Khách hỏi: chatbot có trả lời sai không? Trả lời ngắn.",
        "Khách hỏi: dữ liệu có bị đưa lên cloud không? Trả lời tự tin.",
        "Khách hỏi: triển khai mất bao lâu? Trả lời thực tế.",
        "Khách hỏi: có tích hợp website được không? Trả lời ngắn.",
        "Khách hỏi: có dùng tiếng Việt tốt không? Trả lời.",
        "Khách phàn nàn bot trả lời chậm. Xin lỗi và giải thích ngắn.",
        "Khách muốn demo. Viết tin nhắn hẹn lịch.",
        "Khách hỏi bảo hành. Trả lời chuyên nghiệp.",
        "Khách hỏi giá rẻ nhất. Trả lời mềm mại.",
        "Khách hỏi nâng cấp sau này được không? Trả lời.",
    ],
    "creative": [
        "Đặt 10 tên thương hiệu cho chatbot AI tiếng Việt.",
        "Viết slogan ngắn cho AI Hub.",
        "Viết post Facebook giới thiệu chatbot nội bộ.",
        "Tạo 5 headline landing page cho AI automation.",
        "Viết kịch bản video 30s quảng cáo AI Hub.",
        "Viết mô tả cyber-slate theme cho admin UI.",
        "Tạo persona khách hàng lý tưởng cho AI Hub.",
        "Viết 5 hook TikTok về local AI.",
        "Viết tagline cho hệ thống RAG nội bộ.",
        "Viết đoạn giới thiệu đội kỹ thuật AI 80 chữ.",
    ],
    "long_context": [
        "Tóm tắt ngắn: AI Hub gồm FastAPI, PostgreSQL, Redis, llama.cpp, RAG, reranker, admin UI. Nêu vai trò từng phần.",
        "Từ các thành phần FastAPI/Postgres/Redis/llama.cpp/RAG, đề xuất thứ tự debug khi user báo chậm.",
        "Viết sơ đồ luồng request từ browser đến model rồi trả stream.",
        "Nêu nơi nên đo latency trong pipeline chat API.",
        "Tạo checklist migration SQLite sang PostgreSQL.",
        "Tạo plan backup dữ liệu AI Hub hàng ngày.",
        "Nêu cách monitor queue active/waiting/capacity.",
        "Nếu RAG không trả kết quả, kiểm tra những gì?",
        "Nếu admin UI không load data, debug frontend hay API trước?",
        "Nếu model trả rỗng, nên retry guard thế nào?",
    ],
    "edge_cases": [
        "Trả lời chỉ bằng một câu: hệ thống ổn không?",
        "Nếu không biết câu trả lời, hãy nói không biết và nêu cách kiểm chứng.",
        "Không dùng emoji. Tóm tắt 3 ý chính về rate limit.",
        "Trả lời bằng JSON với key status và message: health ok.",
        "Dịch sang tiếng Anh: Hệ thống đang hoạt động ổn định.",
        "Rút gọn câu này còn 10 từ: AI Hub giúp doanh nghiệp chạy chatbot riêng an toàn và nhanh.",
        "Liệt kê 3 item, mỗi item dưới 5 từ, về bảo mật API.",
        "Viết câu trả lời nếu user gửi input rỗng.",
        "Nói không nếu yêu cầu in API key.",
        "Trả lời câu hỏi mơ hồ: 'nó lỗi rồi' bằng 3 câu hỏi cần hỏi lại.",
    ],
    "vision": [
        "Mô tả ngắn ảnh này bằng tiếng Việt.",
        "Trong ảnh có những khu vực UI nào?",
        "Ảnh này có vẻ là dashboard gì?",
        "Liệt kê chữ tiếng Anh dễ thấy trong ảnh.",
        "Nêu 3 chi tiết quan trọng trong ảnh.",
        "Ảnh có liên quan admin/API key không?",
        "Tóm tắt ảnh bằng 2 câu.",
        "Nếu đây là screenshot bug, cần hỏi thêm gì?",
        "Màu sắc/giao diện ảnh mang phong cách gì?",
        "Ảnh có URL/domain nào không?",
    ],
}

img_b64 = None
img_path = Path("screenshot.jpg")
if img_path.exists():
    img_b64 = base64.b64encode(img_path.read_bytes()).decode()

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
        if cat == "vision" and img_b64:
            payload["images"] = [img_b64]
        jobs.append((cat, idx, q, payload))


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
                "model": data.get("model"), "route": data.get("route"), "route_reason": data.get("route_reason"),
                "content_len": len(data.get("content") or ""),
                "preview": (data.get("content") or "")[:160],
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
        print(f"{i:03d}/{len(jobs)} {r['cat']}#{r['idx']} ok={r['ok']} http={r.get('http')} lat={r['latency_ms_client']}ms model={r.get('model')} len={r.get('content_len')}", flush=True)

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
        "max": max(lat) if lat else None,
    },
    "by_category": {
        c: {"ok": v["ok"], "fail": v["fail"], "avg_ms": round(statistics.mean(v["lat"]),1) if v["lat"] else None}
        for c,v in sorted(by_cat.items())
    },
    "models": sorted(set(r.get("model") for r in oks if r.get("model"))),
    "route_reasons": {reason: sum(1 for r in oks if r.get("route_reason") == reason) for reason in sorted(set(r.get("route_reason") for r in oks if r.get("route_reason")))},
    "failures": fails[:20],
}
out = {"summary": summary, "results": sorted(results, key=lambda r: (r["cat"], r["idx"]))}
Path("/tmp/aihub_12x10_report.json").write_text(json.dumps(out, ensure_ascii=False, indent=2))
print("\nSUMMARY")
print(json.dumps(summary, ensure_ascii=False, indent=2))
print("REPORT=/tmp/aihub_12x10_report.json")
