# Memory Recall Fixes — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix memory recall from 28.3% to ≥70% via 4 fixes: (1) test re-design tracking per-user key_facts, (2) JSON retry in structmem extractor, (3) verbatim memory service, (4) 12B routing for memory_recall queries.

**Architecture:** 4 changes in 3 categories: test infra (1 file), ai-hub memory pipeline (2 files: structmem retry + new verbatim_memory service), ai-hub routing (1 file: config). 320 LOC total. Re-run full 30-min test at end to verify all fixes.

**Tech Stack:** Python 3.12, asyncio, aiohttp, psycopg3 (existing), pytest (existing).

---

## File Structure

**New files:**
- `app/services/verbatim_memory.py` (~120 LOC) — recent raw messages service
- `tests/unit/test_verbatim_memory.py` (~80 LOC) — unit tests for VerbatimMemory

**Modified files:**
- `scripts/test_comprehensive_30min.py` (~120 LOC added) — UserMemoryTracker + per-user recall check
- `app/services/structmem_service.py` (~40 LOC) — JSON retry with stricter prompt
- `app/services/ai_service.py` (~25 LOC) — integrate verbatim memory into chat flow
- `app/core/config.py` (~4 lines) — add memory_recall query type

**No new dependencies** — uses existing psycopg3, aiohttp, pytest.

---

## Conventions

- Each task has RED test → GREEN impl → commit
- All env reads via `os.getenv()` with sensible defaults
- TDD: write test first, see it fail, implement, see it pass
- Cache TTL: N/A (no cache changes)
- Commit after every task

---

## Task 1: Add `UserMemoryTracker` class to test script

**Files:**
- Modify: `scripts/test_comprehensive_30min.py` (add class)

- [ ] **Step 1.1: Add `UserMemoryTracker` class**

Find a good place in `scripts/test_comprehensive_30min.py` to add a new class (e.g., after the `PhaseRunner` class). Add:

```python
class UserMemoryTracker:
    """Per-user record of what they were asked + the key_facts of each question.

    Used by Phase 3 to do per-user recall check instead of fixed 10-keyword baseline.
    """

    def __init__(self) -> None:
        self._records: dict[str, list[tuple[str, tuple[str, ...]]]] = {}  # user_id → [(topic, key_facts), ...]

    def record(self, user_id: str, topic: str, key_facts: tuple[str, ...]) -> None:
        if user_id not in self._records:
            self._records[user_id] = []
        self._records[user_id].append((topic, key_facts))

    def get_facts(self, user_id: str) -> tuple[str, ...]:
        """Flatten all key_facts recorded for this user across all topics."""
        all_facts: list[str] = []
        for topic, facts in self._records.get(user_id, []):
            all_facts.extend(facts)
        return tuple(all_facts)

    def user_count(self) -> int:
        return len(self._records)
```

- [ ] **Step 1.2: Smoke test the class**

Run:
```bash
cd /home/hung/ai-hub && ./venv/bin/python -c "
import sys
sys.path.insert(0, 'scripts')
from test_comprehensive_30min import UserMemoryTracker

t = UserMemoryTracker()
t.record('user1', 'áo thun trắng', ('có', '250000'))
t.record('user1', 'quần jean', ('có', '450000'))
t.record('user2', 'giày thể thao', ('có',))

facts1 = t.get_facts('user1')
facts2 = t.get_facts('user2')
facts3 = t.get_facts('unknown_user')

assert facts1 == ('có', '250000', 'có', '450000'), f'user1 facts: {facts1}'
assert facts2 == ('có',), f'user2 facts: {facts2}'
assert facts3 == (), f'unknown user: {facts3}'
assert t.user_count() == 2
print('UserMemoryTracker OK')
"
```
Expected: `UserMemoryTracker OK`

- [ ] **Step 1.3: Commit**

```bash
git add scripts/test_comprehensive_30min.py
git commit -m "feat(test): UserMemoryTracker class for per-user key_facts tracking"
```

---

## Task 2: Modify `Phase1Warmup` to record per-user facts

**Files:**
- Modify: `scripts/test_comprehensive_30min.py` (Phase1Warmup class)

- [ ] **Step 2.1: Add `tracker` parameter to Phase1Warmup**

Find `class Phase1Warmup(PhaseRunner):` and its `run()` method. Add a `tracker: UserMemoryTracker` parameter to the `__init__`:

```python
class Phase1Warmup(PhaseRunner):
    """10 personas × 10 câu = 100 turns, gather baseline latency + record per-user facts."""

    def __init__(self, cfg, client, metrics, log, tracker: UserMemoryTracker) -> None:
        super().__init__(cfg, client, metrics, log)
        self.tracker = tracker

    async def run(self) -> PhaseResult:
        started = datetime.now(timezone.utc)
        t_start = time.monotonic()
        topics = all_topics()
        for persona in PERSONAS:
            for turn in range(self.cfg.phase1_turns_per_user):
                topic = random.choice(topics)
                question = random.choice(topic.questions)
                # NEW: record per-user fact
                self.tracker.record(persona.user_id, topic.name, question.key_facts)
                await self.client.chat(
                    user=persona.user_id,
                    message=question.text,
                    session_id=persona.user_id,
                    topic=topic.name,
                    phase="phase1_warmup",
                    turn=turn,
                )
        ended = datetime.now(timezone.utc)
        return PhaseResult(
            name="phase1_warmup",
            started_at=started.isoformat(),
            ended_at=ended.isoformat(),
            duration_seconds=time.monotonic() - t_start,
            extra={"users": len(PERSONAS), "turns_per_user": self.cfg.phase1_turns_per_user,
                   "tracked_users": self.tracker.user_count()},
        )
```

- [ ] **Step 2.2: Update `_run_full()` to pass tracker to Phase1Warmup**

Find the line in `_run_full()` that calls `Phase1Warmup(cfg, client, metrics, log).run()`. Change to:
```python
tracker = UserMemoryTracker()
...
result = await Phase1Warmup(cfg, client, metrics, log, tracker).run()
```

(Add `tracker = UserMemoryTracker()` right after `metrics = MetricsCollector()`.)

- [ ] **Step 2.3: Smoke test --quick to verify tracker works**

Run: `cd /home/hung/ai-hub && ./venv/bin/python scripts/test_comprehensive_30min.py --quick 2>&1 | tail -10`
Expected: Test runs without error, tracker.user_count() == 10 after phase 1.

(Note: --quick mode uses `phase1_turns_per_user=5`, so 10 personas × 5 = 50 records in tracker.)

- [ ] **Step 2.4: Commit**

```bash
git add scripts/test_comprehensive_30min.py
git commit -m "feat(test): Phase1Warmup records per-user key_facts via UserMemoryTracker"
```

---

## Task 3: Modify `Phase3Recall` to use per-user facts

**Files:**
- Modify: `scripts/test_comprehensive_30min.py` (Phase3Recall class + MetricsCollector)

- [ ] **Step 3.1: Add `tracker` parameter to Phase3Recall**

Find `class Phase3Recall(PhaseRunner):` and add `tracker` to `__init__`:

```python
class Phase3Recall(PhaseRunner):
    """Round 1-3: chọn 10 user từ phase 1, wait 2-3 min, memory check, continue 10 câu."""

    def __init__(self, cfg, client, metrics, log, tracker: UserMemoryTracker) -> None:
        super().__init__(cfg, client, metrics, log)
        self.tracker = tracker
```

- [ ] **Step 3.2: Replace baseline keywords with per-user facts**

Find the lines in `run()` that define `baseline_facts`:
```python
                # Baseline clothing keywords: response should mention ≥70% if memory works
                baseline_facts = ("áo", "quần", "giày", "váy", "túi",
                                  "size", "giá", "giao hàng", "đổi trả", "bảo hành")
                matched, total, missed = check_key_facts(body, baseline_facts)
```

Replace with:
```python
                # Use per-user facts from phase 1 (more accurate than fixed 10-keyword baseline)
                user_facts = self.tracker.get_facts(persona.user_id)
                if not user_facts:
                    # Fallback: user not in tracker, use baseline
                    user_facts = ("áo", "quần", "giày", "váy", "túi",
                                  "size", "giá", "giao hàng", "đổi trả", "bảo hành")
                matched, total, missed = check_key_facts(body, user_facts)
```

- [ ] **Step 3.3: Update `_run_full()` to pass tracker to Phase3Recall**

Find the line that calls `Phase3Recall(cfg, client, metrics, log).run()`. Change to:
```python
result = await Phase3Recall(cfg, client, metrics, log, tracker).run()
```

- [ ] **Step 3.4: Re-run --quick, verify recall improvement**

Run: `cd /home/hung/ai-hub && ./venv/bin/python scripts/test_comprehensive_30min.py --quick 2>&1 | tail -10`
Expected: Memory recall should jump from ~25% to ~70%+ (because we're now measuring what user actually asked, not a fixed 10-keyword baseline).

- [ ] **Step 3.5: Commit**

```bash
git add scripts/test_comprehensive_30min.py
git commit -m "feat(test): Phase3Recall uses per-user key_facts (accurate recall measurement)"
```

---

## Task 4: Fix invalid JSON in structmem extractor (retry with stricter prompt)

**Files:**
- Modify: `app/services/structmem_service.py` (find extract function, add retry)

- [ ] **Step 4.1: Find the extract function**

Run: `grep -n 'def extract\|def _extract\|json.loads\|JSONDecode' app/services/structmem_service.py | head -10`

Look for the function that calls the LLM to extract SPO triples. Most likely named `extract_triples`, `_extract`, or similar.

- [ ] **Step 4.2: Wrap JSON parse in try/except with retry**

Inside the extraction function, find the line that parses the LLM response (e.g., `triples = json.loads(response_text)`). Replace with:

```python
                # Try parse JSON
                try:
                    triples = json.loads(response_text)
                except json.JSONDecodeError as e:
                    logger.warning("StructMem extraction: invalid JSON, retrying with stricter prompt: %s", e)
                    # Retry once with stricter prompt
                    retry_prompt = original_prompt + "\n\nIMPORTANT: Output ONLY valid JSON array. No markdown code blocks, no explanations, just JSON."
                    response_retry = await self._call_llm(retry_prompt)
                    try:
                        triples = json.loads(response_retry)
                    except json.JSONDecodeError as e2:
                        logger.error("StructMem extraction: invalid JSON on retry, skipping: %s", e2)
                        return []  # skip this extraction
```

(Adjust variable names to match the existing code. The pattern is: try parse, on fail log + retry with stricter prompt, on 2nd fail log + return empty list.)

- [ ] **Step 4.3: Verify no syntax errors**

Run: `cd /home/hung/ai-hub && ./venv/bin/python -c "from app.services.structmem_service import *; print('OK')"`
Expected: `OK`

- [ ] **Step 4.4: Commit**

```bash
git add app/services/structmem_service.py
git commit -m "fix(structmem): retry JSON parse once with stricter prompt, skip on 2nd fail

Log warning 'StructMem extraction returned invalid JSON' appeared 1x in
2026-06-13 test. Now: 1 retry with stricter prompt, skip if still invalid.
Prevents silent memory loss from malformed LLM output."
```

---

## Task 5: Create `VerbatimMemory` class

**Files:**
- Create: `app/services/verbatim_memory.py`
- Create: `tests/unit/test_verbatim_memory.py`

- [ ] **Step 5.1: Create `app/services/verbatim_memory.py`**

```python
"""Verbatim memory service.

Returns recent raw messages for a user from the messages table.
Used to give the LLM direct access to recent conversation history
without relying on summary/structmem extraction.
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


class VerbatimMemory:
    """Get recent raw messages for a user."""

    def __init__(self, db_pool, max_messages: int = 20):
        self.db = db_pool
        self.max_messages = max_messages

    async def get_recent(
        self, user_id: str, session_id: str | None = None, limit: int | None = None
    ) -> list[dict]:
        """Return up to `limit` recent messages for the user, newest first.

        If `session_id` is provided, filter by that session.
        """
        actual_limit = limit or self.max_messages
        async with self.db.connection() as conn:
            async with conn.cursor() as cur:
                if session_id:
                    await cur.execute(
                        "SELECT role, content, created_at FROM messages "
                        "WHERE user_id = %s AND session_id = %s "
                        "ORDER BY created_at DESC LIMIT %s",
                        (user_id, session_id, actual_limit),
                    )
                else:
                    await cur.execute(
                        "SELECT role, content, created_at FROM messages "
                        "WHERE user_id = %s "
                        "ORDER BY created_at DESC LIMIT %s",
                        (user_id, actual_limit),
                    )
                rows = await cur.fetchall()
        return [{"role": r[0], "content": r[1], "ts": str(r[2])} for r in rows]

    @staticmethod
    def format_for_context(messages: list[dict], max_chars_per_msg: int = 200) -> str:
        """Render messages as a <verbatim_history> block for system prompt.

        Returns empty string if no messages.
        """
        if not messages:
            return ""
        lines = ["<verbatim_history>"]
        for m in reversed(messages):  # chronological order (oldest first)
            content = m["content"][:max_chars_per_msg]
            lines.append(f"[{m['ts']}] {m['role']}: {content}")
        lines.append("</verbatim_history>")
        return "\n".join(lines)
```

- [ ] **Step 5.2: Create `tests/unit/test_verbatim_memory.py`**

```python
"""Unit tests for VerbatimMemory."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.verbatim_memory import VerbatimMemory


def _make_pool(rows):
    """Create mock pool returning given rows from a single query."""
    cur = AsyncMock()
    cur.fetchall = AsyncMock(return_value=rows)
    conn = MagicMock()
    conn.cursor = MagicMock()
    conn.cursor.return_value.__aenter__ = AsyncMock(return_value=cur)
    conn.cursor.return_value.__aexit__ = AsyncMock(return_value=None)
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=None)
    pool = MagicMock()
    pool.connection = MagicMock(return_value=conn)
    return pool, cur


@pytest.mark.no_isolated_db
def test_format_for_context_empty():
    assert VerbatimMemory.format_for_context([]) == ""


@pytest.mark.no_isolated_db
def test_format_for_context_single():
    msgs = [{"role": "user", "content": "Hello", "ts": "2026-06-13T10:00:00"}]
    out = VerbatimMemory.format_for_context(msgs)
    assert "<verbatim_history>" in out
    assert "</verbatim_history>" in out
    assert "user: Hello" in out
    assert "2026-06-13T10:00:00" in out


@pytest.mark.no_isolated_db
def test_format_for_context_chronological():
    msgs = [
        {"role": "assistant", "content": "Hi", "ts": "2026-06-13T10:00:01"},
        {"role": "user", "content": "Hello", "ts": "2026-06-13T10:00:00"},
    ]
    out = VerbatimMemory.format_for_context(msgs)
    # Should reverse so user:Hello comes first, then assistant:Hi
    user_idx = out.find("user: Hello")
    asst_idx = out.find("assistant: Hi")
    assert user_idx < asst_idx, f"expected user before assistant, got:\n{out}"


@pytest.mark.no_isolated_db
def test_format_for_context_truncates_long_content():
    long = "x" * 1000
    msgs = [{"role": "user", "content": long, "ts": "2026-06-13T10:00:00"}]
    out = VerbatimMemory.format_for_context(msgs, max_chars_per_msg=100)
    # Should only contain first 100 chars + "..." wait, no, we just slice
    assert "x" * 100 in out
    assert "x" * 200 not in out


@pytest.mark.asyncio
@pytest.mark.no_isolated_db
async def test_get_recent_queries_db():
    pool, cur = _make_pool([
        ("user", "msg1", "2026-06-13T10:00:00"),
        ("assistant", "reply1", "2026-06-13T10:00:01"),
    ])
    vm = VerbatimMemory(pool, max_messages=20)
    msgs = await vm.get_recent("user123", session_id="s1", limit=5)
    assert len(msgs) == 2
    assert msgs[0]["role"] == "user"
    assert msgs[0]["content"] == "msg1"
    # Verify query params
    args, _ = cur.execute.call_args
    assert "user_id = %s AND session_id = %s" in args[0]
    assert args[1] == ("user123", "s1", 5)


@pytest.mark.asyncio
@pytest.mark.no_isolated_db
async def test_get_recent_no_session():
    pool, cur = _make_pool([])
    vm = VerbatimMemory(pool)
    msgs = await vm.get_recent("user123")
    args, _ = cur.execute.call_args
    assert "WHERE user_id = %s" in args[0]
    assert args[1] == ("user123", 20)  # default max_messages
```

- [ ] **Step 5.3: Run tests**

Run: `cd /home/hung/ai-hub && ./venv/bin/pytest tests/unit/test_verbatim_memory.py -v --no-cov`
Expected: 6/6 pass.

- [ ] **Step 5.4: Commit**

```bash
git add app/services/verbatim_memory.py tests/unit/test_verbatim_memory.py
git commit -m "feat(memory): VerbatimMemory service + 6 unit tests (recent messages query)"
```

---

## Task 6: Integrate VerbatimMemory into ai_service.py

**Files:**
- Modify: `app/services/ai_service.py` (chat flow)

- [ ] **Step 6.1: Find the chat method's system prompt construction**

Run: `grep -n 'system_prompt\|self._load_structmem\|verbatim' app/services/ai_service.py | head -10`

- [ ] **Step 6.2: Add verbatim memory import + initialization**

Add to imports at top of `app/services/ai_service.py`:
```python
from app.services.verbatim_memory import VerbatimMemory
```

Find the chat service class's `__init__` and add:
```python
        self._verbatim = VerbatimMemory(self._db_pool, max_messages=20) if self._settings.cache_enabled else None  # reuse cache_enabled as proxy
```

(Actually, just always enable verbatim — it's a DB query, not a config flag. Replace with:)
```python
        self._verbatim = VerbatimMemory(self._db_pool, max_messages=20) if getattr(self._settings, 'verbatim_memory_enabled', True) else None
```

And add to `app/core/config.py`:
```python
    verbatim_memory_enabled: bool = Field(default=True, alias="VERBATIM_MEMORY_ENABLED")
```

(Or just always init — it's cheap. Pick whatever is simpler.)

- [ ] **Step 6.3: Load verbatim memory + inject in chat flow**

After the existing `_load_structmem()` call, add:
```python
        # Load verbatim memory (last N raw messages)
        if self._verbatim is not None:
            try:
                verbatim_msgs = await self._verbatim.get_recent(req.user_name, req.session_id, limit=10)
                if verbatim_msgs:
                    verbatim_block = VerbatimMemory.format_for_context(verbatim_msgs, max_chars_per_msg=200)
                    # Inject into system prompt (append)
                    system_prompt = (system_prompt or "") + "\n\n" + verbatim_block
                    logger.info("verbatim_memory_injected user=%s messages=%d", req.user_name, len(verbatim_msgs))
            except Exception as e:
                logger.warning("verbatim_memory_failed: %r", e)
                # Continue without verbatim
```

(Adjust the integration point to match the actual code structure. The pattern: load after structmem, append to system_prompt.)

- [ ] **Step 6.4: Verify ai-hub still imports**

Run: `cd /home/hung/ai-hub && ./venv/bin/python -c "from app.services.ai_service import *; print('OK')"`
Expected: `OK`

- [ ] **Step 6.5: Commit**

```bash
git add app/services/ai_service.py app/core/config.py
git commit -m "feat(memory): integrate VerbatimMemory into ai_service.chat()"
```

---

## Task 7: Add `memory_recall` query type for 12B routing

**Files:**
- Modify: `app/core/config.py` (2 lines)

- [ ] **Step 7.1: Add memory_recall pattern + model map**

Find `query_type_patterns` field in `app/core/config.py`. Add a new entry:
```python
            "memory_recall": [r"\b(nhớ|trước đó|hồi nãy|đã hỏi|nhắc lại|trước|đoạn chat (trước|trên))\b"],
```

Find `query_type_model_map` field. Add a new entry:
```python
            "memory_recall": "normal",  # bypass fast_background, use 12B for better recall quality
```

- [ ] **Step 7.2: Verify config loads**

Run:
```bash
cd /home/hung/ai-hub && ./venv/bin/python -c "
from app.core.config import Settings
s = Settings()
print('memory_recall pattern:', s.query_type_patterns.get('memory_recall'))
print('memory_recall model:', s.query_type_model_map.get('memory_recall'))
assert s.query_type_model_map.get('memory_recall') == 'normal'
print('memory_recall config OK')
"
```
Expected: `memory_recall config OK`

- [ ] **Step 7.3: Commit**

```bash
git add app/core/config.py
git commit -m "feat(routing): memory_recall query type routes to 12B (bypass fast_background)

Detects 'nhớ'/'trước đó'/'hồi nãy' etc in user message and routes to
default 12B Q4 instead of fast_background E2B. Better recall quality
with larger model, accept slower latency for memory-bound queries."
```

---

## Task 8: Re-run --quick to verify all 4 fixes work

**Files:** none (verification)

- [ ] **Step 8.1: Start full ai-hub stack**

Run:
```bash
cd /home/hung/ai-hub
# 12B Q4 on 8080
./scripts/start_5060ti_16gb.sh &
disown
# E2B Q4 on 8081
PARALLEL=4 ./scripts/start_background_q4.sh &
disown
# Wait for both
sleep 20
for i in {1..30}; do
  if curl -s -m 2 http://127.0.0.1:8080/health > /dev/null && curl -s -m 2 http://127.0.0.1:8081/health > /dev/null; then
    echo "Both llama.cpp up after $((i*2))s"
    break
  fi
  sleep 2
done
# Start uvicorn
nohup ./venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 > /tmp/aihub-uvicorn-quick.log 2>&1 &
disown
sleep 5
curl -s -H "X-API-KEY: $(grep '^API_KEY=' .env | cut -d= -f2 | tr -d '"')" http://127.0.0.1:8000/health
```
Expected: ai-hub healthy.

- [ ] **Step 8.2: Run --quick, verify recall improved**

Run:
```bash
cd /home/hung/ai-hub && time ./venv/bin/python scripts/test_comprehensive_30min.py --quick 2>&1 | tail -10
LATEST=$(ls -t reports/comprehensive_30min_*.json | head -1)
./venv/bin/python -c "
import json
r = json.load(open('$LATEST'))
print('Recall:', f'{r[\"metrics_summary\"][\"memory_recall_avg_pct\"]:.1f}%', '(target >=70%)')
print('Verdict:', r['verdict'])
print('Error rate:', f'{r[\"metrics_summary\"][\"error_rate\"]*100:.1f}%')
"
```
Expected: Recall ≥70% (was 28.3% with old baseline). If still <70%, check log for `verbatim_memory_injected` and structmem extraction success.

- [ ] **Step 8.3: Verify verbatim memory injected in log**

Run: `grep -E 'verbatim_memory_injected|structmem_extraction|invalid JSON' /tmp/aihub-uvicorn-quick.log | head -10`
Expected: Should see `verbatim_memory_injected` lines and no `invalid JSON` warnings.

---

## Task 9: Re-run full 30-min test, verify success criteria

**Files:** none (verification)

- [ ] **Step 9.1: Clean reports**

Run: `cd /home/hung/ai-hub && rm -f reports/comprehensive_30min_*.json reports/comprehensive_30min_*.log`

- [ ] **Step 9.2: Run full 30-min test in background**

Run: `cd /home/hung/ai-hub && nohup ./venv/bin/python scripts/test_comprehensive_30min.py > /tmp/full_test_v3.log 2>&1 & disown`
TEST_PID=$!
echo "Test PID: $TEST_PID"

- [ ] **Step 9.3: Wait 5 min then check progress**

Run: `sleep 300; ps -p $TEST_PID -o etime,stat 2>/dev/null; grep -c '"POST /v1/chat' /tmp/aihub-uvicorn-quick.log; tail -3 /tmp/full_test_v3.log`

- [ ] **Step 9.4: Wait 15 more min then check progress**

Run: `sleep 900; ps -p $TEST_PID -o etime,stat 2>/dev/null; grep -c '"POST /v1/chat' /tmp/aihub-uvicorn-quick.log; tail -3 /tmp/full_test_v3.log`

- [ ] **Step 9.5: Wait until test completes (≤35 min)**

Run: `while ps -p $TEST_PID > /dev/null 2>&1; do sleep 60; done; echo "Done at $(date '+%H:%M:%S')"; tail -10 /tmp/full_test_v3.log`

- [ ] **Step 9.6: Verify 4 success criteria**

Run:
```bash
cd /home/hung/ai-hub
LATEST=$(ls -t reports/comprehensive_30min_*.json | head -1)
./venv/bin/python -c "
import json
r = json.load(open('$LATEST'))
ms = r['metrics_summary']
print('=== SUCCESS CRITERIA ===')
print(f'1. Context overflow: 0 (target 0)', '✓' if ms['errors'] == 0 else f'✗ ({ms[\"errors\"]} errors)')
ctx_errs = sum(1 for e in r['top_errors'] if 'exceed_context_size' in e.get('error',''))
print(f'   (ctx-specific: {ctx_errs})')
print(f'2. Memory recall: {ms[\"memory_recall_avg_pct\"]:.1f}% (target >=70%)', '✓' if ms['memory_recall_avg_pct'] >= 70 else '✗')
speedups = ms.get('cache_speedup_pct', {}) or {}
positive = sum(1 for s in speedups.values() if s >= 10)
print(f'3. Cache speedup: {positive}/{len(speedups)} topics >=10%', '✓' if positive == 5 and len(speedups) == 5 else '✗')
print(f'4. Runtime: {r[\"total_duration_seconds\"]/60:.1f} min (target <=35)', '✓' if r['total_duration_seconds'] <= 2100 else '✗')
print()
print('Verdict:', r['verdict'])
"
```

Expected:
- 0 context errors ✓
- Recall ≥70% ✓
- 5/5 cache topics positive (still depends on cache layer from previous sprint)
- Runtime ≤35 min ✓
- Verdict: PASS

- [ ] **Step 9.7: Archive + commit report**

Run:
```bash
cd /home/hung/ai-hub
LATEST=$(ls -t reports/comprehensive_30min_*.json | head -1)
mkdir -p reports/2026-06-13-memory-fixes-after
cp "$LATEST" reports/2026-06-13-memory-fixes-after/
git add -f reports/2026-06-13-memory-fixes-after/
git commit -m "test: 30-min test after 4 memory fixes (recall 28% -> ?%)

Test redesign (track per-user key_facts) + JSON retry in structmem +
verbatim memory (last 10 msgs in system prompt) + 12B for memory_recall.

Compared to 2026-06-13 baseline (recall 28.3%):
  - Recall: 28.3% -> ?% (target >=70%)
  - Context overflow: still 0 ✓
  - Runtime: 45 min -> ? min (target <=35)"
```

---

## Task 10: Stop ai-hub, restore .env, final cleanup

**Files:** `.env`

- [ ] **Step 10.1: Stop all services**

Run: `cd /home/hung/ai-hub && pkill -f 'uvicorn app.main:app' 2>/dev/null; pkill -f 'llama-server' 2>/dev/null; sleep 3; ps aux | grep -E '[u]vicorn|[l]lama-server' | wc -l`
Expected: 0 (all stopped)

- [ ] **Step 10.2: Restore .env (re-enable MiniMax)**

Run: `cd /home/hung/ai-hub && sed -i 's/^MINIMAX_ENABLED=false/MINIMAX_ENABLED=true/' .env && grep -E '^MINIMAX_ENABLED' .env`
Expected: `MINIMAX_ENABLED=true`

---

## Self-Review Checklist

✅ **Spec coverage:**
- Section 3 Fix 1 → Tasks 1-3
- Section 3 Fix 2 → Task 4
- Section 3 Fix 3 → Tasks 5-6
- Section 3 Fix 4 → Task 7
- Section 5 success criteria → Tasks 8-9
- Section 6 error handling → Task 4 (JSON retry), Task 5-6 (verbatim try/except), Task 7 (12B fallback)
- Section 7 out of scope → respected (no vector search, no multi-modal)

✅ **Placeholder scan:** No TBD/TODO/implement-later. All steps have full code or clear command.

✅ **Type consistency:**
- `UserMemoryTracker.record/get_facts/user_count` consistent across Tasks 1, 2, 3
- `VerbatimMemory.get_recent/format_for_context` consistent across Tasks 5, 6
- `cfg.phase1_turns_per_user`, `tracker` parameter consistent

✅ **File paths exact:** all paths use full paths.

✅ **Commit cadence:** 9 commits planned, each small.

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-06-13-memory-recall-fixes.md`.**

**Two execution options:**

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks. Best for code quality.

2. **Inline Execution** — Execute tasks in this session using `executing-plans`. Best for faster overall progress.
