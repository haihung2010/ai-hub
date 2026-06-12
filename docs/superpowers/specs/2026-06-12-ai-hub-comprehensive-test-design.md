# AI Hub Comprehensive 30-Minute Test — Design

**Date:** 2026-06-12
**Status:** Approved
**Author:** Brainstorming session with user
**Related:** `scripts/memory_stress_test.py`, `scripts/loadtest.py`, `scripts/perf_test_20users.py`

---

## 1. Background & Motivation

ai-hub là central router phục vụ chatbot multi-tenant với:
- 3 loại memory (Summary rolling, StructMem SPO, Pinned)
- RAG (hybrid search + reranker)
- Multi-tenant isolation (Postgres RLS)
- Cloud + local provider fallback

Hiện có sẵn các test scripts:
- `memory_stress_test.py` (1086 LOC) — 10 user × 50 câu/user, topic cố định
- `loadtest.py` (335 LOC) — continuous multi-tenant
- `perf_test_20users.py` (539 LOC) — 20 user × 5 turn

**Vấn đề:** Không có script nào cover được toàn bộ 4 dimensions cùng lúc (functional + memory load + memory persistence + multi-tenant isolation), và không có test cho same-topic cache speedup. Khi nghi ngờ memory bị lỗi sau 1 thời gian dùng → không có công cụ reproduce.

**Mục tiêu:** Tạo 1 script test tổng hợp chạy trong 30 phút, mô phỏng user e-commerce quần áo thật, đo throughput + memory recall + cache speedup, output JSON report với pass/fail.

---

## 2. Architecture

```
test_comprehensive_30min.py (single file, ~700 LOC, asyncio + aiohttp)
├── SETUP (5 min)
│   ├── health_check() → verify port 8000 (ai-hub) + 8080 (llama.cpp)
│   ├── seed_knowledge_cards() → POST /v1/admin/knowledge/upload × 50-100
│   └── build_topic_bank() + load_user_personas()
│
├── PHASE 1: Warmup (3 min)
│   └── 10 personas × 10 câu = 100 turns, gather baseline latency
│
├── PHASE 2: Rotate (15 min)
│   ├── 100 user instances (10 personas × 10 lần reuse)
│   ├── 10 user concurrent × 10 câu → next 10 → ... → 100 user
│   ├── 5 chủ đề lặp lại bởi ≥3 user khác nhau → đo same-topic speedup
│   └── Output: latency distribution per topic, cache hit curve
│
├── PHASE 3: Memory Recall + Continue (7 min)
│   ├── Round 1: 10 user (1 per persona) → wait 2-3 min → memory check → 10 q cont.
│   ├── Round 2: 10 user tiếp theo (instance _02) → wait 1 min → repeat
│   ├── Round 3 (nếu còn time): instance _03, repeat
│   ├── Max 3 rounds = 30 user, time guard dừng nếu vượt budget
│   ├── Per round: "Bạn còn nhớ tôi đã hỏi gì?" → check ≥70% key facts
│   └── Output: memory recall accuracy % per user + per round
│
└── REPORT (30s)
    ├── JSON: reports/comprehensive_30min_<ts>.json
    ├── Console: pass/fail summary
    └── Log: reports/comprehensive_30min_<ts>.log (full request/response per error)
```

**Concurrency model:**
- Phase 1: 10 concurrent (1 persona = 1 user, 10 questions tuần tự)
- Phase 2: 10 concurrent (round-robin 100 user, mỗi user 10 q tuần tự)
- Phase 3: 10 concurrent (return to first 10, continue, rotate)
- Local 12B Q4 16GB tuned = parallel=4 slots → throttle xuống 4 concurrent MAX

---

## 3. Components

| Component | Responsibility | LOC |
|---|---|---|
| `Config` | env vars, timeouts, thresholds, persona list, topic bank seed | ~80 |
| `KnowledgeSeeder` | generate 50-100 cards (clothing products + FAQ) → upload admin endpoint | ~100 |
| `TopicBank` | 30 chủ đề × 10 câu = 300 câu hỏi, có 5 chủ đề overlap cho cache test | ~120 |
| `UserPersona` | 10 personas (An, Bình, Chi, Dũng, Em, Phương, Giang, Hà, Khánh, Linh) + suffix generator | ~30 |
| `MetricsCollector` | thread-safe in-memory, flush JSON mỗi phase, final aggregation | ~80 |
| `PhaseRunner` | 3 phases với concurrency control + abort on error spike + time guard | ~150 |
| `ReportGenerator` | p50/p95/p99, cache speedup curve, memory recall %, pass/fail | ~100 |
| `main()` | orchestrate setup → phases → report | ~40 |

**Total: ~700 LOC, 1 file `scripts/test_comprehensive_30min.py`**

---

## 4. Data flow

```
1. health_check()
   ├── GET /health → expect 200, status=ok
   └── GET localhost:8080/health → expect 200 (llama.cpp)

2. seed_knowledge_cards(n=75)
   ├── Build cards in-memory:
   │   ├── 50 product cards (áo thun, quần jean, váy, giày, phụ kiện)
   │   │   mỗi card: {title, content (200-500 chars), domain: "clothing", tags}
   │   └── 25 FAQ cards (đổi trả, size chart, shipping, bảo hành, payment)
   └── POST /v1/admin/knowledge/upload × 75 (sequential, ~1-2s/card)

3. phase1_warmup()
   ├── 10 personas × 10 câu = 100 turns
   ├── Concurrency: 4 (match llama.cpp parallel=4)
   ├── Per turn: POST /v1/chat {user, message, model_mode: "lite"}
   └── Record: {user, turn, topic, latency_ms, tokens_in, tokens_out, status}

4. phase2_rotate()
   ├── Build 100 user instances: persona + "_" + str(i).zfill(2)
   ├── 5 chủ đề cache-test: "áo thun trắng", "quần jean xanh", "giày thể thao", "váy maxi", "túi xách"
   ├── Chia user thành 10 group × 10 user. Mỗi group:
   │   ├── 5 user đầu hỏi cache topic (lần 1)
   │   ├── 5 user sau hỏi topic khác
   │   ├── Trong 100 user, 5 cache topic xuất hiện ≥3 lần
   │   └── Đo latency: turn-1 vs turn-2 vs turn-3 cho cùng topic
   └── Concurrency: 4 (round-robin)

5. phase3_recall()
   ├── Chọn 10 user từ phase 1 (1 user từ mỗi persona) → "round 1"
   ├── sleep(120-180s) — wait cho memory consolidation trigger (SUMMARY_THRESHOLD=8)
   ├── Hỏi memory check: "Bạn còn nhớ tôi đã hỏi gì trong cuộc trò chuyện trước không?"
   ├── Parse response → check key_facts (substring match + LLM judge fallback)
   ├── Continue 10 câu nữa cho mỗi user (mixed topics, bao gồm 1-2 cache topic)
   ├── Round 2: chọn 10 user tiếp theo (instance _02 của mỗi persona), repeat (wait 60s, memory check, 10 câu)
   ├── Round 3 (nếu còn thời gian): instance _03, repeat
   ├── Tối đa 3 rounds = 30 user (time guard dừng nếu quá 7 min budget)
   └── Output: {user, round, recall_pct, follow_up_latency_avg, errors}

6. generate_report()
   ├── Aggregate:
   │   ├── Total turns, total time, throughput (turns/min)
   │   ├── Latency: p50, p95, p99 per phase + overall
   │   ├── Error rate: 5xx / timeouts / connection errors
   │   ├── Memory recall: % key facts correctly recalled per user → avg
   │   └── Cache speedup: (latency_turn1 - latency_turnN) / latency_turn1 per topic
   ├── Pass/fail against criteria (section 7)
   └── Write JSON + log + console summary
```

---

## 5. Topic bank + personas (concrete samples)

### User personas (10)
```
An (nữ, 25, thích váy), Bình (nam, 30, thích áo sơ mi), Chi (nữ, 22, sinh viên, thời trang giá rẻ),
Dũng (nam, 35, công sở), Em (nữ, 28, mẹ bỉm sữa), Phương (nữ, 26, dân văn phòng),
Giang (nam, 24, sinh viên IT), Hà (nữ, 32, doanh nhân), Khánh (nam, 28, freelance),
Linh (nữ, 27, giáo viên)
```

### Topic bank (30 chủ đề, mỗi cái 10 câu = 300 câu)
**Categories:**
- Áo: thun, sơ mi, khoác, len, polo, blazer, hoodie, tank top, áo dài, áo vest
- Quần: jean, tây, short, jogger, legging, culottes, quần kaki, quần lót, quần yếm, quần baggy
- Váy: dài, ngắn, maxi, công sở, dạ hội, chữ A, xòe, body, yếm, tennis
- Giày: thể thao, tây, sandal, cao gót, boots, sneakers, oxford, loafer, dép, slip-on
- Phụ kiện: túi xách, mũ, thắt lưng, kính, trang sức, khăn, găng tay, tất, ví, dây chuyền
- FAQ: đổi trả, size chart, shipping, bảo hành, payment, tracking, sale events, loyalty, vận chuyển quốc tế, hỗ trợ

**Sample topic — "áo thun trắng":**
```
Q1: "Có áo thun trắng nào không?"              key_fact: ["có"]
Q2: "Size nào đang có sẵn?"                    key_fact: ["S", "M", "L"]
Q3: "Giá bao nhiêu?"                           key_fact: ["250000", "250k"]
Q4: "Chất liệu vải gì?"                        key_fact: ["cotton 100%"]
Q5: "Có thể giặt máy không?"                   key_fact: ["có", "40 độ"]
Q6: "Có mấy màu khác không?"                   key_fact: ["đen", "xám", "xanh"]
Q7: "Giao hàng mất bao lâu?"                   key_fact: ["2-3 ngày"]
Q8: "Có freeship không?"                        key_fact: ["đơn từ 300k"]
Q9: "Có thể đổi trả không?"                    key_fact: ["7 ngày"]
Q10: "Có thể in logo lên áo không?"            key_fact: ["có", "+50000"]
```

### 5 chủ đề cache-test (xuất hiện ≥3 lần trong 100 user)
1. "áo thun trắng" — 5 lần
2. "quần jean xanh" — 4 lần
3. "giày thể thao" — 3 lần
4. "váy maxi hoa" — 3 lần
5. "túi xách da" — 3 lần

---

## 6. Metrics collected

### Per request
```json
{
  "user": "stress_user_chi_03",
  "turn": 7,
  "topic": "áo thun trắng",
  "latency_ms": 4250,
  "tokens_in": 1024,
  "tokens_out": 156,
  "status": 200,
  "error": null,
  "timestamp": "2026-06-12T15:23:45Z",
  "phase": "phase2_rotate"
}
```

### Per user (aggregated)
```json
{
  "user": "stress_user_chi_03",
  "total_turns": 30,
  "errors": 0,
  "avg_latency_ms": 4100,
  "p95_latency_ms": 6800,
  "topics_asked": ["áo thun trắng", "quần jean xanh", ...]
}
```

### Per topic (cache speedup)
```json
{
  "topic": "áo thun trắng",
  "occurrences": 5,
  "turn1_latency_ms": 4400,
  "turn2_latency_ms": 4150,
  "turn3_latency_ms": 4080,
  "speedup_pct": 7.3
}
```

### Memory recall
```json
{
  "user": "stress_user_chi_01",
  "key_facts_asked": 12,
  "key_facts_recalled": 9,
  "recall_pct": 75.0,
  "missed_facts": ["giặt máy 40 độ", "freeship 300k", "in logo +50k"]
}
```

---

## 7. Pass/Fail criteria

| Metric | Threshold | Action if fail |
|---|---|---|
| **Error rate** | ≤5% requests có status ≥500 hoặc timeout | collect full request/response per error, ghi log, suggest fix |
| **p95 latency** | observe baseline (không fail), warn nếu >2× median | flag, không fail |
| **Memory recall** | ≥70% key facts được nhắc lại đúng | log misses với detail, suggest memory config tweak |
| **Same-topic speedup** | ≥10% reduction (turn N vs turn 1) | log ratio, không fail (có thể local 12B không có cache) |
| **Total runtime** | <35 min (5 min buffer) | warn, dừng phase 2/3 sớm |

**Pass:** error_rate ≤5% AND memory_recall ≥70%
**Soft pass:** error_rate ≤5% AND memory_recall 50-70% (log improvement suggestions)
**Fail:** error_rate >5% OR memory_recall <50% OR total runtime >40 min

---

## 8. Error handling

- **Per request:** timeout 60s, retry 0 (đo real-world reliability)
- **HTTP 5xx / timeout / connection error:** log full request + response (không PII), increment error counter
- **Error rate >5% trong 1 phase:** continue nhưng flag phase đó, summary sẽ highlight
- **ai-hub crash:** poll /health mỗi 5s trong phase transitions, abort nếu down
- **Knowledge seed fail:** log + abort setup (không run phases nếu KB không ready)
- **Memory recall parse fail:** nếu response trống hoặc 0 chars → treat như error, không recurse

### Top errors report
Cuối mỗi phase, generate "Top 10 errors" table:
```
{error_type: "timeout", count: 23, example_request: ..., example_response: ...}
{error_type: "500_internal", count: 5, example_request: ..., example_response: ...}
```

---

## 9. Out of scope (YAGNI)

- ❌ Multi-modal (image input) — user chỉ mention text chat
- ❌ Admin endpoint test — đã có `test_admin_comprehensive.py`
- ❌ Web UI / admin UI test — backend only
- ❌ Load >20 concurrent — local 12B Q4 16GB maxes at 4 slots, overkill
- ❌ Test các provider khác (cloud M3) — user explicitly tắt MiniMax
- ❌ RAG scale >100 cards — overkill cho memory test
- ❌ Long-running (>30 min) — user explicitly 30 min

---

## 10. Implementation summary

| Step | Action | Time |
|---|---|---|
| 1 | Start ai-hub (full stack, disable MiniMax) | 3 min |
| 2 | Verify health, generate + seed 75 knowledge cards | 5 min |
| 3 | Run `python scripts/test_comprehensive_30min.py` | 30 min |
| 4 | Read JSON report + console summary | 2 min |
| 5 | If fail → triage top errors, decide next step | TBD |

**Pre-implementation prerequisites:**
- [ ] ai-hub stopped (currently down, clean start OK)
- [ ] MiniMax disabled in .env: `MINIMAX_ENABLED=false`
- [ ] GPU free (no other llama.cpp process running on 8080)
- [ ] Postgres + Redis running natively (already true)
- [ ] ~5GB free disk for reports

**Post-implementation cleanup:**
- [ ] Stop test ai-hub
- [ ] Optionally remove seeded knowledge cards (admin endpoint or SQL)
- [ ] Archive report to `reports/2026-06-12-comprehensive-30min/`

---

## 11. Open questions

None — all clarified during brainstorming. Key decisions:
- Provider: local 12B Q4 only (tắt MiniMax)
- KB seed: lớn (50-100 cards)
- Pattern: 10 user concurrent × 10 câu rotate, 100 user total
- Success criteria: error ≤5%, recall ≥70%, latency + speedup observe
