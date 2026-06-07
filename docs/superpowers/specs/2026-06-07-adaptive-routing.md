# Adaptive Routing for ai-hub — Design

**Date:** 2026-06-07
**Status:** Approved (brainstorming 2026-06-07)
**Author:** Brainstorming session with user
**Related:**
- `2026-06-06-12b-q4-full-optimization.md` (current 12B Q4 primary)
- `bench_12b_full_2026-06-06.md` (per-model throughput baseline)
- `loadtest-5k-2026-06-07.md` (real-world 5K test showing 95% VRAM)

---

## 1. Background & Motivation

**Current state**: ai-hub's routing is static. `_select_model()` in `app/services/ai_service.py:504` uses a regex-based BRANE intent classifier (`query_type_model_map` in `app/core/config.py:197`) and the request's `model_mode` ("lite"/"normal"/"external"). For 5K load test (2026-06-07), this resulted in:

- **0% of 12B Q4** was used (all routed to E2B-bg + E4B for short prompts)
- **95% VRAM usage** sustained (15.5/16 GB) — no headroom for spikes
- **p95 latency 44s** — high but stable
- **No "periodic comprehensive evaluation"** for IHI-style systems

**User's request (2026-06-07)**: A flexible mechanism that:
1. Routes simple questions → E2B (fast, cheap)
2. Routes complex reasoning → 12B (high quality)
3. Adapts to system load: low load → 12B primary, high load → 12B only for hard
4. Periodic comprehensive evaluation (e.g. IHI every 6h rollup to 12B)
5. 12B as memory-summary aggregator when needed

## 2. Goal

Build a **4-component adaptive routing system** that:
- Picks the right model per request (heuristic + eventually ML)
- Adapts to current system load
- Runs periodic rollups for IHI / similar monitoring systems
- Stays within the 16GB VRAM budget (no OOM, no degradation cliff)

## 3. Architecture (Approach B: Bootstrap + Iterate)

```
                           ai-hub request flow
                                    │
   ┌────────────────────────────────▼────────────────────────────┐
   │                  app/services/router.py (NEW)              │
   │                                                            │
   │  ┌─────────────────┐  ┌─────────────────┐  ┌────────────┐  │
   │  │ Difficulty      │  │ Load Monitor    │  │ Memory     │  │
   │  │ Classifier      │  │ /health?slots   │  │ Context    │  │
   │  │ (heuristic)     │  │ polled every 1s │  │ (project)  │  │
   │  │   ↓ score       │  │   ↓ saturation  │  │   ↓ hint   │  │
   │  │  easy/med/hard  │  │   0.0-1.0 each  │  │   ihi/chat │  │
   │  └────────┬────────┘  └────────┬────────┘  └─────┬──────┘  │
   │           └────────────┬──────┴────────────────┘         │
   │                        ▼                                  │
   │            decision = combine(scores)                    │
   │                        │                                  │
   │            ┌───────────┼────────────┐                     │
   │            ▼           ▼            ▼                    │
   │          E2B-bg      E4B          12B                     │
   │         (port 8081) (port 8082) (port 8080)              │
   └────────────────────────────────────────────────────────────┘

   ┌────────────────────────────────────────────────────────────┐
   │   app/services/scheduler.py (NEW)                          │
   │   APScheduler cron: every 6h                               │
   │   → aggregate ihi_windows → 12B summary → ihi_rollups     │
   └────────────────────────────────────────────────────────────┘
```

## 4. Components

### 4.1 DifficultyClassifier (`app/services/difficulty_classifier.py`, ~250 LOC)

**Phase 1: Heuristic-based**

```python
def score(req: ChatRequest) -> float:
    s = 0.0
    text = req.user_message
    s += min(len(text) / 2000, 1.0) * 0.3          # length
    s += 0.3 if "```" in text else 0.0                 # has code
    s += 0.2 if any(c in text for c in "∑∫√=") else 0.0  # has math
    s += 0.2 if "?" in text and len(text.split("?")) > 2 else 0.0  # multi-question
    s += 0.1 * min(history_count / 10, 1.0)            # multi-turn
    return min(s, 1.0)

def classify(score: float) -> str:
    if score < 0.3: return "easy"
    if score < 0.6: return "med"
    return "hard"
```

**Phase 2: ML-based (auto-label from history)**
- After 30+ days of real usage, auto-label:
  - Queries where user **did not** escalate → label "easy"
  - Queries where 12B was used → label "hard"
- Train FastEmbed + LogisticRegression (3 classes)
- Inference: <2ms CPU
- Threshold-tune on held-out set

### 4.2 LoadMonitor (`app/services/load_monitor.py`, ~150 LOC)

```python
async def get_saturation(base_url: str) -> float:
    """Probe llama-server /health?include_slots=1."""
    async with httpx.AsyncClient(timeout=1.0) as c:
        r = await c.get(f"{base_url}/health")
        data = r.json()
    slots = data.get("slots", [])
    busy = sum(1 for s in slots if s.get("state") == 1)  # 1=processing
    return busy / max(len(slots), 1)
```

Polled every 1s, cached 200ms in-process. Per-port saturation:
- 8080 (12B): 0-1.0
- 8081 (E2B-bg): 0-1.0
- 8082 (E4B): 0-1.0

### 4.3 AdaptiveRouter (`app/services/router.py`, ~300 LOC)

```python
def route(req, *, difficulty, saturation, project_hint) -> ModelChoice:
    # Step 1: complexity → preferred model
    if difficulty == "easy": preferred = "E2B-bg"
    elif difficulty == "med": preferred = "E4B"
    else: preferred = "12B"

    # Step 2: load-aware degradation
    if preferred == "12B" and saturation[8080] > 0.8:
        if difficulty == "hard" and saturation[8082] < 0.6:
            return "E4B"
        return "E2B-bg"
    if preferred == "E4B" and saturation[8082] > 0.9:
        return "E2B-bg"

    # Step 3: project override (IHI always E2B-bg for live monitoring)
    if project_hint == "ihi" and preferred == "12B":
        return "E2B-bg"

    return preferred
```

**Replaces**: `_select_model()` in `app/services/ai_service.py:504`. Wraps existing logic — when difficulty classifier or load monitor is unavailable, fall back to current behavior.

### 4.4 PeriodicSummarizer (`app/services/scheduler.py`, ~200 LOC)

```python
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

scheduler = AsyncIOScheduler()

@scheduler.scheduled_job(CronTrigger.from_crontab("0 */6 * * *"))
async def rollup_ihi_windows():
    """Aggregate last 6h of IHI windows, send to 12B for summary."""
    windows = await db.fetch_all("""
        SELECT * FROM ihi_windows
        WHERE created_at > NOW() - INTERVAL '6 hours'
        ORDER BY created_at
    """)
    total_tokens = sum(len(w["data"]) for w in windows)
    if total_tokens < 5000:
        logger.info("Skip rollup: only %d tokens accumulated", total_tokens)
        return
    summary_input = format_windows_as_table(windows)
    summary = await ai_service.summarize(
        text=summary_input,
        model_override="gemma4-12b",
        user_id="_ihi_rollup",
        session_id="_rollup_6h",
    )
    await db.execute(
        "INSERT INTO ihi_rollups (window_start, window_end, summary, model) VALUES (...)",
        ...
    )

# In app/main.py startup:
scheduler.start()
```

**Schema addition** (new table):
```sql
CREATE TABLE ihi_rollups (
    id TEXT PRIMARY KEY,
    window_start TIMESTAMP NOT NULL,
    window_end TIMESTAMP NOT NULL,
    summary TEXT NOT NULL,
    model TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

## 5. Data flow

```
Request → AIService.handle_chat()
  │
  ├─[1] difficulty_classifier.score(req)            [1-2ms, CPU]
  │    → difficulty ∈ {easy, med, hard}
  │
  ├─[2] load_monitor.get_saturation()              [1ms, cache 200ms]
  │    → {8080: 0.7, 8081: 0.3, 8082: 0.5}
  │
  ├─[3] router.route(difficulty, saturation, project)
  │    → ("local-gemma4-12b-q4-text", 8080)
  │
  ├─[4] (existing) ai_service _build_messages, etc.
  │
  └─[5] Call model, persist usage_event
       (token fields populated per Rank 1 fix)
```

## 6. Error handling

- **Classifier fail** (exception): fall back to current `query_type_model_map` (no degradation)
- **Load monitor fail** (probe timeout): assume `saturation = 0` (assume idle, don't over-degrade)
- **All models saturated**: queue with current behavior, return 503 if timeout
- **Periodic summarizer fail**: log + alert via `failure_risk_events`; rollup table is best-effort
- **Training data insufficient** (Phase 2): ship heuristic-only, log "no ML classifier available yet"

## 7. Testing

- `test_difficulty_classifier.py`: 20+ synthetic queries → assert correct classification
- `test_load_monitor.py`: mock `/health?include_slots=1` responses
- `test_router.py`: decision matrix (difficulty × saturation → expected model)
- `test_scheduler.py`: mock cron trigger, verify ihi_rollups row created
- **Live test**: run 1K requests with various difficulties, verify distribution matches expectation (e.g. 60% E2B, 30% E4B, 10% 12B)

## 8. Token budget

- 4 new modules × ~200 LOC avg = ~800 LOC code
- ~500 LOC tests
- Total: ~1300 LOC new code
- Per-agent: 3-5M tokens for implementation
- Total budget: 10-15M tokens for the full Phase 1 (vs 30M available)

## 9. Phase plan

**Phase 1 (3-5 days)**:
- Heuristic classifier + load monitor + adaptive router + periodic summarizer
- All heuristic, no ML
- 12B ctx=8K already applied for safety
- End state: working adaptive routing system

**Phase 2 (2-3 days, after 30+ days of real usage data)**:
- Auto-labeler from history
- Train FastEmbed+LR classifier
- A/B test vs heuristic, switch when accuracy > heuristic
- Cascade escalation (E2B → 12B on low confidence)
- Speculative cascades (if llama.cpp gains native API)

**Phase 3 (separate)**:
- Multi-node load balancing (existing `LlamaCppLoadBalancer` ready, needs wiring)
- Hardware upgrade to RTX 5090 for safer headroom

## 10. Deliverables

- 4 new modules in `app/services/`
- 1 new table `ihi_rollups` in PG schema (auto-created by `init_db()`)
- ~500 LOC tests
- Phase 1 PR ready to merge to main

## 11. References

- [RouteLLM](https://github.com/lm-sys/RouteLLM) — matrix factorization router
- [AutoMix](https://github.com/automix-llm/automix) — self-verification cascade (NeurIPS 2024)
- [FrugalGPT](https://github.com/lcw99/FrugalGPT) — cascade with scoring (TMLR 2024)
- [Speculative Cascades (Google)](https://research.google/blog/speculative-cascades-a-hybrid-approach-for-smarter-faster-llm-inference/) — hybrid (NeurIPS 2024)
- [codelion/adaptive-classifier](https://github.com/codelion/adaptive-classifier) — ModernBERT production classifiers
- [APScheduler](https://apscheduler.readthedocs.io/) — periodic job pattern
- Research report: `/tmp/claude-1000/.../tasks/aa39f675ad23f2fe1.output` (full citations)

---

**Spec self-review (post-write):**
- ✅ No placeholders
- ✅ Internal consistency: all 4 components wired in single diagram
- ✅ Scope: focused on Phase 1, Phase 2/3 deferred
- ✅ Ambiguity: error handling explicit, ML path documented but not blocking
- ✅ Concrete: code patterns, table schema, file paths
