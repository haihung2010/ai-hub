# Memory Recall Fixes — Design

**Date:** 2026-06-13
**Status:** Approved
**Author:** Follow-up brainstorming to 2026-06-12 test + 2026-06-13 re-run
**Related:**
- `reports/2026-06-13-after-fixes/comprehensive_30min_20260613-084643.json` (FAIL, 28.3% recall)
- `docs/superpowers/specs/2026-06-13-ai-hub-comprehensive-test-fixes-design.md` (previous fixes)
- `app/services/structmem_service.py`, `app/services/ai_service.py`, `app/core/config.py`

---

## 1. Background & Motivation

Test re-run ngày 2026-06-13 với StructMem enabled vẫn cho 28.3% memory recall (target ≥50%). Investigation phát hiện:

| Vấn đề | Evidence | Root cause |
|---|---|---|
| 1. Test recall check sai baseline | 1 user recall 10% mặc dù model list được 8 messages verbatim | Test dùng 10 fixed keywords, user chỉ hỏi 1-2 categories → even perfect recall = 20-30% |
| 2. `StructMem extraction returned invalid JSON` | Uvicorn log warning xuất hiện 1 lần trong test | Extractor LLM trả về malformed JSON → memories bị drop |
| 3. Memory không inject vào LLM context cho new questions | Test 2: "Áo thun trắng giá bao nhiêu?" → "Bạn chưa cung cấp thông tin" (sai, memory có rồi) | Memory retrieval chỉ fired cho recall queries, không cho regular chat |
| 4. E2B Q4 quá nhỏ để summarize well | Model response abstract ("Màu sắc", "Chất liệu") thay vì specific ("áo thun trắng size M") | E2B 2B params vs recall needs more reasoning |

**Mục tiêu:** 4 fixes → recall ≥50% thực sự (không phải test inflation).

---

## 2. Architecture

```
┌────────────────────────────────────────────────────────────────┐
│  Fix 1: Test re-design (track per-user key_facts)             │
│  scripts/test_comprehensive_30min.py                          │
│    Phase1: track user_id → list of (topic, key_facts)         │
│    Phase3: pass user-specific facts to recall check           │
│    → Recall now measures what user actually asked             │
└────────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────────┐
│  Fix 2: JSON extraction retry                                 │
│  app/services/structmem_service.py                            │
│    extract_and_store(): try parse JSON, on fail retry 1x     │
│    with stricter prompt, on 2nd fail log + skip              │
│    → No more silent memory loss                               │
└────────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────────┐
│  Fix 3: Verbatim memory (last N raw messages)                 │
│  app/services/verbatim_memory.py (new, ~100 LOC)              │
│    - get_recent(user_id, n=20) → query messages table         │
│    - format_for_context() → render as <history> blocks       │
│  app/services/ai_service.py                                   │
│    - _load_verbatim() called alongside _load_structmem()      │
│    - Inject into system prompt when present                   │
│    → 100% verbatim recall for last 20 messages                │
└────────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────────┐
│  Fix 4: 12B for memory recall queries                          │
│  app/core/config.py                                           │
│    - query_type_patterns: add "memory_recall" pattern        │
│      matching "nhớ|trước đó|hồi nãy|đã hỏi"                  │
│    - query_type_model_map: "memory_recall" → "normal"        │
│      (which routes to 12B instead of fast_background)        │
│  → Recall queries use 12B, not E2B (better quality)           │
└────────────────────────────────────────────────────────────────┘
```

---

## 3. Components

### Fix 1: Test re-design

**File:** `scripts/test_comprehensive_30min.py`

**Changes:**
- Add `UserMemoryTracker` class that stores per-user: topics asked, key_facts, question texts
- Phase 1: instead of just sending messages, also record each `persona.user_id → (topic, key_facts)` mapping
- Phase 3: instead of using 10 fixed baseline keywords, use the user's actual recorded key_facts from phase 1
- Add a method to `MetricsCollector` for per-user recall tracking

**Trade-off:** Test now measures "did the model recall what THIS user actually asked" instead of "did the response mention all 10 keywords". More accurate, but more code.

### Fix 2: JSON extraction retry

**File:** `app/services/structmem_service.py`

**Changes:**
- `extract_and_store()` wraps JSON parse in try/except
- On `JSONDecodeError`: retry extraction once with stricter prompt (add "Output ONLY valid JSON, no markdown")
- On 2nd failure: log error + skip (don't block the pipeline)
- Add metric: `structmem_extraction_failures` count

**Effort:** 30 LOC, single function change.

### Fix 3: Verbatim memory service

**New file:** `app/services/verbatim_memory.py` (~100 LOC)

```python
class VerbatimMemory:
    """Get recent raw messages for a user from the messages table."""

    def __init__(self, db_pool, max_messages: int = 20):
        self.db = db_pool
        self.max_messages = max_messages

    async def get_recent(self, user_id: str, session_id: str | None = None) -> list[dict]:
        """Return up to max_messages recent messages for the user."""
        async with self.db.connection() as conn:
            async with conn.cursor() as cur:
                if session_id:
                    await cur.execute(
                        "SELECT role, content, created_at FROM messages "
                        "WHERE user_id = %s AND session_id = %s "
                        "ORDER BY created_at DESC LIMIT %s",
                        (user_id, session_id, self.max_messages),
                    )
                else:
                    await cur.execute(
                        "SELECT role, content, created_at FROM messages "
                        "WHERE user_id = %s "
                        "ORDER BY created_at DESC LIMIT %s",
                        (user_id, self.max_messages),
                    )
                rows = await cur.fetchall()
        return [{"role": r[0], "content": r[1], "ts": r[2]} for r in rows]

    def format_for_context(self, messages: list[dict]) -> str:
        """Render as a <verbatim_history> block for system prompt."""
        if not messages:
            return ""
        lines = ["<verbatim_history>"]
        for m in reversed(messages):  # chronological
            lines.append(f"[{m['ts']}] {m['role']}: {m['content'][:200]}")
        lines.append("</verbatim_history>")
        return "\n".join(lines)
```

**Modified:** `app/services/ai_service.py`

- In chat flow, after `_load_structmem()`, call `verbatim_memory.get_recent(user_id, session_id)`
- Inject formatted text into system prompt

**Trade-off:** Adds 200-1000 chars to system prompt. May slow inference slightly but improves recall dramatically.

### Fix 4: 12B for memory recall

**File:** `app/core/config.py` (config only, no code)

**Changes:**
- Add to `query_type_patterns["memory_recall"]`: `[r"\b(nhớ|trước đó|hồi nãy|đã hỏi|nhắc lại)\b"]`
- Add to `query_type_model_map["memory_recall"]`: `"normal"` (which routes to 12B instead of `fast_background`)

**How it works:**
- Query classifier detects "Bạn còn nhớ..." → intent type "memory_recall"
- `query_type_model_map["memory_recall"] = "normal"` → bypasses `fast_background` route
- Routes to default model (12B Q4) with proper ctx

**Trade-off:** Recall queries now use 12B (~2x slower than E2B). Acceptable for memory-bound workloads.

---

## 4. Data flow (Fix 3 + 4 integrated)

```
User: "Bạn còn nhớ tôi đã hỏi gì trong cuộc trò chuyện trước không?"
  ↓
ai_service.chat(req)
  ↓
  # Query classifier detects "nhớ" → intent = "memory_recall"
  # query_type_model_map["memory_recall"] = "normal" → routes to 12B (not E2B)
  ↓
  # Load memory
  structmem_items = _load_structmem(user_id, query)  # 4 SPO triples
  verbatim_history = verbatim_memory.get_recent(user_id, session_id)  # last 20 messages
  ↓
  # Inject into system prompt
  system_prompt += structmem_items.format() + verbatim_history.format_for_context()
  ↓
  # Call 12B (not E2B) with full context
  response = await _call_llm(provider=12B, prompt=system_prompt + user_msg)
  ↓
  return response
```

---

## 5. Success criteria

| Metric | Before | Target |
|---|---|---|
| Memory recall (test fix only) | 28.3% | **≥70%** |
| Memory recall (all 4 fixes + 12B) | - | **≥80%** |
| Invalid JSON warnings | 1+ per test | **0** |
| Verbatim recall (last 5 messages) | partial | **100%** |
| 12B used for memory_recall queries | E2B | **12B Q4** (verify in response model field) |

---

## 6. Error handling

| Failure mode | Handling |
|---|---|
| JSON extraction fails twice | Log error, skip this episode, continue (don't block pipeline) |
| Verbatim memory query fails | Log warning, skip injection, continue with structmem only |
| 12B unavailable | Falls back to E2B (existing route logic) |
| Test fix: user has no key_facts recorded | Default to baseline 10 keywords |
| Verbatim memory returns >20 messages | Truncate to 20 (oldest dropped) |

---

## 7. Out of scope

- ❌ Replace E2B with 12B for all queries (would slow all traffic)
- ❌ Vector search over message history (semantic retrieval on raw messages)
- ❌ Long-term compression of messages (only first 20 retained)
- ❌ Multi-modal memory (image content in messages)
- ❌ Cross-session memory (verbatim is per-session only)

---

## 8. File changes summary

**New files:**
- `app/services/verbatim_memory.py` (~100 LOC)

**Modified files:**
- `scripts/test_comprehensive_30min.py` (per-user key_facts tracking, ~80 LOC)
- `app/services/structmem_service.py` (JSON retry, ~30 LOC)
- `app/services/ai_service.py` (verbatim memory integration, ~30 LOC)
- `app/core/config.py` (memory_recall query type, 4 lines)
- `tests/unit/test_verbatim_memory.py` (~80 LOC, new)

**Total:** ~320 LOC across 5 files.

---

## 9. Open questions

None. All clarified during brainstorming.

Key decisions:
- Test fix: track per-user key_facts from phase 1 (most impactful)
- Verbatim memory: query existing messages table (no new storage)
- 12B recall: add query_type_patterns entry, no code changes
- JSON retry: 1 retry with stricter prompt, then skip
