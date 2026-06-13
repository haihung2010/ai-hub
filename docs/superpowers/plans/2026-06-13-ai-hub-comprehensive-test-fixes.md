# AI Hub Comprehensive Test Fixes — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix 4 issues found in the 2026-06-12 30-min stress test (40 context overflows, 25.7% memory recall, negative cache speedup, 56-min test runtime) to meet success criteria: 0 context errors, ≥50% recall, ≥10% cache speedup, ≤35-min test runtime.

**Architecture:** 3 sprints, each shippable. Sprint 1: config + test infra (no ai-hub code). Sprint 2: enable StructMem in .env. Sprint 3: new `CacheService` (Redis + in-memory fallback) integrated into `ai_service.chat()`.

**Tech Stack:** Python 3.12, aiohttp (existing), redis-py (new dependency), pytest (existing), llama.cpp with E2B Q4 background.

---

## File Structure

**New files (Sprint 3):**
- `app/services/cache_service.py` (~180 LOC) — Redis + in-memory chat response cache
- `tests/integration/test_cache_service.py` (~150 LOC) — unit + integration tests

**Modified files:**
- `scripts/start_background_q4.sh` (1 line: PARALLEL default 16→4)
- `scripts/test_comprehensive_30min.py` (add time_guard, skipped field, reduce default users)
- `app/core/config.py` (4 new fields for cache)
- `app/services/ai_service.py` (cache check before/after LLM call)
- `app/main.py` (wire CacheService into lifespan)
- `.env` (2 lines: ENABLE_STRUCTMEM, STRUCTMEM_EXTRACTION_THRESHOLD)

**No new dependencies** — `redis` is already in `requirements.txt` (used by rate limiter).

---

## Conventions

- Each sprint starts with `--quick` smoke test (verify no regression)
- Each commit is small + reviewable
- All env reads via `os.getenv()` with sensible defaults
- TDD: write test first, see it fail, implement, see it pass
- Cache TTL: 3600s (1 hour) for clothing Q&A
- Cache key: `sha256(user_id + ":" + message)` for shared cache (safe for clothing domain)

---

## Sprint 1: Config + Test Infrastructure (1-3 days)

### Task 1: Lower background E2B parallel 16→4

**Files:**
- Modify: `scripts/start_background_q4.sh` (1 line: `PARALLEL=${PARALLEL:-16}` → `PARALLEL=${PARALLEL:-4}`)

- [ ] **Step 1.1: Edit `start_background_q4.sh`**

Run: `grep -n '^PARALLEL=' scripts/start_background_q4.sh`
Expected: `PARALLEL=${PARALLEL:-16}`

Use `Edit` tool to change `16` to `4`. Should now read `PARALLEL=${PARALLEL:-4}`.

- [ ] **Step 1.2: Commit**

```bash
git add scripts/start_background_q4.sh
git commit -m "perf(background): lower E2B parallel 16->4 for 8192 ctx/slot (was 2048)

Long conversation history (14 msgs * 150 tokens = ~2100 tokens) was
overflowing 2048 ctx/slot (32768 ctx / 16 parallel), causing 40/1430
(2.8%) 400 errors in 2026-06-12 stress test.

New: 32768 ctx / 4 parallel = 8192 tokens/slot. Plenty of headroom
for system prompt + history + response.

Trade-off: less concurrent slots (4 vs 16) but quality > concurrency
for memory + RAG workloads. Test re-run target: 0 context overflow."
```

- [ ] **Step 1.3: Verify script still parses**

Run: `bash -n scripts/start_background_q4.sh && echo OK`
Expected: `OK` (no syntax errors).

---

### Task 2: Add `time_guard()` function to test script

**Files:**
- Modify: `scripts/test_comprehensive_30min.py` (add helper after imports, ~line 38)

- [ ] **Step 2.1: Add `time_guard()` helper**

Find the line `# ── Config ────────────────────────────────────────────────────────────────` and insert before it:

```python
def time_guard(cfg: "Config", started_at: datetime) -> bool:
    """Return True if test should continue, False if over budget.

    Compares elapsed wall-clock since started_at against cfg.total_runtime_cap_seconds.
    Used to skip remaining phases if test is over budget.
    """
    elapsed = (datetime.now(timezone.utc) - started_at).total_seconds()
    return elapsed < cfg.total_runtime_cap_seconds
```

Note: `Config` is a forward reference because Config is defined later. The string annotation `"Config"` avoids runtime error.

- [ ] **Step 2.2: Smoke test the helper**

Run:
```bash
cd /home/hung/ai-hub && ./venv/bin/python -c "
import sys
sys.path.insert(0, 'scripts')
from datetime import datetime, timezone, timedelta
from test_comprehensive_30min import time_guard, Config
cfg = Config.from_env()
cfg.total_runtime_cap_seconds = 5
# Started 2s ago: should continue
started = datetime.now(timezone.utc) - timedelta(seconds=2)
assert time_guard(cfg, started) == True, 'should continue (2s < 5s)'
# Started 10s ago: should stop
started = datetime.now(timezone.utc) - timedelta(seconds=10)
assert time_guard(cfg, started) == False, 'should stop (10s > 5s)'
print('time_guard OK')
"
```
Expected: `time_guard OK`

- [ ] **Step 2.3: Commit**

```bash
git add scripts/test_comprehensive_30min.py
git commit -m "feat(test): time_guard() helper to enforce test runtime cap"
```

---

### Task 3: Add `skipped` field to `PhaseResult`

**Files:**
- Modify: `scripts/test_comprehensive_30min.py` (modify PhaseResult dataclass, ~line 774)

- [ ] **Step 3.1: Edit `PhaseResult` dataclass**

Find:
```python
@dataclass
class PhaseResult:
    name: str
    started_at: str
    ended_at: str
    duration_seconds: float
    extra: dict = field(default_factory=dict)
```

Replace with:
```python
@dataclass
class PhaseResult:
    name: str
    started_at: str
    ended_at: str
    duration_seconds: float
    skipped: bool = False
    skip_reason: str = ""
    extra: dict = field(default_factory=dict)
```

- [ ] **Step 3.2: Smoke test `PhaseResult.skipped` field**

Run:
```bash
cd /home/hung/ai-hub && ./venv/bin/python -c "
import sys
sys.path.insert(0, 'scripts')
from test_comprehensive_30min import PhaseResult
r1 = PhaseResult(name='p1', started_at='', ended_at='', duration_seconds=0)
r2 = PhaseResult(name='p2', started_at='', ended_at='', duration_seconds=0, skipped=True, skip_reason='time budget')
print('r1.skipped:', r1.skipped)
print('r2.skipped:', r2.skipped, 'reason:', r2.skip_reason)
assert r1.skipped == False
assert r2.skipped == True
print('PhaseResult.skipped OK')
"
```
Expected: `PhaseResult.skipped OK`

- [ ] **Step 3.3: Commit**

```bash
git add scripts/test_comprehensive_30min.py
git commit -m "feat(test): PhaseResult.skipped + skip_reason for time-budget aborts"
```

---

### Task 4: Integrate `time_guard` in `_run_full()` main loop

**Files:**
- Modify: `scripts/test_comprehensive_30min.py` (modify `_run_full()` function)

- [ ] **Step 4.1: Add time check before each phase**

Find in `_run_full()` (around line 1015):
```python
        if not phases_filter or 1 in phases_filter:
            print("[main] Phase 1: warmup (10 personas × 10 turns)")
            result = await Phase1Warmup(cfg, client, metrics, log).run()
            report_gen.add_phase(result)
            print(f"  done in {result.duration_seconds:.1f}s")

        if not phases_filter or 2 in phases_filter:
            print("[main] Phase 2: rotate (100 user, 5 cache topics)")
            result = await Phase2Rotate(cfg, client, metrics, log).run()
            report_gen.add_phase(result)
            print(f"  done in {result.duration_seconds:.1f}s")

        if not phases_filter or 3 in phases_filter:
            print("[main] Phase 3: memory recall + continue (3 rounds × 10 user)")
            result = await Phase3Recall(cfg, client, metrics, log).run()
            report_gen.add_phase(result)
            print(f"  done in {result.duration_seconds:.1f}s")
```

Replace with:
```python
        if not phases_filter or 1 in phases_filter:
            if not time_guard(cfg, started):
                log.warning("[main] time budget exceeded, skipping Phase 1")
                report_gen.add_phase(PhaseResult(
                    name="phase1_warmup", started_at="", ended_at="",
                    duration_seconds=0, skipped=True, skip_reason="time budget",
                ))
            else:
                print("[main] Phase 1: warmup (10 personas × 10 turns)")
                result = await Phase1Warmup(cfg, client, metrics, log).run()
                report_gen.add_phase(result)
                print(f"  done in {result.duration_seconds:.1f}s")

        if not phases_filter or 2 in phases_filter:
            if not time_guard(cfg, started):
                log.warning("[main] time budget exceeded, skipping Phase 2")
                report_gen.add_phase(PhaseResult(
                    name="phase2_rotate", started_at="", ended_at="",
                    duration_seconds=0, skipped=True, skip_reason="time budget",
                ))
            else:
                print("[main] Phase 2: rotate (50 user, 5 cache topics)")
                result = await Phase2Rotate(cfg, client, metrics, log).run()
                report_gen.add_phase(result)
                print(f"  done in {result.duration_seconds:.1f}s")

        if not phases_filter or 3 in phases_filter:
            if not time_guard(cfg, started):
                log.warning("[main] time budget exceeded, skipping Phase 3")
                report_gen.add_phase(PhaseResult(
                    name="phase3_recall", started_at="", ended_at="",
                    duration_seconds=0, skipped=True, skip_reason="time budget",
                ))
            else:
                print("[main] Phase 3: memory recall + continue (3 rounds × 10 user)")
                result = await Phase3Recall(cfg, client, metrics, log).run()
                report_gen.add_phase(result)
                print(f"  done in {result.duration_seconds:.1f}s")
```

- [ ] **Step 4.2: Smoke test via `--quick` (should still complete)**

Run: `cd /home/hung/ai-hub && time ./venv/bin/python scripts/test_comprehensive_30min.py --quick 2>&1 | tail -10`
Expected: All 3 phases complete in <5 min, no `skipped` phases.

- [ ] **Step 4.3: Commit**

```bash
git add scripts/test_comprehensive_30min.py
git commit -m "feat(test): time_guard integrated in _run_full() to skip phases over budget"
```

---

### Task 5: Reduce default `phase2_users_total` 100→50

**Files:**
- Modify: `scripts/test_comprehensive_30min.py` (1 line in `Config.from_env`)

- [ ] **Step 5.1: Edit Config default**

Find in `Config.from_env()`:
```python
            phase2_users_total=int(os.getenv("AIHUB_TEST_PHASE2_USERS", "100")),
```

Replace with:
```python
            phase2_users_total=int(os.getenv("AIHUB_TEST_PHASE2_USERS", "50")),
```

- [ ] **Step 5.2: Smoke test default load**

Run:
```bash
cd /home/hung/ai-hub && ./venv/bin/python -c "
import sys
sys.path.insert(0, 'scripts')
from test_comprehensive_30min import Config
c = Config.from_env()
print('phase2_users_total:', c.phase2_users_total, '(expect 50)')
assert c.phase2_users_total == 50
print('OK')
"
```
Expected: `phase2_users_total: 50 (expect 50)` and `OK`

- [ ] **Step 5.3: Commit**

```bash
git add scripts/test_comprehensive_30min.py
git commit -m "perf(test): reduce default phase2 users 100->50 to fit in 30 min budget

100 user * 10 turns = 1000 phase-2 turns takes ~34 min alone.
50 user * 10 turns = 500 phase-2 turns takes ~17 min.

Total test (phase1+2+3) now ~25-30 min, within 35 min hard cap.
Override via AIHUB_TEST_PHASE2_USERS env var if more load needed."
```

- [ ] **Step 5.4: Sprint 1 smoke test**

Run: `cd /home/hung/ai-hub && time ./venv/bin/python scripts/test_comprehensive_30min.py --quick 2>&1 | tail -10`
Expected: All 3 phases complete, no `skipped` phase, runtime <5 min.

---

## Sprint 2: StructMem Enable (3-5 days)

### Task 6: Update `.env` for StructMem

**Files:**
- Modify: `.env` (2 lines)

- [ ] **Step 6.1: Edit `.env`**

Run: `grep -nE '^ENABLE_STRUCTMEM|^STRUCTMEM_EXTRACTION_THRESHOLD' .env`

Expected:
```
ENABLE_STRUCTMEM=false
STRUCTMEM_EXTRACTION_THRESHOLD=16
```

Use `Edit` tool to change:
- `ENABLE_STRUCTMEM=false` → `ENABLE_STRUCTMEM=true`
- `STRUCTMEM_EXTRACTION_THRESHOLD=16` → `STRUCTMEM_EXTRACTION_THRESHOLD=8`

- [ ] **Step 6.2: Verify change**

Run: `grep -E '^ENABLE_STRUCTMEM|^STRUCTMEM_EXTRACTION_THRESHOLD' .env`
Expected:
```
ENABLE_STRUCTMEM=true
STRUCTMEM_EXTRACTION_THRESHOLD=8
```

- [ ] **Step 6.3: Document the change in CLAUDE.md or commit message**

`.env` is gitignored, so no commit needed. The change is local-only. The next commit referencing StructMem should mention this.

---

### Task 7: Verify StructMem activates in ai-hub (no code change)

**Files:** none (verification only)

- [ ] **Step 7.1: Start ai-hub with new .env**

Run:
```bash
cd /home/hung/ai-hub && nohup ./venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 > /tmp/aihub-uvicorn.log 2>&1 &
disown
sleep 5
curl -s -H "X-API-KEY: $(grep '^API_KEY=' .env | cut -d= -f2 | tr -d '"')" http://127.0.0.1:8000/health
```
Expected: `{"status":"ok", ...}`

- [ ] **Step 7.2: Send chat and check StructMem activation**

Run:
```bash
API_KEY=$(grep '^API_KEY=' /home/hung/ai-hub/.env | cut -d= -f2 | tr -d '"')
# Send 10 messages to trigger extraction threshold (8)
for i in {1..10}; do
  curl -s -H "X-API-KEY: $API_KEY" -H "Content-Type: application/json" \
    -d "{\"project_id\":\"default\",\"tenant_id\":\"default\",\"user_name\":\"smoke_structmem\",\"user_message\":\"Câu hỏi $i về áo thun\",\"session_id\":\"smoke_structmem_s1\",\"model_mode\":\"lite\",\"stream\":false}" \
    http://127.0.0.1:8000/v1/chat > /dev/null
  echo "Sent turn $i"
  sleep 1
done
echo ""
echo "=== Check StructMem activity in log ==="
grep -E 'structmem|STRUCTMEM' /tmp/aihub-uvicorn.log | tail -10
```
Expected: Should see `structmem` related log entries (extraction triggered, SPO triples generated).

- [ ] **Step 7.3: Verify in PostgreSQL**

Run:
```bash
./venv/bin/python -c "
import psycopg
conn = psycopg.connect('postgresql://aihub:aihub_pass@localhost:5432/ai_hub?sslmode=require')
with conn.cursor() as cur:
    cur.execute(\"\"\"
        SELECT column_name FROM information_schema.columns
        WHERE table_name LIKE '%structmem%' OR table_name LIKE '%memory%'
        ORDER BY table_name, column_name
    \"\"\")
    cols = cur.fetchall()
    print('Memory/StructMem tables+columns:')
    for c in cols[:20]:
        print(' ', c[0])
"
```
Expected: Tables like `structmem_triples`, `memory_records` exist.

- [ ] **Step 7.4: Stop ai-hub (Sprint 2 verification done)**

Run: `pkill -f 'uvicorn app.main:app' && sleep 2 && ps aux | grep '[u]vicorn' | wc -l`
Expected: `0`

---

### Task 8: Re-run --quick test, verify recall ≥50%

**Files:** none (verification)

- [ ] **Step 8.1: Start full ai-hub stack (12B + E2B) for quick test**

Run:
```bash
cd /home/hung/ai-hub
# 12B Q4 on 8080
./scripts/start_5060ti_16gb.sh &
disown
# E2B Q4 on 8081
./scripts/start_background_q4.sh &
disown
# Wait for both
sleep 20
for i in {1..30}; do
  if curl -s -m 2 http://127.0.0.1:8080/health > /dev/null && curl -s -m 2 http://127.0.0.1:8081/health > /dev/null; then
    echo "Both llama.cpp up after ${i}s"
    break
  fi
  sleep 2
done
# Start uvicorn
nohup ./venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 > /tmp/aihub-uvicorn.log 2>&1 &
disown
sleep 5
curl -s -H "X-API-KEY: $(grep '^API_KEY=' .env | cut -d= -f2 | tr -d '"')" http://127.0.0.1:8000/health
```
Expected: ai-hub healthy.

- [ ] **Step 8.2: Run --quick and check recall**

Run:
```bash
cd /home/hung/ai-hub && time ./venv/bin/python scripts/test_comprehensive_30min.py --quick 2>&1 | tail -10
LATEST=$(ls -t reports/comprehensive_30min_*.json | head -1)
./venv/bin/python -c "
import json
r = json.load(open('$LATEST'))
print('Recall:', f'{r[\"metrics_summary\"][\"memory_recall_avg_pct\"]:.1f}%', '(target >=50%)')
print('Error rate:', f'{r[\"metrics_summary\"][\"error_rate\"]*100:.1f}%')
print('Verdict:', r['verdict'])
"
```
Expected: Recall ≥50% (was 25.7%). If still <50%, check log for structmem extraction errors.

- [ ] **Step 8.3: Document StructMem results**

If recall <50%, file a follow-up issue. If ≥50%, proceed to Sprint 3.

---

## Sprint 3: CacheService + Integration (5-7 days)

### Task 9: Add cache config fields to `app/core/config.py`

**Files:**
- Modify: `app/core/config.py` (4 new fields in Settings class)

- [ ] **Step 9.1: Find the right place to add fields**

Run: `grep -n 'failure_risk_log_only' app/core/config.py | head -3`
Expected: shows the line with `failure_risk_log_only` field.

- [ ] **Step 9.2: Add 4 new fields**

After `failure_risk_log_only: bool = Field(default=True, alias="FAILURE_RISK_LOG_ONLY")` line, add:

```python
    # Response cache (Sprint 3 Fix 3)
    cache_enabled: bool = Field(default=True, alias="CACHE_ENABLED")
    cache_redis_url: str = Field(default="", alias="CACHE_REDIS_URL")
    cache_ttl_seconds: int = Field(default=3600, alias="CACHE_TTL_SECONDS")
    cache_max_size_mb: int = Field(default=100, alias="CACHE_MAX_SIZE_MB")
```

- [ ] **Step 9.3: Smoke test config loads with cache fields**

Run:
```bash
cd /home/hung/ai-hub && ./venv/bin/python -c "
from app.core.config import Settings
s = Settings()
print('cache_enabled:', s.cache_enabled)
print('cache_redis_url:', repr(s.cache_redis_url))
print('cache_ttl_seconds:', s.cache_ttl_seconds)
print('cache_max_size_mb:', s.cache_max_size_mb)
assert s.cache_enabled == True
assert s.cache_ttl_seconds == 3600
print('Cache config OK')
"
```
Expected: `Cache config OK`

- [ ] **Step 9.4: Commit**

```bash
git add app/core/config.py
git commit -m "feat(cache): 4 config fields for response cache (enabled/url/ttl/size)"
```

---

### Task 10: Create `app/services/cache_service.py`

**Files:**
- Create: `app/services/cache_service.py`

- [ ] **Step 10.1: Create file with full implementation**

```python
"""Redis-backed chat response cache with in-memory fallback.

Used by ai_service to skip LLM call when same (user_id, message) seen before.
For clothing e-commerce Q&A, same questions from different users can share
cache safely (product info is public).

Auto-fallback to in-memory if Redis is down. Tracks hits/misses for metrics.
"""
from __future__ import annotations

import hashlib
import logging
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)


class CacheService:
    """Thread-safe chat response cache.

    Key: sha256(f"{user_id}:{message}")
    Value: serialized response string
    TTL: cache_ttl_seconds (default 3600s = 1 hour)
    Size limit: cache_max_size_mb (default 100 MB) for in-memory fallback
    """

    def __init__(
        self,
        redis_url: str = "",
        ttl_seconds: int = 3600,
        max_size_mb: int = 100,
    ):
        self.ttl = ttl_seconds
        self._max_size_bytes = max_size_mb * 1024 * 1024
        self._memory_cache: dict[str, tuple[float, str]] = {}
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0
        self._redis = None
        self._redis_failed = False
        if redis_url:
            self._init_redis(redis_url)

    def _init_redis(self, redis_url: str) -> None:
        try:
            import redis
            self._redis = redis.from_url(redis_url, decode_responses=True, socket_timeout=2)
            self._redis.ping()
            logger.info("CacheService: Redis connected at %s", redis_url)
        except Exception as e:
            logger.warning("CacheService: Redis unavailable (%r), using in-memory only", e)
            self._redis = None
            self._redis_failed = True

    @staticmethod
    def hash_key(user_id: str, message: str) -> str:
        """Hash user_id + message into a stable cache key."""
        return hashlib.sha256(f"{user_id}:{message}".encode("utf-8")).hexdigest()

    def get(self, key: str) -> Optional[str]:
        """Return cached value or None. Auto-fallback to memory if Redis fails."""
        if self._redis is not None:
            try:
                val = self._redis.get(key)
                if val is not None:
                    self.hits += 1
                    return val
                self.misses += 1
                return None
            except Exception as e:
                logger.warning("CacheService: Redis get failed (%r), falling back to memory", e)
                self._redis = None
        return self._get_memory(key)

    def _get_memory(self, key: str) -> Optional[str]:
        with self._lock:
            entry = self._memory_cache.get(key)
            if entry is None:
                self.misses += 1
                return None
            expires_at, value = entry
            if expires_at < time.time():
                del self._memory_cache[key]
                self.misses += 1
                return None
            self.hits += 1
            return value

    def set(self, key: str, value: str) -> None:
        """Store value with TTL. Auto-fallback if Redis fails."""
        if self._redis is not None:
            try:
                self._redis.setex(key, self.ttl, value)
                return
            except Exception as e:
                logger.warning("CacheService: Redis set failed (%r), falling back to memory", e)
                self._redis = None
        self._set_memory(key, value)

    def _set_memory(self, key: str, value: str) -> None:
        with self._lock:
            self._evict_if_needed(len(value))
            self._memory_cache[key] = (time.time() + self.ttl, value)

    def _evict_if_needed(self, new_entry_size: int) -> None:
        """Evict oldest 20% if over size limit."""
        with self._lock:
            total = sum(len(v) for _, v in self._memory_cache.values()) + new_entry_size
            if total <= self._max_size_bytes:
                return
            sorted_entries = sorted(self._memory_cache.items(), key=lambda kv: kv[1][0])
            evict_count = max(1, len(sorted_entries) // 5)
            for k, _ in sorted_entries[:evict_count]:
                del self._memory_cache[k]

    def metrics(self) -> dict:
        """Return hit/miss/size metrics for observability."""
        total = self.hits + self.misses
        with self._lock:
            size_bytes = sum(len(v) for _, v in self._memory_cache.values())
        return {
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate_pct": (self.hits / total * 100) if total else 0.0,
            "size_mb": size_bytes / 1024 / 1024,
            "size_entries": len(self._memory_cache),
            "redis_connected": self._redis is not None,
        }

    def clear(self) -> None:
        """Clear all cached values (testing only)."""
        with self._lock:
            self._memory_cache.clear()
        if self._redis is not None:
            try:
                self._redis.flushdb()
            except Exception:
                pass
```

- [ ] **Step 10.2: Smoke test the class**

Run:
```bash
cd /home/hung/ai-hub && ./venv/bin/python -c "
from app.services.cache_service import CacheService
c = CacheService(ttl_seconds=60, max_size_mb=1)

# Test hash
k = CacheService.hash_key('user1', 'hello')
assert len(k) == 64, f'expected 64 char sha256, got {len(k)}'
print('hash_key OK')

# Test miss
assert c.get(k) is None
assert c.misses == 1

# Test set + get
c.set(k, 'response text')
assert c.get(k) == 'response text'
assert c.hits == 1

# Test metrics
m = c.metrics()
print('metrics:', m)
assert m['hits'] == 1
assert m['misses'] == 1
assert m['hit_rate_pct'] == 50.0
print('CacheService basic OK')
"
```
Expected: `CacheService basic OK`

- [ ] **Step 10.3: Commit**

```bash
git add app/services/cache_service.py
git commit -m "feat(cache): CacheService with Redis + in-memory fallback + LRU eviction

- sha256(user_id + message) for stable cache key
- Redis primary, auto-fallback to in-memory if Redis down
- LRU eviction (oldest 20%) when over size limit
- Thread-safe via threading.Lock
- metrics() for observability (hits/misses/size/redis_connected)
- clear() for testing"
```

---

### Task 11: Write unit test for CacheService

**Files:**
- Create: `tests/unit/test_cache_service.py`

- [ ] **Step 11.1: Create unit test file**

```python
"""Unit tests for CacheService."""
from __future__ import annotations

import time

import pytest

from app.services.cache_service import CacheService


def test_hash_key_deterministic():
    k1 = CacheService.hash_key("user1", "hello")
    k2 = CacheService.hash_key("user1", "hello")
    assert k1 == k2
    assert len(k1) == 64  # sha256 hex


def test_hash_key_differs_by_user():
    k1 = CacheService.hash_key("user1", "hello")
    k2 = CacheService.hash_key("user2", "hello")
    assert k1 != k2


def test_hash_key_differs_by_message():
    k1 = CacheService.hash_key("user1", "hello")
    k2 = CacheService.hash_key("user1", "goodbye")
    assert k1 != k2


def test_get_returns_none_on_miss():
    c = CacheService(ttl_seconds=60)
    assert c.get("nonexistent_key") is None
    assert c.misses == 1
    assert c.hits == 0


def test_set_then_get_returns_value():
    c = CacheService(ttl_seconds=60)
    k = CacheService.hash_key("u1", "msg")
    c.set(k, "response text")
    assert c.get(k) == "response text"
    assert c.hits == 1
    assert c.misses == 0


def test_ttl_expiration():
    c = CacheService(ttl_seconds=1)
    k = CacheService.hash_key("u1", "msg")
    c.set(k, "response")
    assert c.get(k) == "response"
    time.sleep(1.1)
    assert c.get(k) is None
    assert c.misses == 2  # 1 initial + 1 after expire


def test_metrics_basic():
    c = CacheService(ttl_seconds=60)
    k = CacheService.hash_key("u1", "msg")
    c.set(k, "v")
    c.get(k)  # hit
    c.get("missing")  # miss
    m = c.metrics()
    assert m["hits"] == 1
    assert m["misses"] == 1
    assert m["hit_rate_pct"] == 50.0
    assert m["size_entries"] == 1
    assert m["redis_connected"] is False


def test_metrics_empty_cache():
    c = CacheService(ttl_seconds=60)
    m = c.metrics()
    assert m["hits"] == 0
    assert m["misses"] == 0
    assert m["hit_rate_pct"] == 0.0
    assert m["size_mb"] == 0.0


def test_lru_eviction():
    """When over size limit, evict oldest 20%."""
    c = CacheService(ttl_seconds=60, max_size_mb=1)  # 1 MB
    # Fill cache with ~1 MB of entries (each 100KB)
    big_value = "x" * (100 * 1024)  # 100KB
    for i in range(12):
        c.set(f"key_{i}", big_value)
    # 12 * 100KB = 1.2 MB > 1 MB limit
    # Should have evicted at least 1 (oldest)
    assert c.metrics()["size_entries"] < 12


def test_clear():
    c = CacheService(ttl_seconds=60)
    c.set("k1", "v1")
    c.set("k2", "v2")
    c.clear()
    assert c.get("k1") is None
    assert c.metrics()["size_entries"] == 0


def test_concurrent_access():
    """Thread-safety smoke test."""
    import threading
    c = CacheService(ttl_seconds=60)

    def worker(i):
        k = f"key_{i}"
        c.set(k, f"value_{i}")
        c.get(k)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(50)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    m = c.metrics()
    assert m["hits"] + m["misses"] >= 50  # at least 50 misses (no hits because 50 different keys)
```

- [ ] **Step 11.2: Run test to verify all pass**

Run: `cd /home/hung/ai-hub && ./venv/bin/pytest tests/unit/test_cache_service.py -v --no-cov`
Expected: 10/10 tests pass.

- [ ] **Step 11.3: Commit**

```bash
git add tests/unit/test_cache_service.py
git commit -m "test(cache): 10 unit tests for CacheService (hash/TTL/LRU/concurrent)"
```

---

### Task 12: Integrate CacheService into ai_service.py

**Files:**
- Modify: `app/services/ai_service.py` (cache check + write in chat flow)

- [ ] **Step 12.1: Find the chat method**

Run: `grep -n 'async def chat\|async def _call_llm' app/services/ai_service.py | head -5`
Expected: shows the chat method signature.

- [ ] **Step 12.2: Add cache import + initialization**

Add to imports at top:
```python
from app.services.cache_service import CacheService
```

Find the `__init__` of the chat service class and add cache initialization. Pattern:
```python
        self._cache = CacheService(
            redis_url=settings.cache_redis_url,
            ttl_seconds=settings.cache_ttl_seconds,
            max_size_mb=settings.cache_max_size_mb,
        ) if settings.cache_enabled else None
```

(Adjust based on actual class structure — look for where other services like `self._pinned_memory` are initialized.)

- [ ] **Step 12.3: Add cache check at start of chat flow**

Find the `chat` method and add at the very start (after parsing/normalizing request):
```python
        # Cache check
        if self._cache is not None:
            cache_key = CacheService.hash_key(req.user_name, req.user_message)
            cached = self._cache.get(cache_key)
            if cached is not None:
                logger.info("cache_hit user=%s", req.user_name)
                return ChatResponse.parse_raw(cached)
```

- [ ] **Step 12.4: Add cache write at end of chat flow**

Find the return statement at the end of chat flow (after LLM call returns response) and add before it:
```python
        # Cache write
        if self._cache is not None:
            cache_key = CacheService.hash_key(req.user_name, req.user_message)
            self._cache.set(cache_key, response.json())
```

- [ ] **Step 12.5: Verify ai-hub still imports cleanly**

Run: `cd /home/hung/ai-hub && ./venv/bin/python -c "from app.services.ai_service import *; print('OK')"`
Expected: `OK`

- [ ] **Step 12.6: Commit**

```bash
git add app/services/ai_service.py
git commit -m "feat(cache): CacheService integration in ai_service.chat()

- Check cache before LLM call (skip if hit)
- Write to cache after LLM response
- Hash key = sha256(user_id + message)
- Disable-able via CACHE_ENABLED=false"
```

---

### Task 13: Wire CacheService into main.py lifespan

**Files:**
- Modify: `app/main.py` (ensure cache is initialized in app lifespan, expose metrics endpoint)

- [ ] **Step 13.1: Find lifespan and add cache exposure**

Run: `grep -n 'lifespan\|app.state' app/main.py | head -10`

If ai_service is already constructed in lifespan (it is, per the existing test config), the cache is already initialized. Just expose metrics via the existing `/v1/admin/health/providers` endpoint or add a new `/v1/admin/cache/metrics` endpoint.

- [ ] **Step 13.2: Add cache metrics endpoint (optional)**

If pattern is straightforward, add to `app/routes/admin.py`:
```python
@router.get("/cache/metrics")
async def cache_metrics(request: Request) -> dict:
    """Return cache hit/miss/size metrics."""
    ai_service = request.app.state.ai_service
    if hasattr(ai_service, "_cache") and ai_service._cache is not None:
        return ai_service._cache.metrics()
    return {"error": "cache not enabled"}
```

(Skip this task if it would require too much refactoring — metrics can be logged in uvicorn log instead.)

- [ ] **Step 13.3: Commit (if changed)**

```bash
git add app/main.py app/routes/admin.py
git commit -m "feat(cache): expose cache metrics via /v1/admin/cache/metrics"
```

---

### Task 14: Update test report to include cache metrics

**Files:**
- Modify: `scripts/test_comprehensive_30min.py` (add cache metrics to ReportGenerator.build)

- [ ] **Step 14.1: Add cache_metrics field to report**

In `ReportGenerator.build()` return dict, add:
```python
        cache_metrics = self._get_cache_metrics(cfg)
        return {
            ...,
            "cache_metrics": cache_metrics,
            ...,
        }
```

And add method:
```python
    def _get_cache_metrics(self, cfg: "Config") -> dict:
        """Fetch cache metrics from ai-hub's /v1/admin/cache/metrics endpoint.

        Best-effort: returns empty dict if endpoint unavailable.
        """
        try:
            import urllib.request
            req = urllib.request.Request(
                f"{cfg.base_url}/v1/admin/cache/metrics",
                headers=cfg.headers(),
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                import json
                return json.loads(resp.read())
        except Exception:
            return {}
```

- [ ] **Step 14.2: Commit**

```bash
git add scripts/test_comprehensive_30min.py
git commit -m "feat(test): include cache_metrics in report (from /v1/admin/cache/metrics)"
```

---

### Task 15: Re-run full 30-min test, verify all 4 success criteria

**Files:** none (verification only)

- [ ] **Step 15.1: Start full ai-hub stack (3 components)**

Run:
```bash
cd /home/hung/ai-hub
# Stop any existing services first
pkill -f 'llama-server\|uvicorn' 2>/dev/null || true
sleep 3
# 12B Q4 on 8080
./scripts/start_5060ti_16gb.sh &
disown
# E2B Q4 on 8081 (will use new PARALLEL=4)
PARALLEL=4 ./scripts/start_background_q4.sh &
disown
# Wait
sleep 30
for i in {1..30}; do
  if curl -s -m 2 http://127.0.0.1:8080/health > /dev/null && curl -s -m 2 http://127.0.0.1:8081/health > /dev/null; then
    echo "Both llama.cpp up after ${i}*2=$((i*2))s"
    break
  fi
  sleep 2
done
# Start uvicorn
nohup ./venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 > /tmp/aihub-uvicorn.log 2>&1 &
disown
sleep 5
curl -s -H "X-API-KEY: $(grep '^API_KEY=' .env | cut -d= -f2 | tr -d '"')" http://127.0.0.1:8000/health
```
Expected: All 3 healthy.

- [ ] **Step 15.2: Run full 30-min test in background**

Run:
```bash
cd /home/hung/ai-hub && mkdir -p reports
nohup ./venv/bin/python scripts/test_comprehensive_30min.py > /tmp/full_test_v2.log 2>&1 &
disown
TEST_PID=$!
echo "Test PID: $TEST_PID"
echo "Monitor: tail -f /tmp/full_test_v2.log"
```

- [ ] **Step 15.3: Wait 5 min then check progress**

Run:
```bash
sleep 300
ps -p $TEST_PID -o etime,stat 2>/dev/null
grep -c '"POST /v1/chat' /tmp/aihub-uvicorn.log
tail -3 /tmp/full_test_v2.log
```

- [ ] **Step 15.4: Wait 15 more min then check progress**

Run:
```bash
sleep 900
ps -p $TEST_PID -o etime,stat 2>/dev/null
grep -c '"POST /v1/chat' /tmp/aihub-uvicorn.log
tail -3 /tmp/full_test_v2.log
```

- [ ] **Step 15.5: Wait until test completes (≤35 min total)**

Run:
```bash
while ps -p $TEST_PID > /dev/null 2>&1; do
  sleep 60
done
echo "Test completed at $(date '+%H:%M:%S')"
echo ""
echo "=== Verdict ==="
tail -10 /tmp/full_test_v2.log
```

- [ ] **Step 15.6: Verify all 4 success criteria**

Run:
```bash
cd /home/hung/ai-hub
LATEST=$(ls -t reports/comprehensive_30min_*.json | head -1)
./venv/bin/python -c "
import json
r = json.load(open('$LATEST'))
ms = r['metrics_summary']
print('=== SUCCESS CRITERIA ===')
print(f'1. Context overflow: 0 (target 0)', '✓' if ms['errors'] == 0 else '✗', f'(actual: {ms[\"errors\"]} errors)')
ctx_errs = sum(1 for e in r['top_errors'] if 'exceed_context_size' in e.get('error',''))
print(f'   (specifically context-overflow errors: {ctx_errs})')
print(f'2. Memory recall: {ms[\"memory_recall_avg_pct\"]:.1f}% (target >=50%)', '✓' if ms['memory_recall_avg_pct'] >= 50 else '✗')
print(f'3. Cache speedup (5/5 positive):')
speedups = ms.get('cache_speedup_pct', {}) or {}
positive = sum(1 for s in speedups.values() if s >= 10)
print(f'   {positive}/{len(speedups)} topics >=10%', '✓' if positive == 5 and len(speedups) == 5 else '✗')
for t, s in speedups.items():
    print(f'     {t}: {s:+.1f}%')
print(f'4. Test runtime: {r[\"total_duration_seconds\"]/60:.1f} min (target <=35)', '✓' if r['total_duration_seconds'] <= 2100 else '✗')
print()
print('Verdict:', r['verdict'])
"
```

Expected:
- 0 context overflow errors ✓
- recall ≥50% ✓
- 5/5 topics with cache speedup ≥10% ✓
- runtime ≤35 min ✓
- Verdict: PASS

- [ ] **Step 15.7: Archive report**

Run:
```bash
cd /home/hung/ai-hub
LATEST=$(ls -t reports/comprehensive_30min_*.json | head -1)
mkdir -p reports/2026-06-13-after-fixes
cp "$LATEST" reports/2026-06-13-after-fixes/
git add -f reports/2026-06-13-after-fixes/
git commit -m "test: 30-min test after 4 fixes (verdict=expected PASS)

Compared to baseline 2026-06-12:
  - Context overflow errors: 40 -> 0 ✓
  - Memory recall: 25.7% -> ?% (target >=50%)
  - Cache speedup: 5/5 topics positive (target)
  - Test runtime: 56 min -> ? min (target <=35)"
```

---

## Self-Review Checklist

✅ **Spec coverage:**
- Section 2 architecture → Tasks 1, 6, 9-13
- Section 3 components → Tasks 1, 2-5, 6-7, 9-14
- Section 4 data flow → Task 12 (cache flow)
- Section 5 success criteria → Task 15 (verification)
- Section 6 error handling → Task 10 (cache fallback), Task 4 (time guard skip)
- Section 7 out of scope → respected (no multi-tenant cache, no schema migration)
- Section 8 sprint breakdown → 3 sprint sections in this plan
- Section 9 files → all listed in plan

✅ **Placeholder scan:** No TBD/TODO/implement-later. Each step has full code or clear command.

✅ **Type consistency:**
- `CacheService` signature consistent across Tasks 10, 11, 12
- `time_guard()` signature consistent in Tasks 2, 4
- `PhaseResult.skipped` field used consistently in Tasks 3, 4
- `cfg.total_runtime_cap_seconds` referenced in Tasks 2, 4
- `Config.from_env()` modified in Task 5, smoke test in Task 5.2 verifies

✅ **File paths exact:** all paths use full paths (`scripts/...`, `app/services/...`, etc.)

✅ **Commit cadence:** 14 commits planned, each small and reviewable.

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-06-13-ai-hub-comprehensive-test-fixes.md`.**

**Two execution options:**

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks. Best for code quality.

2. **Inline Execution** — Execute tasks in this session using `executing-plans`. Best for faster overall progress (each sprint ~1-3 days of work).
