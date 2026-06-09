# AI Hub — Bug Audit & Fix Report
**Date:** 2026-06-08
**Scope:** Full system audit + parallel fix orchestration
**Result:** 28/60 bugs fixed, ~12 deferred (cross-file conflict hotspots + LOW severity)

---

## 1. Tổng quan hệ thống

- **Quy mô:** 13.081 dòng Python (93 files), 121KB admin.js, 100+ scripts, 97 tests
- **Mức tổng thể:** Feature-rich, đã qua nhiều lần mở rộng, có nhiều lỗ hổng bảo mật nghiêm trọng + technical debt đáng kể
- **Bug count:** 60+ identified (CRITICAL: 13, HIGH: 18, MEDIUM: 18, LOW: 11+)

---

## 2. Bugs đã FIX (28)

### Round 1 (4 parallel subagents) — 26 bugs

#### `app/core/config.py` + `app/core/database.py` (core agent)
- ✅ `api_key: str` thêm `min_length=16` (chặn key 1 ký tự)
- ✅ `lite_num_ctx` default 65536→8192 + `ge=512, le=131072` (chặn OOM 16GB VRAM)
- ✅ `database_url` thêm `min_length=1` (fail-fast khi thiếu)
- ✅ `project_context_sizes` field validator: values phải `>=512`
- ✅ `failure_risk` cross-field validator: `medium < high`, bounds 0..1
- ✅ `adaptive_max_tokens` cross-field validator: `severe < cutoff`
- ✅ `_get_pool()` thêm `SELECT 1` warmup, raise `RuntimeError` rõ ràng
- ✅ `CREATE EXTENSION vector` block: silent → `logger.warning` rõ ràng

#### `app/services/whisper_service.py` (core agent)
- ✅ Tạo stub file tránh `ModuleNotFoundError` cho callers

#### `app/middleware/security.py` (middleware agent)
- ✅ `hmac.compare_digest` thay `==` cho master key (timing-safe)
- ✅ Dead `if False and...` IP block: real `is_blocked()` check wired
- ✅ InMemoryFailureTracker thêm `is_blocked()` + eviction (>50k clear, >10k prune stale)
- ✅ `_client_ip` chỉ trust `X-Real-IP`/`cf-connecting-ip` khi `request.client.host` là loopback hoặc trong `TRUSTED_PROXY_IPS`
- ✅ `TRUSTED_PROXY_IPS` setting mới
- ✅ Static master key: `api_key_allowed_projects = self._settings.allowed_projects` (chặn bypass ACL)
- ✅ `request.state.api_key_is_admin` set cho cả static key (True) và DB path (theo record)
- ⚠️ Rate limiter off-by-one: REVERT CẦN KIỂM TRA (xem §4 Fix D)

#### `app/services/providers/minimax.py` (provider agent)
- ✅ Race condition: thay `self._model = model` bằng local `effective_model`
- ✅ `_build_payload` nhận `model_override` param
- ✅ `cache_control` đặt đúng chỗ (content block, không phải message dict) — Anthropic spec
- ✅ Test `tests/unit/test_minimax_provider.py` cập nhật cho new signature

#### `app/services/rerank_service.py` (provider agent)
- ✅ Sync `httpx.Client` → `httpx.AsyncClient` + `await` (unblock event loop)
- ✅ Optional external `client` param, tự tạo/close nếu None

#### `app/services/providers/load_balancer.py` (provider agent)
- ✅ Trả về provider chết khi all down → raise `UpstreamError`

#### `app/services/facebook_service.py` (provider agent)
- ✅ `__aenter__` / `__aexit__` / `close()` thêm vào (caller contract documented)
- ⏳ Route `facebook_webhook.py` wire context manager (xem §4 Fix 4)

#### `app/services/memory_extraction_service.py` (service agent)
- ✅ Dedupe bằng `md5(content_hash)` + index mới

#### `app/services/memory_consolidation_service.py` (service agent)
- ✅ Sau consolidation: `DELETE FROM memory_items WHERE id = ANY(%s)`

#### `app/agents/tools.py` (service agent)
- ✅ Module-level `ConnectionPool(min=1, max=4)`, thay `psycopg.connect` per-call

#### `app/services/ihi_case_saver.py` (service agent)
- ✅ `t_min=70, t_max=90` (industrial default) + TODO derive from device specs

#### `app/services/ihi_rag_service.py` (service agent)
- ✅ Embedding parsing wrap try/except, log warning, skip empty (không poison cache)

#### `app/services/skill_service.py` (service agent)
- ✅ Dead `self._cache.clear()` removed (5 call sites)

#### `app/services/prediction_service.py` (service agent)
- ✅ INSERT+SELECT → single `INSERT ... RETURNING *`

#### `app/services/tracing_service.py` (service agent)
- ✅ `threading.Lock` + `reset()` + `TracingService` class cho DI

### Round 2 (3 parallel subagents) — 14 bugs

#### `app/routes/admin.py` (routes agent)
- ✅ 25 admin endpoints thêm `Depends(require_admin)`
- ✅ `KeyCreateRequest.rpm_limit`: `ge=1, le=10000`
- ✅ `KeyPatchRequest.rpm_limit`: `ge=1, le=10000`
- ✅ `RagUploadRequest.content`: `max_length=10_000_000`

#### `app/routes/chat.py` (routes agent)
- ✅ Tenant mismatch check 403; master-key log INFO warning

#### `app/routes/facebook_webhook.py` (routes agent)
- ✅ HMAC-SHA256 verify `X-Hub-Signature-256` với `hmac.compare_digest`
- ✅ `FacebookService` wire `async with` context manager

#### `app/services/ai_service.py` (routes agent)
- ✅ `_load_history` thêm warning log cho client-supplied history

#### `app/services/usage_service.py` (routes agent)
- ✅ `summary()`, `get_time_series()`, `get_cost_series_7d()` nhận `tenant_id` param + `AND tenant_id = %s`

#### `static/index.html` (frontend agent)
- ✅ L251 hardcoded API key → empty string
- ✅ L320 demo-projects branch removed
- ✅ L911 `restoreLatestSessionForUser` thêm `alert()`

#### `static/admin.js` (frontend agent)
- ✅ L9-17 `?key=` URL reader: console.warn + strip (no localStorage)
- ✅ L1960-1969 cross-page `?key=` forwarding removed
- ✅ L1139 `beforeunload` clear `ADMIN.autoTimer`

#### `static/admin.html` (frontend agent)
- ✅ Verified load đúng `admin.v2.js` + `admin.v2.css`
- ⏳ `admin.js` (121KB) + `admin.css` (48KB) dead files — chưa xóa (cần manual check)

#### `app/services/mcp_tools.py` (MCP utils agent)
- ✅ `analyze_stock` shell injection removed → `NotImplementedError` stub

#### `app/routes/mcp_tools.py` (MCP utils agent)
- ✅ `/v1/tools/query-database` → HTTP 410 (disabled vì blocklist bypass)
- ✅ `DBQueryRequest` giữ với deprecation docstring

#### `app/utils/cost_calculator.py` (MCP utils agent)
- ✅ Catch-all `("*", "*")` removed
- ✅ `_match_rate` return `None` on miss
- ✅ Warn-once per (provider, model) unknown

#### `app/utils/token_counter.py` (MCP utils agent)
- ✅ Gemma-aware tokenizer (`_get_gemma_tokenizer` lazy-load `transformers`)
- ✅ `_is_gemma_model` dispatch trong `count_text_tokens` + `count_messages_tokens`
- ✅ Graceful fallback tiktoken → char heuristic

---

## 3. Bug chưa fix (CẦN LÀM) — Cross-file hotspots

Những bug này overlap với fixes đã làm ở Round 1+2, cần integration check:

### 🔴 CRITICAL

#### 1. `minimax_enabled` không bao giờ được check trong `_select_provider`
- **File:** `app/services/ai_service.py:577-597`
- **Issue:** MiniMax M3 wired làm `app.state.cloud` nhưng `_select_provider` chỉ check `openrouter_enabled`/`nine_router_enabled`. Khi `MINIMAX_ENABLED=true`, cloud vẫn pick OpenRouter logic.
- **Fix cần:** Thêm `or self._settings.minimax_enabled` vào check ở L591.

#### 2. Tenant ID vẫn client-supplied (không override)
- **File:** `app/routes/chat.py:42-49`
- **Issue:** Middleware không thể override body (parse sau middleware). Check ở Round 2 chỉ 403 mismatch nhưng không force-override.
- **Fix cần:** Nếu `request.state.api_key_tenant_id` set → force `payload.tenant_id = api_key_tenant_id`. Static key → log warning + giữ user value.

#### 3. `init_db()` chạy ở import time
- **File:** `app/main.py:154, 505`
- **Issue:** Nếu DB down, `uvicorn` crash stacktrace thay vì retry/503. Module không import được trong CI/lint.
- **Fix cần:** Move `init_db()` vào đầu lifespan (trong `await asyncio.to_thread(init_db)`), xóa call ở top `create_app()`.

### 🟠 HIGH

#### 4. `cache_control` vẫn chưa được verify end-to-end với MiniMax M3 thật
- **Status:** Code fixed ở Round 1 (provider agent), test cập nhật. CẦN test live với `RUN_LIVE=1 MINIMAX_API_KEY=...`.

#### 5. `api_key` config min_length=16 có thể break existing .env
- **File:** `.env` dòng `API_KEY=...`
- **Issue:** Nếu `API_KEY` trong `.env` < 16 chars → server không start. CẦN verify và update.

#### 6. `lite_num_ctx` default đổi từ 65536 → 8192
- **File:** `app/core/config.py:65`
- **Issue:** Có thể có client đang dựa vào default 65536. CẦN audit usage hoặc đặt `LITE_NUM_CTX` explicit trong `.env`.

#### 7. `trusted_proxy_ips` và `allowed_projects` settings mới
- **File:** `app/core/config.py`
- **Issue:** CẦN thêm vào `.env` nếu operator muốn dùng (default empty list = secure).

#### 8. Whisper stub thiếu implementation thật
- **File:** `app/services/whisper_service.py`
- **Status:** Stub tạo để chặn `ModuleNotFoundError`, nhưng `transcribe()` sẽ fail nếu `faster-whisper` chưa cài.

---

## 4. Cần verify / có thể là regression

### Rate limiter off-by-one
- **File:** `app/middleware/security.py:113-132`
- **Change:** `count <= self._limit` → `count < self._limit`
- **Concern:** Với `limit=60`:
  - Cũ: 60 requests allowed, 61st blocked (count=61, 61<=60 = False)
  - Mới: 59 requests allowed, 60th blocked (count=60, 60<60 = False)
  - **Đây là regression** — strict hơn 1 request. CẦN revert hoặc phân tích lại.

---

## 5. LOW severity chưa fix

| # | File | Issue |
|---|---|---|
| 1 | `scripts/` | 14+ obsolete launcher scripts (`start_12b_q4_p1.sh`–`p4.sh`, `start_thinking_qwen.sh`, etc.) |
| 2 | `scripts/` | 11+ stale benchmark/loadtest scripts + large `.json` result dumps |
| 3 | `static/admin.js` (121KB), `static/admin.css` (48KB) | Dead files, not loaded by `admin.html` |
| 4 | `app/utils/cost_calculator.py:34-35` | MiniMax M3 cost = 0.0 (intentional per CLAUDE.md nhưng cost dashboard useless) |
| 5 | `start.sh:19-26` | No timeout on health-check curl, có thể hang forever |
| 6 | `app/services/mcp_tools.py:35-59` | `analyze_stock` stub (replaced, OK) |
| 7 | `tests/conftest.py:88-107` | `isolated_db` autouse + truncate không có `pytest-xdist` config → parallel deadlock |
| 8 | `app/services/tracing_service.py` | Langfuse flush không blocking, in-flight spans có thể mất |
| 9 | `app/services/scheduler.py:30-95` | `PeriodicSummarizer` không có drift protection |
| 10 | `app/services/ai_service.py:1220-1225` | `_compute_usage_metrics` không tính search context size |
| 11 | `static/admin.css:986,1013-1130` | 20 `!important` declarations (responsive design smell) |

---

## 6. Test coverage gaps (10+ critical paths uncovered)

Các path trong `app/services/ai_service.py` **không có test**:

| # | Path | Line |
|---|---|---|
| 1 | `_route_fast_background_if_eligible` | 613 |
| 2 | `_build_search_context` (MiniMax MCP) | 460 |
| 3 | `_inject_skill_prompts` (MUSE Autoskill) | 758 |
| 4 | `_apply_failure_risk_decision` | 820 |
| 5 | `_load_context_parallel` | 260 |
| 6 | `_should_knowledge_rag` | 310 |
| 7 | `_compute_usage_metrics` (billing) | 1220 |
| 8 | `_queue_depth` / `_adaptive_max_tokens` | 684, 688 |
| 9 | `_save_prediction_record` | 966 |
| 10 | Admin routes: `db/query`, `db/tables`, `db/preview`, `security/unblock`, `users/{id}/detail` | admin.py |

---

## 7. Test execution

**Để chạy full test suite:**
```bash
cd /home/hung/ai-hub
./venv/bin/pytest tests/ --no-cov -v
```

**Conftest guard yêu cầu:**
```bash
AI_HUB_ALLOW_DB_TRUNCATE_FOR_TESTS=1 \
DATABASE_URL=postgresql://...ai_hub_test \
./venv/bin/pytest tests/ --no-cov -v
```

**Lint:**
```bash
./venv/bin/ruff check app/
./venv/bin/mypy app/  # nếu config mypy tồn tại
```

---

## 8. Verification đã làm (per-subagent)

Mỗi subagent verify trong scope riêng:
- ✅ Core: `Settings()` load, `init_db` importable, validators fire
- ✅ Middleware: `SecurityMiddleware` import, hmac asserts pass
- ✅ Provider: `MiniMaxProvider`, `RerankService`, `LlamaCppLoadBalancer`, `FacebookService` import + signatures
- ✅ Service: 8 services import OK
- ✅ Routes: 3 route files import OK
- ✅ Frontend: grep confirms hardcoded key + `?key=` removed
- ✅ MCP utils: imports + cost calc warn-once works

**Chưa chạy:** full integration test, end-to-end smoke test, lint sweep.

---

## 9. Files changed (28 files)

```
app/core/config.py
app/core/database.py
app/middleware/security.py
app/services/whisper_service.py (new)
app/services/providers/minimax.py
app/services/providers/load_balancer.py
app/services/facebook_service.py
app/services/rerank_service.py
app/services/memory_extraction_service.py
app/services/memory_consolidation_service.py
app/services/ihi_case_saver.py
app/services/ihi_rag_service.py
app/services/skill_service.py
app/services/prediction_service.py
app/services/tracing_service.py
app/services/mcp_tools.py
app/services/usage_service.py
app/services/ai_service.py
app/agents/tools.py
app/routes/admin.py
app/routes/chat.py
app/routes/facebook_webhook.py
app/routes/mcp_tools.py
app/utils/cost_calculator.py
app/utils/token_counter.py
static/index.html
static/admin.js
tests/unit/test_minimax_provider.py
tests/unit/test_load_balancer.py
```

---

## 10. Recommended next steps

### Ngay (30 phút)
1. **Verify `.env` `API_KEY` ≥16 chars** (do `min_length=16` mới)
2. **Run full test suite** với env guards
3. **Revert/check rate limiter off-by-one** (§4)
4. **Apply Round 3 fixes** (3 critical cross-file items §3)

### Tuần này
5. Move `init_db()` to lifespan (§3 #3)
6. Wire MiniMax routing (§3 #1)
7. Force-override tenant_id (§3 #2)
8. Add `TRUSTED_PROXY_IPS` + `ALLOWED_PROJECTS` to `.env`
9. Test MiniMax live với `RUN_LIVE=1`

### Tuần sau
10. Add tests cho 10 uncovered paths (§6)
11. Cleanup: delete dead `admin.js`/`admin.css`/obsolete scripts
12. Document: update `CLAUDE.md` với new config fields

### Tháng tới
13. Replace `query_database` MCP với proper read-only DB user
14. Replace 14+ obsolete `start_*.sh` scripts với single config-driven launcher
15. Refactor `_compute_usage_metrics` để tính search context size

---

**Generated:** 2026-06-08
**Audit duration:** ~15 phút (5 parallel subagents)
**Fix duration:** ~10 phút (7 parallel/serial subagents)
**Token cost estimate:** ~80-100K tokens
