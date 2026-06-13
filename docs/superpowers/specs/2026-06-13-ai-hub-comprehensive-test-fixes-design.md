# AI Hub Comprehensive Test Fixes — Design

**Date:** 2026-06-13
**Status:** Approved
**Author:** Brainstorming session with user (follow-up to 2026-06-12 test)
**Related:**
- `docs/superpowers/specs/2026-06-12-ai-hub-comprehensive-test-design.md`
- `docs/superpowers/plans/2026-06-12-ai-hub-comprehensive-test.md`
- `reports/2026-06-12-comprehensive-30min/comprehensive_30min_20260613-000722.json`

---

## 1. Background & Motivation

Test 30 phút ngày 2026-06-12 (verdict=FAIL) phát hiện 4 vấn đề trong ai-hub:

| # | Vấn đề | Root cause | Impact |
|---|---|---|---|
| 1 | 40/1430 context overflow (2.8% error) | E2B Q4 background parallel=16 → 2048 ctx/slot quá chật cho history 14 msgs | Tất cả user, 100% conversation dài |
| 2 | Memory recall 25.7% (target 70%) | `ENABLE_STRUCTMEM=false`, chỉ chạy SummaryService (mất specific facts) | Tất cả user, persist after gap |
| 3 | Cache speedup -310% đến +12.5% (mixed) | Không có cross-request cache, repeat topics chậm hơn do contention | Multi-user load |
| 4 | Test runtime 56 min (target 30) | `total_runtime_cap_seconds=2100` defined in Config nhưng KHÔNG enforce | Dev experience, CI/CD |

**Mục tiêu:** Fix all 4 issues trong 2-3 sprints, đạt 4 success criteria (zero context overflow, recall ≥50%, cache speedup ≥10%, test runtime ≤35 min).

---

## 2. Architecture

```
┌──────────────────────────────────────────────────────────────┐
│  Fix layers (4 độc lập, có thể ship từng sprint)             │
│                                                              │
│  ┌─ Sprint 1: Fix 1 (E2B config) ──────────────────────────┐  │
│  │  scripts/start_background_q4.sh                        │  │
│  │    PARALLEL: 16 → 4                                   │  │
│  │    → 32768/4 = 8192 tokens/slot (was 2048)            │  │
│  │    → No code change, just env override                │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                              │
│  ┌─ Sprint 1: Fix 4 (test infra) ──────────────────────────┐  │
│  │  scripts/test_comprehensive_30min.py                   │  │
│  │    + time_guard() function (check elapsed vs cap)     │  │
│  │    + PhaseResult.skipped: bool                        │  │
│  │    + main() respects guard, logs skipped phase        │  │
│  │    + Default phase2_users_total: 100 → 50            │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                              │
│  ┌─ Sprint 2: Fix 2 (memory) ─────────────────────────────┐  │
│  │  .env:                                                 │  │
│  │    ENABLE_STRUCTMEM: false → true                     │  │
│  │    STRUCTMEM_EXTRACTION_THRESHOLD: 16 → 8             │  │
│  │  + Manual verify: StructMem vs Summary mutual excl.  │  │
│  │  + Re-run test, measure recall                        │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                              │
│  ┌─ Sprint 3: Fix 3 (cache) ──────────────────────────────┐  │
│  │  New: app/services/cache_service.py                   │  │
│  │    + Redis-backed (auto-fallback in-memory)           │  │
│  │    + Key = sha256(user_id + ":" + message)            │  │
│  │    + TTL = 3600s (clothing Q&A stable)                │  │
│  │  Modified: app/services/ai_service.py                │  │
│  │    + Cache check before LLM call                     │  │
│  │    + Cache write after LLM response                  │  │
│  │  Modified: app/core/config.py                        │  │
│  │    + Cache enabled flag + TTL + size limit           │  │
│  │  New: tests/integration/test_cache_service.py        │  │
│  └────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────┘
```

---

## 3. Components

### Fix 1: Background model config

**File:** `scripts/start_background_q4.sh`

**Change:** Default `PARALLEL=16` → `PARALLEL=4` (allow override via env)

**Why:** 32768 ctx / 4 parallel = 8192 tokens/slot (was 2048). Long history (14 msgs × ~150 tokens) = ~2100 tokens, fits in 8192 with room for system prompt + response.

**Trade-off:** Less concurrent slots (4 vs 16). But quality > concurrency for this workload.

### Fix 2: StructMem enable

**File:** `.env`

**Changes:**
- `ENABLE_STRUCTMEM: false → true`
- `STRUCTMEM_EXTRACTION_THRESHOLD: 16 → 8`

**Why:** StructMem extracts SPO (Subject-Predicate-Object) triples that preserve specific facts (price, size, material). Better than SummaryService's abstract summary for fact recall.

**Trade-off:** StructMem and SummaryService are mutually exclusive per CLAUDE.md. Enable StructMem → Summary disabled for same conversation. Need to verify which is better for our use case (e-commerce Q&A).

**Verification step:** After enabling, check via SQL:
```sql
SELECT user_id, structmem_enabled FROM conversations WHERE tenant_id='default' LIMIT 5;
```

### Fix 3: Application-level response cache

**New file:** `app/services/cache_service.py` (~150 LOC)

```python
class CacheService:
    """Redis-backed chat response cache with in-memory fallback.

    Key: sha256(user_id + ":" + user_message)
    Value: serialized chat response (JSON)
    TTL: 3600s (configurable)
    """

    def __init__(self, redis_url: str | None, ttl_seconds: int = 3600, max_size_mb: int = 100):
        self.ttl = ttl_seconds
        self._redis: redis.Redis | None = None
        self._memory_cache: dict[str, tuple[float, str]] = {}  # key -> (expires_at, value)
        self._max_size_bytes = max_size_mb * 1024 * 1024
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0
        if redis_url:
            try:
                self._redis = redis.from_url(redis_url, decode_responses=True)
                self._redis.ping()
            except Exception:
                logger.warning("Cache: Redis unavailable, using in-memory fallback")
                self._redis = None

    def get(self, key: str) -> str | None:
        """Returns cached value or None. Auto-fallback to memory if Redis down."""
        if self._redis:
            try:
                val = self._redis.get(key)
                if val:
                    self.hits += 1
                    return val
                self.misses += 1
                return None
            except Exception:
                self._redis = None  # disable Redis, use memory
        # in-memory fallback
        with self._lock:
            entry = self._memory_cache.get(key)
            if entry and entry[0] > time.time():
                self.hits += 1
                return entry[1]
            self.misses += 1
            return None

    def set(self, key: str, value: str) -> None:
        """Store value with TTL."""
        if self._redis:
            try:
                self._redis.setex(key, self.ttl, value)
                return
            except Exception:
                self._redis = None
        with self._lock:
            self._evict_if_needed(len(value))
            self._memory_cache[key] = (time.time() + self.ttl, value)

    @staticmethod
    def hash_key(user_id: str, message: str) -> str:
        return hashlib.sha256(f"{user_id}:{message}".encode()).hexdigest()

    def _evict_if_needed(self, new_entry_size: int) -> None:
        """LRU-style eviction when total size exceeds limit."""
        with self._lock:
            total = sum(len(v) for _, v in self._memory_cache.values()) + new_entry_size
            if total <= self._max_size_bytes:
                return
            # Evict oldest 20%
            sorted_keys = sorted(self._memory_cache.items(), key=lambda kv: kv[1][0])
            evict_count = max(1, len(sorted_keys) // 5)
            for k, _ in sorted_keys[:evict_count]:
                del self._memory_cache[k]

    def metrics(self) -> dict:
        total = self.hits + self.misses
        return {
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": (self.hits / total * 100) if total else 0.0,
            "size_mb": (sum(len(v) for _, v in self._memory_cache.values()) / 1024 / 1024) if not self._redis else 0,
        }
```

**Modified:** `app/services/ai_service.py`

Add cache check at start of chat flow:
```python
async def chat(self, req: ChatRequest) -> ChatResponse:
    cache_key = CacheService.hash_key(req.user_name, req.user_message)
    cached = self._cache.get(cache_key)
    if cached:
        return ChatResponse.parse_raw(cached)  # fast path

    # existing LLM call...
    response = await self._call_llm(req)

    # cache write
    self._cache.set(cache_key, response.json())
    return response
```

**Modified:** `app/core/config.py` — add 4 fields:
```python
cache_enabled: bool = Field(default=True, alias="CACHE_ENABLED")
cache_redis_url: str = Field(default="", alias="CACHE_REDIS_URL")
cache_ttl_seconds: int = Field(default=3600, alias="CACHE_TTL_SECONDS")
cache_max_size_mb: int = Field(default=100, alias="CACHE_MAX_SIZE_MB")
```

### Fix 4: Test time guard

**Modified:** `scripts/test_comprehensive_30min.py`

**Changes:**

1. Add `time_guard()` helper to `Config` or module:
```python
def time_guard(cfg: Config, started: datetime) -> bool:
    """Returns True if test should continue, False if over budget."""
    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    return elapsed < cfg.total_runtime_cap_seconds
```

2. Modify `_run_full()` to check time_guard between phases:
```python
async def _run_full(cfg, log, phases_filter):
    started = datetime.now(timezone.utc)
    ...
    for phase_num, phase_runner in [(1, Phase1Warmup), (2, Phase2Rotate), (3, Phase3Recall)]:
        if not time_guard(cfg, started):
            log.warning(f"Skipping phase {phase_num} due to time budget")
            result = PhaseResult(name=f"phase{phase_num}_skipped", ..., skipped=True)
            report_gen.add_phase(result)
            continue
        # ... run phase
```

3. Add `skipped: bool = False` to `PhaseResult` dataclass.

4. Reduce default `phase2_users_total: 100 → 50` (cut load by 50%).

5. Update `ReportGenerator.build()` to include `skipped_phases` field if any.

### Test re-run plan

After each sprint, re-run `--quick` mode (2-3 min) to verify no regression. After sprint 3, re-run full 30-min test and compare to baseline:
- `reports/2026-06-12-comprehensive-30min/comprehensive_30min_20260613-000722.json` (baseline, FAIL)
- `reports/2026-06-XX-comprehensive-30min/<new>.json` (after fixes, expected PASS)

---

## 4. Data flow (cache example)

```
User POST /v1/chat {user_name, user_message, ...}
  ↓
ai_service.chat(req)
  ↓
cache_key = sha256("stress_an_00:Có áo thun trắng nào không?")
  ↓
cached = cache.get(cache_key)
  ├─ HIT (hits++) → return ChatResponse.parse_raw(cached) [~5ms]
  └─ MISS (misses++) → continue ↓
  ↓
  call llama.cpp (background E2B Q4) [~600ms]
  ↓
  response = await _call_llm(req)
  ↓
  cache.set(cache_key, response.json()) [~2ms]
  ↓
  return response

After test:
  cache.metrics() = {hits: 850, misses: 580, hit_rate: 59.4%, size_mb: 12.3}
  Expected: 50-70% hit rate (5 cache topics × 5-7 occurrences = ~250-350 cache hits, plus session repeats)
```

---

## 5. Success criteria (verified after sprint 3)

| Metric | Before | After target | Verification |
|---|---|---|---|
| Context overflow errors | 40 (2.8%) | **0** | `grep '"exceed_context_size"' uvicorn.log` returns 0 |
| Memory recall avg | 25.7% | **≥50%** | `metrics_summary.memory_recall_avg_pct >= 50` |
| Same-topic cache speedup (5/5 topics) | -310% to +12.5% (1/5 positive) | **≥10%** (5/5 positive) | `cache_speedup_pct` dict all values ≥ 10 |
| Test runtime | 56 min | **≤35 min** | `total_duration_seconds <= 2100` |

**Verdict expected:** PASS

---

## 6. Error handling

| Failure mode | Handling |
|---|---|
| Redis unavailable | Auto-fallback to in-memory, log warning, continue (no test impact) |
| Cache key collision (different session, same hash) | Unlikely with sha256 + user_id; verify in test |
| Cache corruption (invalid JSON) | `try/except` in parse, treat as miss |
| LLM call after cache miss fails | Propagate error (existing behavior, cache.set skipped) |
| StructMem extraction fails | Log error, fall back to SummaryService (or no memory for that convo) |
| Background llama.cpp restart | Cache service unaffected (no shared state) |
| Test phase over budget | Log "skipped", set `skipped=True`, continue to next phase (don't fail) |

---

## 7. Out of scope (YAGNI)

- ❌ Multi-tenant cache segregation (cache shared across users OK for clothing domain)
- ❌ Cache invalidation API (TTL is enough for clothing Q&A, prices change slowly)
- ❌ Persistent cache (Redis itself is persistent; in-memory fallback is OK for restart)
- ❌ Cache for streaming responses (only non-streaming; test uses non-streaming)
- ❌ FAISS / embedding cache changes (different layer, FastEmbed already fast)
- ❌ Adaptive routing / model selection changes (separate concern)
- ❌ Cloud cache (Redis on localhost is enough for our deployment)
- ❌ Schema migration for existing users (StructMem enable is a flag, no schema change)

---

## 8. Sprint breakdown

| Sprint | Duration | Fix | Deliverable | Verification |
|---|---|---|---|---|
| 1 | 1-3 days | 1 + 4 | Config + test infra | Re-run `--quick`, verify 0 context errors + ≤35 min |
| 2 | 3-5 days | 2 | StructMem enable | Re-run `--quick`, verify recall ≥50% |
| 3 | 5-7 days | 3 | CacheService + integration | Re-run full 30-min test, verify all 4 success criteria |

Total: 2-3 weeks. Each sprint shippable.

---

## 9. Files modified/created

**Sprint 1:**
- Modify: `scripts/start_background_q4.sh` (1 line)
- Modify: `scripts/test_comprehensive_30min.py` (add time_guard, skipped field, default 50 users)
- Modify: `.env` (no change in sprint 1, leave for sprint 2)

**Sprint 2:**
- Modify: `.env` (2 lines: ENABLE_STRUCTMEM, STRUCTMEM_EXTRACTION_THRESHOLD)

**Sprint 3:**
- Create: `app/services/cache_service.py` (~150 LOC)
- Create: `tests/integration/test_cache_service.py` (~100 LOC)
- Modify: `app/core/config.py` (4 fields)
- Modify: `app/services/ai_service.py` (cache check before/after LLM call)
- Modify: `app/routes/chat.py` (if needed for cache observability)

---

## 10. Open questions

None. All clarified during brainstorming.

Key decisions:
- Scope: All 4 fixes
- Risk tolerance: Aggressive (full refactor for cache, 2-3 sprints)
- Decomposition: 1 mega spec
- Cache: Application-level Redis with in-memory fallback (not llama.cpp prompt cache)
- StructMem: Enable + lower threshold (mutually exclusive with SummaryService, OK for our use case)
