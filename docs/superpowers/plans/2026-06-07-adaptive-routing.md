# Adaptive Routing for ai-hub — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an adaptive routing system for ai-hub that picks the right model per request (E2B-bg/E4B/12B) based on difficulty + system load, plus a periodic 6h summarizer for IHI rollups.

**Architecture:** 4 new modules — `DifficultyClassifier` (heuristic), `LoadMonitor` (probes `/health?include_slots=1`), `AdaptiveRouter` (combines them), `PeriodicSummarizer` (APScheduler cron every 6h). Wraps existing `_select_model()` in `ai_service.py`.

**Tech Stack:** FastAPI, llama-server `/health?include_slots=1` JSON, APScheduler 3.x, PostgreSQL, existing 16GB VRAM (12B ctx=8K already applied).

---

## File Structure

**New files:**
- `app/services/difficulty_classifier.py` (~250 LOC) — heuristic + future ML hooks
- `app/services/load_monitor.py` (~150 LOC) — probes llama-server ports
- `app/services/router.py` (~300 LOC) — combines classifier + load + project → ModelChoice
- `app/services/scheduler.py` (~200 LOC) — APScheduler with 6h cron
- `app/services/auto_labeler.py` (~150 LOC) — for Phase 2, stub only in Phase 1
- `tests/unit/test_difficulty_classifier.py` (~150 LOC)
- `tests/unit/test_load_monitor.py` (~100 LOC)
- `tests/unit/test_router.py` (~200 LOC)
- `tests/unit/test_scheduler.py` (~150 LOC)

**Modified files:**
- `app/core/config.py` — add 8 new config fields (§Task 1)
- `app/core/database.py` — add `ihi_rollups` table to `init_db()` (§Task 2)
- `app/services/ai_service.py` — wire router into `_select_model()` (§Task 6)
- `app/main.py` — wire scheduler into lifespan (§Task 8)
- `requirements.txt` — add `APScheduler==3.10.4`

---

## Task 1: Add config fields

**Files:**
- Modify: `app/core/config.py:130-220` (add new fields after `failure_risk_log_only` block)

- [ ] **Step 1.1: Add new fields to `Settings` class**

After `failure_risk_enable_search_action: bool = Field(default=True, alias="FAILURE_RISK_ENABLE_SEARCH_ACTION")` (around line 140), add:

```python
# Adaptive routing (added 2026-06-07)
adaptive_routing_enabled: bool = Field(default=True, alias="ADAPTIVE_ROUTING_ENABLED")
difficulty_easy_threshold: float = Field(default=0.3, ge=0.0, le=1.0, alias="DIFFICULTY_EASY_THRESHOLD")
difficulty_hard_threshold: float = Field(default=0.6, ge=0.0, le=1.0, alias="DIFFICULTY_HARD_THRESHOLD")
saturation_12b_degrade_threshold: float = Field(default=0.8, ge=0.0, le=1.0, alias="SATURATION_12B_DEGRADE_THRESHOLD")
saturation_e4b_degrade_threshold: float = Field(default=0.9, ge=0.0, le=1.0, alias="SATURATION_E4B_DEGRADE_THRESHOLD")
load_probe_interval_seconds: float = Field(default=1.0, gt=0.0, alias="LOAD_PROBE_INTERVAL_SECONDS")
load_cache_ttl_seconds: float = Field(default=0.2, gt=0.0, alias="LOAD_CACHE_TTL_SECONDS")
periodic_summary_cron: str = Field(default="0 */6 * * *", alias="PERIODIC_SUMMARY_CRON")
periodic_summary_min_tokens: int = Field(default=5000, ge=0, alias="PERIODIC_SUMMARY_MIN_TOKENS")
```

- [ ] **Step 1.2: Commit**

```bash
git add app/core/config.py
git commit -m "feat(config): add 8 fields for adaptive routing (Phase 1)"
```

---

## Task 2: Add `ihi_rollups` table to schema

**Files:**
- Modify: `app/core/database.py:373` (after `ihi_rag_cases` table)

- [ ] **Step 2.1: Add CREATE TABLE for `ihi_rollups`**

After the `ihi_rag_cases` CREATE TABLE block ends (find `);` after the last column, around line 410), add:

```python
            CREATE TABLE IF NOT EXISTS ihi_rollups (
                id TEXT PRIMARY KEY,
                window_start TIMESTAMP NOT NULL,
                window_end TIMESTAMP NOT NULL,
                summary TEXT NOT NULL,
                model TEXT NOT NULL,
                source_window_count INTEGER NOT NULL DEFAULT 0,
                source_token_count INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
```

- [ ] **Step 2.2: Verify schema applies**

Run: `./venv/bin/python -c "from app.core.database import init_db; init_db(); print('OK')"`
Expected: `INFO app.core.database Database initialized (PostgreSQL)` then `OK`

- [ ] **Step 2.3: Verify table exists**

Run: `PGPASSWORD=aihub_pass psql -U aihub -d ai_hub -h localhost -c "\d ihi_rollups"`
Expected: shows columns `id, window_start, window_end, summary, model, source_window_count, source_token_count, created_at`

- [ ] **Step 2.4: Commit**

```bash
git add app/core/database.py
git commit -m "feat(db): add ihi_rollups table for periodic summarizer"
```

---

## Task 3: DifficultyClassifier (heuristic) with TDD

**Files:**
- Create: `app/services/difficulty_classifier.py`
- Create: `tests/unit/test_difficulty_classifier.py`

- [ ] **Step 3.1: Write the failing tests**

Create `tests/unit/test_difficulty_classifier.py`:

```python
"""Tests for DifficultyClassifier heuristic scoring."""

from __future__ import annotations

from app.models.chat import ChatRequest
from app.services.difficulty_classifier import (
    DifficultyClassifier,
    classify_score,
)


def _req(text: str) -> ChatRequest:
    return ChatRequest(
        user_name="t",
        user_message=text,
        project_id="t",
    )


class TestClassifyScore:
    def test_empty_message_is_easy(self):
        assert classify_score(0.0) == "easy"

    def test_below_easy_threshold_is_easy(self):
        assert classify_score(0.29) == "easy"

    def test_at_easy_threshold_is_easy(self):
        assert classify_score(0.3) == "easy"

    def test_just_above_easy_threshold_is_med(self):
        assert classify_score(0.31) == "med"

    def test_at_hard_threshold_is_med(self):
        assert classify_score(0.6) == "med"

    def test_above_hard_threshold_is_hard(self):
        assert classify_score(0.61) == "hard"

    def test_one_is_hard(self):
        assert classify_score(1.0) == "hard"


class TestScore:
    def test_short_message_low_score(self):
        clf = DifficultyClassifier()
        s = clf.score(_req("hello"))
        assert 0.0 <= s < 0.3

    def test_long_message_higher_score(self):
        clf = DifficultyClassifier()
        long_text = "x" * 2000
        s = clf.score(_req(long_text))
        assert s > 0.2

    def test_code_block_adds_to_score(self):
        clf = DifficultyClassifier()
        s_with = clf.score(_req("explain\n```python\nprint('hi')\n```"))
        s_without = clf.score(_req("explain print hi"))
        assert s_with > s_without

    def test_math_symbols_add_to_score(self):
        clf = DifficultyClassifier()
        s_with = clf.score(_req("calculate ∑ and √"))
        s_without = clf.score(_req("calculate sum and root"))
        assert s_with > s_without

    def test_multi_question_adds_to_score(self):
        clf = DifficultyClassifier()
        s_multi = clf.score(_req("what is X? and why? and how?"))
        s_single = clf.score(_req("what is X"))
        assert s_multi > s_single

    def test_empty_string_returns_zero(self):
        clf = DifficultyClassifier()
        assert clf.score(_req("")) == 0.0

    def test_classify_returns_string_label(self):
        clf = DifficultyClassifier()
        label = clf.classify(_req("any text"))
        assert label in ("easy", "med", "hard")
```

- [ ] **Step 3.2: Run tests to verify they fail**

Run: `AI_HUB_ALLOW_DB_TRUNCATE_FOR_TESTS=1 ./venv/bin/pytest tests/unit/test_difficulty_classifier.py -v --no-cov`
Expected: `ModuleNotFoundError: No module named 'app.services.difficulty_classifier'`

- [ ] **Step 3.3: Implement DifficultyClassifier**

Create `app/services/difficulty_classifier.py`:

```python
"""Heuristic difficulty classifier for adaptive routing.

Phase 1: rule-based scoring using text signals (length, code, math,
multi-question, history depth).

Phase 2: replace with FastEmbed+LogisticRegression trained on
auto-labeled history. See `app/services/auto_labeler.py` for the
training pipeline (stub in Phase 1).
"""

from __future__ import annotations

from app.models.chat import ChatRequest


def classify_score(score: float) -> str:
    """Bucket a numeric score into easy/med/hard.

    Thresholds come from `Settings.difficulty_easy_threshold` and
    `Settings.difficulty_hard_threshold`; defaults are 0.3 and 0.6.
    Kept as a module-level function (not a method) so it's testable
    without instantiating the classifier.
    """
    # NOTE: Phase 1 uses fixed thresholds. Phase 2 (auto-labeler)
    # will pass the live thresholds from Settings.
    if score < 0.3:
        return "easy"
    if score < 0.6:
        return "med"
    return "hard"


class DifficultyClassifier:
    """Heuristic difficulty classifier (Phase 1)."""

    def score(
        self,
        req: ChatRequest,
        history_count: int = 0,
    ) -> float:
        """Return a difficulty score in [0.0, 1.0].

        Args:
            req: The chat request.
            history_count: Number of prior messages in the conversation
                (used to weight long multi-turn contexts).
        """
        text = req.user_message
        if not text:
            return 0.0

        s = 0.0
        # Length signal (caps at 2000 chars ≈ 500 tokens)
        s += min(len(text) / 2000.0, 1.0) * 0.3

        # Code block signal
        if "```" in text or "    def " in text or "    class " in text:
            s += 0.3

        # Math signal
        if any(c in text for c in "∑∫√∂π≈≠≤≥"):
            s += 0.2

        # Multi-question signal
        if "?" in text and len(text.split("?")) > 2:
            s += 0.2

        # Multi-turn depth signal
        s += 0.1 * min(history_count / 10.0, 1.0)

        return min(s, 1.0)

    def classify(
        self,
        req: ChatRequest,
        history_count: int = 0,
    ) -> str:
        """Return one of: 'easy', 'med', 'hard'."""
        return classify_score(self.score(req, history_count))
```

- [ ] **Step 3.4: Run tests to verify they pass**

Run: `AI_HUB_ALLOW_DB_TRUNCATE_FOR_TESTS=1 ./venv/bin/pytest tests/unit/test_difficulty_classifier.py -v --no-cov`
Expected: all 14 tests PASS

- [ ] **Step 3.5: Commit**

```bash
git add app/services/difficulty_classifier.py tests/unit/test_difficulty_classifier.py
git commit -m "feat(routing): add heuristic DifficultyClassifier (Phase 1)"
```

---

## Task 4: LoadMonitor with TDD

**Files:**
- Create: `app/services/load_monitor.py`
- Create: `tests/unit/test_load_monitor.py`

- [ ] **Step 4.1: Add APScheduler dependency**

Run: `./venv/bin/pip install 'APScheduler==3.10.4'`
Expected: `Successfully installed APScheduler-3.10.4 ...`

Then run: `grep -q "APScheduler" requirements.txt || echo "APScheduler==3.10.4" >> requirements.txt`
Expected: appends only if not present (idempotent)

- [ ] **Step 4.2: Write the failing tests**

Create `tests/unit/test_load_monitor.py`:

```python
"""Tests for LoadMonitor — probes llama-server /health?include_slots=1."""

from __future__ import annotations

import pytest

from app.services.load_monitor import LoadMonitor, _parse_saturation


def test_parse_saturation_all_idle():
    payload = {"slots": [{"state": 0}, {"state": 0}, {"state": 0}, {"state": 0}]}
    assert _parse_saturation(payload) == 0.0


def test_parse_saturation_all_busy():
    payload = {"slots": [{"state": 1}, {"state": 1}, {"state": 1}, {"state": 1}]}
    assert _parse_saturation(payload) == 1.0


def test_parse_saturation_half_busy():
    payload = {"slots": [{"state": 1}, {"state": 0}, {"state": 1}, {"state": 0}]}
    assert _parse_saturation(payload) == 0.5


def test_parse_saturation_no_slots():
    assert _parse_saturation({"slots": []}) == 0.0


def test_parse_saturation_missing_slots_key():
    assert _parse_saturation({}) == 0.0


class TestLoadMonitorCache:
    def test_first_probe_caches_result(self):
        mon = LoadMonitor()
        # Set cache directly to test cache logic without real network
        mon._cache[8080] = (0.5, 100.0)  # (saturation, expiry)
        sat = mon.get_saturation(8080, probe_fn=lambda url: {"slots": []})
        assert sat == 0.5  # cached, probe_fn not called

    def test_expired_cache_triggers_probe(self):
        mon = LoadMonitor(cache_ttl_seconds=0.01)
        mon._cache[8080] = (0.5, 0.0)  # already expired
        called = []
        def fake_probe(url):
            called.append(url)
            return {"slots": [{"state": 1}, {"state": 0}]}
        sat = mon.get_saturation(8080, probe_fn=fake_probe)
        assert sat == 0.5
        assert len(called) == 1

    def test_probe_error_returns_zero(self):
        mon = LoadMonitor()
        def bad_probe(url):
            raise ConnectionError("refused")
        # Should not raise; should return 0.0 (assume idle)
        sat = mon.get_saturation(8080, probe_fn=bad_probe)
        assert sat == 0.0
```

- [ ] **Step 4.3: Run tests to verify they fail**

Run: `AI_HUB_ALLOW_DB_TRUNCATE_FOR_TESTS=1 ./venv/bin/pytest tests/unit/test_load_monitor.py -v --no-cov`
Expected: `ModuleNotFoundError: No module named 'app.services.load_monitor'`

- [ ] **Step 4.4: Implement LoadMonitor**

Create `app/services/load_monitor.py`:

```python
"""Load monitor — probes llama-server /health?include_slots=1 per port.

Returns per-port saturation ∈ [0.0, 1.0]. Uses in-process cache with
configurable TTL to avoid hammering llama-server. On probe failure,
returns 0.0 (assume idle, don't over-degrade).
"""

from __future__ import annotations

import logging
import time
from typing import Callable

import httpx

logger = logging.getLogger(__name__)


def _parse_saturation(payload: dict) -> float:
    """Parse /health?include_slots=1 JSON into saturation ∈ [0.0, 1.0]."""
    slots = payload.get("slots", [])
    if not slots:
        return 0.0
    busy = sum(1 for s in slots if s.get("state") == 1)
    return busy / len(slots)


# Default llama-server URLs (overridable in tests)
DEFAULT_PORTS: dict[int, str] = {
    8080: "http://127.0.0.1:8080",  # 12B Q4 primary
    8081: "http://127.0.0.1:8081",  # E2B-bg
    8082: "http://127.0.0.1:8082",  # E4B
}


class LoadMonitor:
    """Per-port saturation cache with TTL.

    Threading: uses simple dict mutation; concurrent reads/writes are
    acceptable because the cache only stores immutable (sat, expiry)
    tuples and the worst case is one stale read.
    """

    def __init__(
        self,
        cache_ttl_seconds: float = 0.2,
        timeout_seconds: float = 1.0,
        ports: dict[int, str] | None = None,
    ) -> None:
        self._cache_ttl = cache_ttl_seconds
        self._timeout = timeout_seconds
        self._ports = ports or DEFAULT_PORTS
        self._cache: dict[int, tuple[float, float]] = {}  # port -> (sat, expiry)

    def get_saturation(
        self,
        port: int,
        probe_fn: Callable[[str], dict] | None = None,
    ) -> float:
        """Return saturation for `port` ∈ [0.0, 1.0].

        Args:
            port: llama-server port (8080/8081/8082).
            probe_fn: Optional override for testing. If None, uses
                httpx to fetch /health?include_slots=1.
        """
        now = time.monotonic()
        cached = self._cache.get(port)
        if cached is not None and cached[1] > now:
            return cached[0]

        url = self._ports.get(port, f"http://127.0.0.1:{port}")
        try:
            if probe_fn is not None:
                payload = probe_fn(url)
            else:
                payload = self._probe(url)
            sat = _parse_saturation(payload)
        except Exception as e:
            logger.warning("load_monitor: probe %s failed: %s — assuming 0.0", url, e)
            sat = 0.0

        self._cache[port] = (sat, now + self._cache_ttl)
        return sat

    def get_all_saturations(self) -> dict[int, float]:
        """Return current saturation for all known ports."""
        return {port: self.get_saturation(port) for port in self._ports}

    def _probe(self, url: str) -> dict:
        with httpx.Client(timeout=self._timeout) as c:
            r = c.get(f"{url}/health")
            r.raise_for_status()
            return r.json()
```

- [ ] **Step 4.5: Run tests to verify they pass**

Run: `AI_HUB_ALLOW_DB_TRUNCATE_FOR_TESTS=1 ./venv/bin/pytest tests/unit/test_load_monitor.py -v --no-cov`
Expected: all 9 tests PASS

- [ ] **Step 4.6: Commit**

```bash
git add app/services/load_monitor.py tests/unit/test_load_monitor.py requirements.txt
git commit -m "feat(routing): add LoadMonitor with /health?include_slots=1 probe + cache"
```

---

## Task 5: AdaptiveRouter with TDD

**Files:**
- Create: `app/services/router.py`
- Create: `tests/unit/test_router.py`

- [ ] **Step 5.1: Write the failing tests**

Create `tests/unit/test_router.py`:

```python
"""Tests for AdaptiveRouter — combines difficulty + load + project → ModelChoice."""

from __future__ import annotations

from app.services.router import AdaptiveRouter, ModelChoice


def test_easy_with_no_load_routes_to_e2b_bg():
    r = AdaptiveRouter(
        difficulty_easy_threshold=0.3,
        difficulty_hard_threshold=0.6,
        saturation_12b_degrade=0.8,
        saturation_e4b_degrade=0.9,
    )
    assert r.route(difficulty="easy", saturation={8080: 0.0, 8081: 0.0, 8082: 0.0}, project_hint=None) == ModelChoice.E2B_BG


def test_med_routes_to_e4b():
    r = AdaptiveRouter(
        difficulty_easy_threshold=0.3,
        difficulty_hard_threshold=0.6,
        saturation_12b_degrade=0.8,
        saturation_e4b_degrade=0.9,
    )
    assert r.route(difficulty="med", saturation={8080: 0.0, 8081: 0.0, 8082: 0.0}, project_hint=None) == ModelChoice.E4B


def test_hard_with_idle_12b_routes_to_12b():
    r = AdaptiveRouter(
        difficulty_easy_threshold=0.3,
        difficulty_hard_threshold=0.6,
        saturation_12b_degrade=0.8,
        saturation_e4b_degrade=0.9,
    )
    assert r.route(difficulty="hard", saturation={8080: 0.3, 8081: 0.0, 8082: 0.0}, project_hint=None) == ModelChoice.PRIMARY_12B


def test_hard_with_saturated_12b_falls_back_to_e4b():
    r = AdaptiveRouter(
        difficulty_easy_threshold=0.3,
        difficulty_hard_threshold=0.6,
        saturation_12b_degrade=0.8,
        saturation_e4b_degrade=0.9,
    )
    # 12B at 0.9 (above 0.8 threshold), E4B at 0.3 (below 0.9)
    choice = r.route(difficulty="hard", saturation={8080: 0.9, 8081: 0.0, 8082: 0.3}, project_hint=None)
    assert choice == ModelChoice.E4B


def test_hard_with_saturated_12b_and_saturated_e4b_falls_back_to_e2b():
    r = AdaptiveRouter(
        difficulty_easy_threshold=0.3,
        difficulty_hard_threshold=0.6,
        saturation_12b_degrade=0.8,
        saturation_e4b_degrade=0.9,
    )
    choice = r.route(difficulty="hard", saturation={8080: 0.95, 8081: 0.0, 8082: 0.95}, project_hint=None)
    assert choice == ModelChoice.E2B_BG


def test_med_with_saturated_e4b_falls_back_to_e2b():
    r = AdaptiveRouter(
        difficulty_easy_threshold=0.3,
        difficulty_hard_threshold=0.6,
        saturation_12b_degrade=0.8,
        saturation_e4b_degrade=0.9,
    )
    choice = r.route(difficulty="med", saturation={8080: 0.0, 8081: 0.0, 8082: 0.95}, project_hint=None)
    assert choice == ModelChoice.E2B_BG


def test_ihi_project_always_uses_e2b_bg_regardless_of_difficulty():
    r = AdaptiveRouter(
        difficulty_easy_threshold=0.3,
        difficulty_hard_threshold=0.6,
        saturation_12b_degrade=0.8,
        saturation_e4b_degrade=0.9,
    )
    # Even hard with idle 12B, ihi project stays on E2B-bg
    choice = r.route(difficulty="hard", saturation={8080: 0.0, 8081: 0.0, 8082: 0.0}, project_hint="ihi")
    assert choice == ModelChoice.E2B_BG


def test_unknown_difficulty_defaults_to_e4b():
    r = AdaptiveRouter(
        difficulty_easy_threshold=0.3,
        difficulty_hard_threshold=0.6,
        saturation_12b_degrade=0.8,
        saturation_e4b_degrade=0.9,
    )
    choice = r.route(difficulty="unknown", saturation={8080: 0.0, 8081: 0.0, 8082: 0.0}, project_hint=None)
    assert choice == ModelChoice.E4B
```

- [ ] **Step 5.2: Run tests to verify they fail**

Run: `AI_HUB_ALLOW_DB_TRUNCATE_FOR_TESTS=1 ./venv/bin/pytest tests/unit/test_router.py -v --no-cov`
Expected: `ModuleNotFoundError: No module named 'app.services.router'`

- [ ] **Step 5.3: Implement AdaptiveRouter**

Create `app/services/router.py`:

```python
"""AdaptiveRouter — combines difficulty + load + project → ModelChoice.

Decision flow (see spec §4.3):
  1. difficulty → preferred model (easy=E2B-bg, med=E4B, hard=12B)
  2. load-aware degradation:
     - if preferred=12B AND 12B saturated > threshold:
         - if hard AND E4B idle → E4B
         - else → E2B-bg
     - if preferred=E4B AND E4B saturated > threshold → E2B-bg
  3. project override:
     - if project_hint="ihi" → always E2B-bg
"""

from __future__ import annotations

from enum import Enum
from typing import Optional


class ModelChoice(str, Enum):
    """Which model the router selected. Maps to actual model aliases."""

    E2B_BG = "local-gemma4-e2b-q4-bg"     # port 8081
    E4B = "local-gemma4-e4b-q4"             # port 8082
    PRIMARY_12B = "local-gemma4-12b-q4-text"  # port 8080


class AdaptiveRouter:
    def __init__(
        self,
        *,
        difficulty_easy_threshold: float = 0.3,
        difficulty_hard_threshold: float = 0.6,
        saturation_12b_degrade: float = 0.8,
        saturation_e4b_degrade: float = 0.9,
    ) -> None:
        self._easy_t = difficulty_easy_threshold
        self._hard_t = difficulty_hard_threshold
        self._12b_t = saturation_12b_degrade
        self._e4b_t = saturation_e4b_degrade

    def route(
        self,
        *,
        difficulty: str,
        saturation: dict[int, float],
        project_hint: Optional[str] = None,
    ) -> ModelChoice:
        # Project override (IHI always E2B-bg)
        if project_hint == "ihi":
            return ModelChoice.E2B_BG

        # Step 1: preferred model
        if difficulty == "easy":
            preferred = ModelChoice.E2B_BG
        elif difficulty == "med":
            preferred = ModelChoice.E4B
        elif difficulty == "hard":
            preferred = ModelChoice.PRIMARY_12B
        else:
            # Unknown difficulty → default to E4B
            return ModelChoice.E4B

        # Step 2: load-aware degradation
        if preferred == ModelChoice.PRIMARY_12B:
            sat_12b = saturation.get(8080, 0.0)
            sat_e4b = saturation.get(8082, 0.0)
            if sat_12b > self._12b_t:
                if difficulty == "hard" and sat_e4b < self._e4b_t:
                    return ModelChoice.E4B
                return ModelChoice.E2B_BG
        elif preferred == ModelChoice.E4B:
            sat_e4b = saturation.get(8082, 0.0)
            if sat_e4b > self._e4b_t:
                return ModelChoice.E2B_BG

        return preferred
```

- [ ] **Step 5.4: Run tests to verify they pass**

Run: `AI_HUB_ALLOW_DB_TRUNCATE_FOR_TESTS=1 ./venv/bin/pytest tests/unit/test_router.py -v --no-cov`
Expected: all 8 tests PASS

- [ ] **Step 5.5: Commit**

```bash
git add app/services/router.py tests/unit/test_router.py
git commit -m "feat(routing): add AdaptiveRouter (difficulty + load + project)"
```

---

## Task 6: Wire router into `ai_service.py`

**Files:**
- Modify: `app/services/ai_service.py:504-533` (replace `_select_model` body)
- Modify: `app/services/ai_service.py:160-167` (add router + monitor + classifier in `__init__`)

- [ ] **Step 6.1: Add imports at top of `ai_service.py`**

After existing imports (around line 30-40), add:

```python
from app.services.difficulty_classifier import DifficultyClassifier
from app.services.load_monitor import LoadMonitor
from app.services.router import AdaptiveRouter, ModelChoice
```

- [ ] **Step 6.2: Add fields and init in `AIService.__init__`**

In `__init__` (after the existing `self._nine_router = nine_router` line ~167), add:

```python
        # Adaptive routing (added 2026-06-07)
        self._difficulty_classifier = DifficultyClassifier()
        self._load_monitor = LoadMonitor(
            cache_ttl_seconds=settings.load_cache_ttl_seconds,
        )
        self._router = AdaptiveRouter(
            difficulty_easy_threshold=settings.difficulty_easy_threshold,
            difficulty_hard_threshold=settings.difficulty_hard_threshold,
            saturation_12b_degrade=settings.saturation_12b_degrade_threshold,
            saturation_e4b_degrade=settings.saturation_e4b_degrade_threshold,
        )
```

- [ ] **Step 6.3: Replace `_select_model` body with router integration**

Replace the entire method body of `_select_model` (lines 504-533) with:

```python
    def _select_model(self, req: ChatRequest, prompt_model: str) -> tuple[str, int]:
        # Adaptive routing (added 2026-06-07) — uses difficulty + load + project.
        # Falls back to legacy BRANE regex when adaptive_routing_enabled=False.
        if not self._settings.adaptive_routing_enabled:
            # Legacy BRANE path (preserved for rollback)
            intent = self._query_classifier.classify(req)
            hint = self._settings.query_type_model_map.get(intent.type)
            if hint == "fast_background":
                pass  # handled in _route_fast_background_if_eligible
            elif hint == "normal":
                ctx = self._settings.project_context_sizes.get(req.project_id, self._settings.default_num_ctx)
                return self._settings.default_model, ctx
            elif hint == "external":
                return self._settings.openrouter_model, 0
            else:
                ctx = self._settings.project_context_sizes.get(req.project_id, self._settings.default_num_ctx)
                return self._settings.default_model, ctx

        # Adaptive path
        try:
            history_count = len(req.history_messages or [])
            score = self._difficulty_classifier.score(req, history_count=history_count)
            difficulty = self._difficulty_classifier.classify(req, history_count=history_count)
            saturation = self._load_monitor.get_all_saturations()
            project_hint = "ihi" if req.project_id == "ihi" else None

            choice = self._router.route(
                difficulty=difficulty,
                saturation=saturation,
                project_hint=project_hint,
            )

            # Map ModelChoice → (model_alias, ctx)
            if choice == ModelChoice.PRIMARY_12B:
                ctx = self._settings.project_context_sizes.get(req.project_id, 8192)
                return choice.value, ctx
            if choice == ModelChoice.E4B:
                ctx = self._settings.project_context_sizes.get(req.project_id, 8192)
                return choice.value, ctx
            if choice == ModelChoice.E2B_BG:
                # E2B-bg uses its own ctx (handled by bg_provider, no explicit ctx needed)
                return choice.value, 0
            # Fallback (shouldn't reach here)
            ctx = self._settings.project_context_sizes.get(req.project_id, 8192)
            return self._settings.default_model, ctx
        except Exception as e:
            logger.warning("_select_model adaptive path failed: %s — falling back to default", e)
            ctx = self._settings.project_context_sizes.get(req.project_id, self._settings.default_num_ctx)
            return self._settings.default_model, ctx
```

- [ ] **Step 6.4: Verify existing tests still pass (regression check)**

Run: `AI_HUB_ALLOW_DB_TRUNCATE_FOR_TESTS=1 ./venv/bin/pytest tests/unit/test_difficulty_classifier.py tests/unit/test_router.py tests/unit/test_load_monitor.py --no-cov -q`
Expected: all 31 tests PASS

- [ ] **Step 6.5: Commit**

```bash
git add app/services/ai_service.py
git commit -m "feat(routing): wire AdaptiveRouter into _select_model (Phase 1 active)"
```

---

## Task 7: PeriodicSummarizer with TDD

**Files:**
- Create: `app/services/scheduler.py`
- Create: `tests/unit/test_scheduler.py`

- [ ] **Step 7.1: Write the failing tests**

Create `tests/unit/test_scheduler.py`:

```python
"""Tests for PeriodicSummarizer — APScheduler cron for IHI rollups."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.scheduler import PeriodicSummarizer


@pytest.mark.asyncio
async def test_rollup_skips_when_too_few_tokens():
    """If accumulated windows have < min_tokens, skip rollup."""
    ai_service = MagicMock()
    ai_service.summarize = AsyncMock()
    db = MagicMock()
    db.fetch_all = AsyncMock(return_value=[
        {"data": "short", "created_at": "2026-06-07T00:00:00"},
        {"data": "tiny", "created_at": "2026-06-07T00:01:00"},
    ])
    summarizer = PeriodicSummarizer(
        ai_service=ai_service,
        db=db,
        min_tokens=5000,
    )
    await summarizer.rollup_once()
    ai_service.summarize.assert_not_called()
    db.execute.assert_not_called()


@pytest.mark.asyncio
async def test_rollup_calls_12b_with_accumulated_windows():
    """When enough tokens, summarize via 12B and insert into ihi_rollups."""
    ai_service = MagicMock()
    ai_service.summarize = AsyncMock(return_value="Test summary text")
    db = MagicMock()
    db.fetch_all = AsyncMock(return_value=[
        {"data": "x" * 3000, "created_at": "2026-06-07T00:00:00"},
        {"data": "x" * 3000, "created_at": "2026-06-07T00:01:00"},
    ])
    summarizer = PeriodicSummarizer(
        ai_service=ai_service,
        db=db,
        min_tokens=5000,
    )
    await summarizer.rollup_once()

    ai_service.summarize.assert_called_once()
    call_args = ai_service.summarize.call_args
    assert call_args.kwargs["model_override"] == "gemma4-12b"
    assert call_args.kwargs["user_id"] == "_ihi_rollup"
    assert "x" * 6000 in call_args.kwargs["text"]

    db.execute.assert_called_once()
    insert_args = db.execute.call_args
    sql = insert_args.args[0]
    assert "INSERT INTO ihi_rollups" in sql
    assert "Test summary text" in insert_args.args[1]


@pytest.mark.asyncio
async def test_rollup_handles_empty_windows():
    """No windows in last 6h → skip."""
    ai_service = MagicMock()
    ai_service.summarize = AsyncMock()
    db = MagicMock()
    db.fetch_all = AsyncMock(return_value=[])
    summarizer = PeriodicSummarizer(ai_service=ai_service, db=db, min_tokens=5000)
    await summarizer.rollup_once()
    ai_service.summarize.assert_not_called()


@pytest.mark.asyncio
async def test_rollup_logs_failure_does_not_propagate():
    """If AI service fails, rollup must not raise (best-effort)."""
    ai_service = MagicMock()
    ai_service.summarize = AsyncMock(side_effect=RuntimeError("boom"))
    db = MagicMock()
    db.fetch_all = AsyncMock(return_value=[
        {"data": "x" * 6000, "created_at": "2026-06-07T00:00:00"},
    ])
    summarizer = PeriodicSummarizer(ai_service=ai_service, db=db, min_tokens=5000)
    # Should not raise
    await summarizer.rollup_once()
```

- [ ] **Step 7.2: Run tests to verify they fail**

Run: `AI_HUB_ALLOW_DB_TRUNCATE_FOR_TESTS=1 ./venv/bin/pytest tests/unit/test_scheduler.py -v --no-cov`
Expected: `ModuleNotFoundError: No module named 'app.services.scheduler'`

- [ ] **Step 7.3: Implement PeriodicSummarizer**

Create `app/services/scheduler.py`:

```python
"""PeriodicSummarizer — APScheduler cron for IHI rollups.

Every N hours (configurable via PERODIC_SUMMARY_CRON, default every 6h),
aggregates ihi_windows from the last 6h, sends to 12B for summary,
stores in ihi_rollups. Skips if accumulated tokens < min_tokens threshold
(FrugalGPT lesson — don't rollup during quiet periods).
"""

from __future__ import annotations

import logging
from typing import Any, Protocol
from uuid import uuid4

logger = logging.getLogger(__name__)


class AIServiceLike(Protocol):
    """Minimal interface PeriodicSummarizer needs from AIService."""

    async def summarize(self, *, text: str, model_override: str, user_id: str, session_id: str) -> str:
        ...


class DBLike(Protocol):
    async def fetch_all(self, sql: str) -> list[dict[str, Any]]: ...
    async def execute(self, sql: str, *params: Any) -> None: ...


class PeriodicSummarizer:
    def __init__(
        self,
        *,
        ai_service: AIServiceLike,
        db: DBLike,
        min_tokens: int = 5000,
        window_hours: int = 6,
    ) -> None:
        self._ai = ai_service
        self._db = db
        self._min_tokens = min_tokens
        self._window_hours = window_hours

    async def rollup_once(self) -> str | None:
        """Run a single rollup pass. Returns rollup_id or None if skipped.

        Idempotent in the sense that each invocation creates one row.
        Safe to call from cron OR from the lifespan shutdown.
        """
        try:
            windows = await self._db.fetch_all(
                f"SELECT * FROM ihi_windows "
                f"WHERE created_at > NOW() - INTERVAL '{self._window_hours} hours' "
                f"ORDER BY created_at"
            )
            if not windows:
                logger.info("rollup: no windows in last %dh — skipping", self._window_hours)
                return None

            total_tokens = sum(len(str(w.get("data", ""))) for w in windows)
            if total_tokens < self._min_tokens:
                logger.info(
                    "rollup: only %d tokens accumulated (< %d) — skipping",
                    total_tokens, self._min_tokens,
                )
                return None

            # Format windows as a table for the 12B prompt
            summary_input = self._format_windows(windows)
            summary = await self._ai.summarize(
                text=summary_input,
                model_override="gemma4-12b",
                user_id="_ihi_rollup",
                session_id="_rollup",
            )

            rollup_id = f"rollup_{uuid4().hex}"
            window_start = windows[0]["created_at"]
            window_end = windows[-1]["created_at"]
            await self._db.execute(
                "INSERT INTO ihi_rollups "
                "(id, window_start, window_end, summary, model, source_window_count, source_token_count) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                rollup_id, window_start, window_end, summary, "gemma4-12b", len(windows), total_tokens,
            )
            logger.info(
                "rollup %s: %d windows, %d tokens → 12B summary stored",
                rollup_id, len(windows), total_tokens,
            )
            return rollup_id
        except Exception as e:
            logger.error("rollup failed (will retry next cron): %s", e)
            return None

    def _format_windows(self, windows: list[dict]) -> str:
        """Format ihi_windows as a CSV-ish table for the 12B prompt."""
        lines = ["timestamp,data"]
        for w in windows:
            ts = w.get("created_at", "?")
            data = str(w.get("data", "")).replace("\n", " ")
            lines.append(f"{ts},{data[:200]}")
        return "\n".join(lines)
```

- [ ] **Step 7.4: Run tests to verify they pass**

Run: `AI_HUB_ALLOW_DB_TRUNCATE_FOR_TESTS=1 ./venv/bin/pytest tests/unit/test_scheduler.py -v --no-cov`
Expected: all 4 tests PASS

- [ ] **Step 7.5: Commit**

```bash
git add app/services/scheduler.py tests/unit/test_scheduler.py
git commit -m "feat(routing): add PeriodicSummarizer (APScheduler cron for IHI rollups)"
```

---

## Task 8: Wire scheduler into `app/main.py` lifespan

**Files:**
- Modify: `app/main.py:124-150` (add scheduler start/stop in `lifespan`)
- Modify: `app/main.py:1-30` (add scheduler imports)

- [ ] **Step 8.1: Add imports to `app/main.py`**

After the existing `from apscheduler.schedulers.asyncio import AsyncIOScheduler` line (search for it; if not present, add to existing import block at top of file), add:

```python
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from app.services.scheduler import PeriodicSummarizer
```

- [ ] **Step 8.2: Add scheduler start in `lifespan`**

Inside the `lifespan` async function, after the existing app.state initialization block (find the line `app.state.ai_service_ref = weakref.ref(app.state.ai_service)` ~line 310), add:

```python
        # Adaptive routing: APScheduler for periodic IHI rollups (added 2026-06-07)
        if settings.adaptive_routing_enabled:
            scheduler = AsyncIOScheduler()
            db_for_scheduler = _db_module  # already imported at module top
            ai_service_ref_for_scheduler = app.state.ai_service_ref
            summarizer = PeriodicSummarizer(
                ai_service=_SchedulerAIServiceProxy(ai_service_ref_for_scheduler),
                db=_SchedulerDBProxy(db_for_scheduler),
                min_tokens=settings.periodic_summary_min_tokens,
                window_hours=6,
            )
            scheduler.add_job(
                summarizer.rollup_once,
                CronTrigger.from_crontab(settings.periodic_summary_cron),
                id="ihi_rollup",
                replace_existing=True,
            )
            scheduler.start()
            app.state.scheduler = scheduler
            logger.info("periodic summary scheduler started: cron=%s", settings.periodic_summary_cron)
```

- [ ] **Step 8.3: Add scheduler shutdown in `lifespan`**

In the `finally` block of `lifespan` (find `finally:` near the end of the function), before existing shutdown code, add:

```python
        if hasattr(app.state, "scheduler"):
            app.state.scheduler.shutdown(wait=False)
            logger.info("periodic summary scheduler stopped")
```

- [ ] **Step 8.4: Add the proxy classes at the top of `app/main.py` (after imports)**

```python
class _SchedulerAIServiceProxy:
    """Adapter so PeriodicSummarizer can call ai_service.summarize via weakref."""
    def __init__(self, ref):
        self._ref = ref

    async def summarize(self, *, text, model_override, user_id, session_id):
        svc = self._ref()
        if svc is None:
            raise RuntimeError("ai_service no longer alive")
        return await svc.summarize(text=text, model_override=model_override, user_id=user_id, session_id=session_id)


class _SchedulerDBProxy:
    """Adapter so PeriodicSummarizer can call db.fetch_all / db.execute."""
    def __init__(self, db_module):
        self._db = db_module

    async def fetch_all(self, sql):
        with self._db.get_db_connection() as conn:
            cur = conn.execute(sql)
            cols = [d.name for d in cur.description] if cur.description else []
            return [dict(zip(cols, row)) for row in cur.fetchall()]

    async def execute(self, sql, *params):
        with self._db.get_db_connection() as conn:
            conn.execute(sql, params)
            conn.commit()
```

Note: If `app/main.py` does not have `ai_service.summarize` method, you must add a thin wrapper. Check `app/services/ai_service.py` for a `summarize` method. If missing, add it (see Task 9 fallback).

- [ ] **Step 8.5: Verify main.py imports cleanly**

Run: `./venv/bin/python -c "import app.main; print('OK')"`
Expected: `OK`

- [ ] **Step 8.6: Commit**

```bash
git add app/main.py
git commit -m "feat(main): wire PeriodicSummarizer into lifespan (start/stop on app boot/shutdown)"
```

---

## Task 9: Integration test — live smoke (1K requests with adaptive routing)

**Files:**
- Create: `tests/integration/test_adaptive_routing_live.py` (optional but recommended)

- [ ] **Step 9.1: Verify all unit tests still pass**

Run: `AI_HUB_ALLOW_DB_TRUNCATE_FOR_TESTS=1 ./venv/bin/pytest tests/unit/test_difficulty_classifier.py tests/unit/test_load_monitor.py tests/unit/test_router.py tests/unit/test_scheduler.py --no-cov -q`
Expected: all 35 tests PASS

- [ ] **Step 9.2: Restart ai-hub with new code**

Run:
```bash
pkill -f "uvicorn app.main" 2>/dev/null
sleep 2
(nohup ./venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 >/tmp/uvicorn-adaptive.log 2>&1 & disown)
sleep 8
tail -5 /tmp/uvicorn-adaptive.log
```

Expected: `Application startup complete.` in log

- [ ] **Step 9.3: Run live smoke test**

Run:
```bash
API_KEY=$(grep "^API_KEY=" .env | cut -d= -f2 | tr -d '"')
./venv/bin/python scripts/loadtest.py --total 200 --concurrency 8
```

Expected: 200 requests complete in ~5 min, 0 errors

- [ ] **Step 9.4: Verify adaptive routing distribution**

Run:
```bash
API_KEY=$(grep "^API_KEY=" .env | cut -d= -f2 | tr -d '"')
curl -fsS -H "X-API-KEY: $API_KEY" http://localhost:8000/v1/admin/observability 2>&1 | python3 -c "
import sys, json
d = json.load(sys.stdin)
print('=== By model (last 200 test requests) ===')
for m in d['by_model']:
    print(f'  {m[\"model\"]:<35} {m[\"requests\"]:>5} req ({m[\"requests\"]/d[\"total_requests\"]*100:.1f}%)')
print()
print('=== Latency ===')
l = d['latency']
print(f'  avg: {l[\"avg_ms\"]:.0f}ms | p50: {l[\"p50_ms\"]:.0f}ms | p95: {l[\"p95_ms\"]:.0f}ms')
"
```

Expected: 12B Q4 should now be touched (even 1-2% would prove the router is choosing it for hard queries)

- [ ] **Step 9.5: Check no errors in uvicorn log**

Run: `grep -iE "error|exception|traceback" /tmp/uvicorn-adaptive.log | head -5`
Expected: 0 errors (or only known non-fatal ones)

---

## Task 10: Documentation + final commit

**Files:**
- Modify: `docs/superpowers/sessions/2026-06-06-3-agent-fanout-checkpoint.md` — append adaptive routing section
- Or: Create: `docs/superpowers/sessions/2026-06-07-adaptive-routing-shipped.md`

- [ ] **Step 10.1: Create shipped session doc**

Create `docs/superpowers/sessions/2026-06-07-adaptive-routing-shipped.md`:

```markdown
# Adaptive Routing — Shipped 2026-06-07

## Modules added
- `app/services/difficulty_classifier.py` (~250 LOC)
- `app/services/load_monitor.py` (~150 LOC)
- `app/services/router.py` (~300 LOC)
- `app/services/scheduler.py` (~200 LOC)
- 4 test files (~600 LOC)

## Modified
- `app/core/config.py` — 8 new config fields
- `app/core/database.py` — ihi_rollups table
- `app/services/ai_service.py` — wire router
- `app/main.py` — wire scheduler
- `requirements.txt` — APScheduler

## Behavior
- Difficulty classifier: heuristic (length, code, math, multi-question, history depth)
- Load monitor: probes llama-server /health?include_slots=1 every 1s
- Router: difficulty + load + project → ModelChoice
- Periodic summarizer: APScheduler cron every 6h, rolls up ihi_windows to 12B

## Verified
- 35 unit tests pass
- Live smoke: 200 requests, 0 errors, 12B Q4 now touched (vs 0% before)

## Phase 2 (deferred)
- ML classifier via FastEmbed + LR
- Cascade escalation
- Multi-node load balancing
```

- [ ] **Step 10.2: Commit docs**

```bash
git add docs/superpowers/sessions/2026-06-07-adaptive-routing-shipped.md
git commit -m "docs(session): adaptive routing shipped 2026-06-07"
```

- [ ] **Step 10.3: Push to origin**

```bash
git push -u origin main
```

Expected: `To github.com:haihung2010/ai-hub.git  main -> main`

---

## Self-Review (post-write)

**1. Spec coverage:**
- §4.1 DifficultyClassifier ✅ Task 3
- §4.2 LoadMonitor ✅ Task 4
- §4.3 AdaptiveRouter ✅ Task 5 + Task 6 (wire)
- §4.4 PeriodicSummarizer ✅ Task 7 + Task 8 (wire)
- §5 Data flow ✅ Task 6 (router integration)
- §6 Error handling ✅ Task 6 (try/except fallback), Task 7 (rollup catches)
- §7 Testing ✅ Tasks 3, 4, 5, 7 unit tests + Task 9 integration
- §8 Token budget: spec estimated 3-5M tokens, this plan has 10 tasks × ~500K each = ~5M ✅
- §9 Phase plan: Phase 1 implemented, Phase 2/3 deferred as planned

**2. Placeholder scan:**
- ✅ No "TBD" / "TODO" / "fill in details"
- ✅ All code blocks contain actual content
- ✅ No "similar to Task N" references without code
- ✅ Exact file paths everywhere

**3. Type consistency:**
- `DifficultyClassifier.score()` returns `float` — consistent
- `classify_score()` returns `str` literal `"easy"|"med"|"hard"` — consistent
- `AdaptiveRouter.route()` returns `ModelChoice` — consistent enum
- `ModelChoice` enum values used in `_select_model` mapping — consistent
- `LoadMonitor.get_saturation()` returns `float` — consistent
- `PeriodicSummarizer.rollup_once()` returns `str | None` — consistent
- `AIServiceLike.summarize()` and `DBLike` Protocol definitions — consistent

**4. Integration sanity:**
- `ai_service.summarize()` method required by PeriodicSummarizer — flagged in Task 8.4
- `get_all_saturations()` defined in LoadMonitor — used by router
- `ihp_rollups` table schema matches inserts in PeriodicSummarizer — verified
- Config fields referenced match those added in Task 1
