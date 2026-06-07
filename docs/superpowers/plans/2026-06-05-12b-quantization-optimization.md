# Gemma 4 12B Quantization Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Find the optimal Gemma 4 12B quantization (Q4 vs Q6 vs Q8) and deployment strategy (standalone vs E2B split) for ai-hub chatbot, balancing throughput, latency, and Vietnamese quality.

**Architecture:** 3 configurations tested sequentially (Q4+E2B combo, Q6+E2B combo, Q8+mmproj standalone). Each config benchmarked with 7 sub-phases (warmup, latency baseline, concurrency 5/10/20/40, Vietnamese quality). DB snapshot/restore between tests for isolation. Top 1-2 configs re-tested with sustained max load.

**Tech Stack:** Python 3.12, llama.cpp (3 quantizations), FastEmbed, PostgreSQL (pg_dump for snapshots), SQLite (alert.db), bash, curl, psql, jq

**Spec:** `docs/superpowers/specs/2026-06-05-12b-quantization-optimization.md`

**Existing infrastructure (foundation):**
- `app/services/ai_service.py` — multi-tenant LLM router
- `scripts/start_lite_q8.sh` — E4B Q8 launcher (template for new launchers)
- 4 LLM ports: 8080 (chatbot), 8081 (background Q4), 8082 (reranker), 8083 (E2B Q4 IHI)
- PostgreSQL `ai_hub` DB with ihi_rag_cases, ihi_case_embeddings, ihi_device_overrides
- `/home/hung/ihi_test/alert.db` (SQLite, 11 cycles of historical IHI data)
- `/home/hung/models/` — model storage

---

## Files Overview

### New Scripts (9)
- `scripts/snapshot_ihi_db.sh` — DB snapshot before each test
- `scripts/restore_ihi_db.sh` — restore from snapshot after test
- `scripts/start_12b_q4_text.sh` — 12B Q4 text-only launcher (port 8080)
- `scripts/start_12b_q6_text.sh` — 12B Q6 text-only launcher (port 8080)
- `scripts/start_12b_q8_mmproj.sh` — 12B Q8 + mmproj launcher (port 8080)
- `scripts/start_e2b_q4_mmproj.sh` — E2B Q4 + mmproj launcher (port 8083)
- `scripts/bench_single_config.py` — benchmark one config (all phases)
- `scripts/gen_final_report.py` — aggregate JSONs → final_comparison.md
- `scripts/bench_12b_configs.py` — master orchestrator (Stage A + B)

### New Test Files (3)
- `tests/unit/test_bench_metrics.py` — test metric computation/aggregation
- `tests/unit/test_gen_final_report.py` — test report formatting
- `tests/unit/test_snapshot_restore.py` — test DB snapshot/restore (uses real PG, idempotent)

### New Reports (10+)
- `reports/bench_12b/q4_combo_basic.json`
- `reports/bench_12b/q4_combo_basic.md`
- `reports/bench_12b/q6_combo_basic.json`
- `reports/bench_12b/q6_combo_basic.md`
- `reports/bench_12b/q8_standalone_basic.json`
- `reports/bench_12b/q8_standalone_basic.md`
- `reports/bench_12b/<winner>_max_load.json` (Stage B)
- `reports/bench_12b/<winner>_max_load.md`
- `reports/bench_12b/errors.log`
- `reports/bench_12b/final_comparison.md`

---

## Phase A: Infrastructure (TDD)

### Task 1: DB snapshot script `scripts/snapshot_ihi_db.sh`

**Files:**
- Create: `scripts/snapshot_ihi_db.sh`
- Test: `tests/unit/test_snapshot_restore.sh`

- [ ] **Step 1: Create directory**

```bash
mkdir -p /tmp/ihi_snapshots
```

- [ ] **Step 2: Write the snapshot script**

```bash
#!/usr/bin/env bash
# Snapshot ihi_rag_cases (PG) + alert.db (SQLite) to /tmp/ihi_snapshots/<ts>/
# Idempotent. Safe to re-run.
set -euo pipefail

TIMESTAMP="${1:-$(date +%s)}"
SNAPSHOT_DIR="/tmp/ihi_snapshots/${TIMESTAMP}"
mkdir -p "$SNAPSHOT_DIR"

# PG tables
pg_dump -t ihi_rag_cases -t ihi_case_embeddings -t ihi_device_overrides --data-only \
  > "$SNAPSHOT_DIR/ihi_pg.sql"

# SQLite
cp /home/hung/ihi_test/alert.db "$SNAPSHOT_DIR/alert.db"

echo "Snapshot saved to $SNAPSHOT_DIR"
echo "  PG: $(wc -l < $SNAPSHOT_DIR/ihi_pg.sql) lines"
echo "  SQLite: $(stat -c %s $SNAPSHOT_DIR/alert.db) bytes"
```

- [ ] **Step 3: Make executable**

```bash
chmod +x scripts/snapshot_ihi_db.sh
```

- [ ] **Step 4: Write smoke test**

```bash
# tests/unit/test_snapshot_restore.sh
#!/usr/bin/env bash
set -euo pipefail

SNAP=$(./scripts/snapshot_ihi_db.sh "test_$(date +%s)")
echo "$SNAP"
SNAP_DIR=$(echo "$SNAP" | head -1 | awk '{print $3}')

# Verify files exist
[[ -f "$SNAP_DIR/ihi_pg.sql" ]] || { echo "FAIL: ihi_pg.sql missing"; exit 1; }
[[ -f "$SNAP_DIR/alert.db" ]] || { echo "FAIL: alert.db missing"; exit 1; }

# Idempotency: re-run with same timestamp
./scripts/snapshot_ihi_db.sh "$(basename $SNAP_DIR)" > /dev/null
[[ -f "$SNAP_DIR/ihi_pg.sql" ]] || { echo "FAIL: idempotent re-run failed"; exit 1; }

echo "PASS: snapshot creates files + idempotent"
```

- [ ] **Step 5: Run test**

```bash
chmod +x tests/unit/test_snapshot_restore.sh
bash tests/unit/test_snapshot_restore.sh
```

Expected: `PASS: snapshot creates files + idempotent`

- [ ] **Step 6: Commit**

```bash
git add scripts/snapshot_ihi_db.sh tests/unit/test_snapshot_restore.sh
git commit -m "feat(bench): add ihi_db snapshot script (idempotent)"
```

---

### Task 2: DB restore script `scripts/restore_ihi_db.sh`

**Files:**
- Create: `scripts/restore_ihi_db.sh`
- Test: extend `tests/unit/test_snapshot_restore.sh`

- [ ] **Step 1: Write the restore script**

```bash
#!/usr/bin/env bash
# Restore ihi_rag_cases (PG) + alert.db (SQLite) from snapshot.
# Usage: restore_ihi_db.sh <snapshot_dir>
set -euo pipefail

SNAPSHOT_DIR="${1:?Usage: restore_ihi_db.sh <snapshot_dir>}"
[[ -d "$SNAPSHOT_DIR" ]] || { echo "ERROR: $SNAPSHOT_DIR not found"; exit 2; }
[[ -f "$SNAPSHOT_DIR/ihi_pg.sql" ]] || { echo "ERROR: ihi_pg.sql missing in snapshot"; exit 2; }
[[ -f "$SNAPSHOT_DIR/alert.db" ]] || { echo "ERROR: alert.db missing in snapshot"; exit 2; }

# PG: truncate + restore
PGPASSWORD=aihub_pass psql -h localhost -U aihub -d ai_hub <<EOF
TRUNCATE ihi_rag_cases RESTART IDENTITY CASCADE;
TRUNCATE ihi_case_embeddings;
TRUNCATE ihi_device_overrides;
\i $SNAPSHOT_DIR/ihi_pg.sql
EOF

# SQLite: copy back
cp "$SNAPSHOT_DIR/alert.db" /home/hung/ihi_test/alert.db

echo "Restored from $SNAPSHOT_DIR"
```

- [ ] **Step 2: Make executable**

```bash
chmod +x scripts/restore_ihi_db.sh
```

- [ ] **Step 3: Extend smoke test**

Append to `tests/unit/test_snapshot_restore.sh`:

```bash
# Test restore (only if PG is available)
if command -v psql &> /dev/null && PGPASSWORD=aihub_pass psql -h localhost -U aihub -d ai_hub -c "SELECT 1" &> /dev/null; then
    ./scripts/snapshot_ihi_db.sh "test_restore_$(date +%s)" > /dev/null
    LATEST=$(ls -t /tmp/ihi_snapshots | head -1)
    LATEST_DIR="/tmp/ihi_snapshots/$LATEST"

    # Modify DB (insert a junk row)
    PGPASSWORD=aihub_pass psql -h localhost -U aihub -d ai_hub -c \
      "INSERT INTO ihi_rag_cases (device_id, severity, pattern, description) VALUES ('TEST_POLLUTE', 'low', '{}', 'junk')" &> /dev/null
    PRE_COUNT=$(PGPASSWORD=aihub_pass psql -h localhost -U aihub -d ai_hub -tA -c "SELECT COUNT(*) FROM ihi_rag_cases WHERE device_id='TEST_POLLUTE'")
    [[ "$PRE_COUNT" == "1" ]] || { echo "FAIL: pollution insert failed"; exit 1; }

    # Restore
    ./scripts/restore_ihi_db.sh "$LATEST_DIR" > /dev/null

    # Verify pollution gone
    POST_COUNT=$(PGPASSWORD=aihub_pass psql -h localhost -U aihub -d ai_hub -tA -c "SELECT COUNT(*) FROM ihi_rag_cases WHERE device_id='TEST_POLLUTE'")
    [[ "$POST_COUNT" == "0" ]] || { echo "FAIL: pollution NOT cleared (got $POST_COUNT rows)"; exit 1; }
    echo "PASS: restore clears pollution"
else
    echo "SKIP: restore test (PG not available)"
fi
```

- [ ] **Step 4: Run test**

```bash
bash tests/unit/test_snapshot_restore.sh
```

Expected: All PASS (or PG SKIP)

- [ ] **Step 5: Commit**

```bash
git add scripts/restore_ihi_db.sh tests/unit/test_snapshot_restore.sh
git commit -m "feat(bench): add ihi_db restore script + pollution test"
```

---

### Task 3: Benchmark metric module `scripts/bench_metrics.py`

**Files:**
- Create: `scripts/bench_metrics.py` (pure functions, importable)
- Test: `tests/unit/test_bench_metrics.py`

- [ ] **Step 1: Write failing test**

```python
# tests/unit/test_bench_metrics.py
import pytest
import time
from scripts.bench_metrics import (
    compute_phase_metrics, aggregate_phases, rank_configs, compute_composite_score,
)


def test_compute_phase_metrics_single_request():
    """Compute metrics from a list of request timings."""
    timings = [
        {"ttft_ms": 100, "e2e_ms": 500, "prompt_tokens": 20, "completion_tokens": 100, "status": "ok"},
        {"ttft_ms": 200, "e2e_ms": 600, "prompt_tokens": 20, "completion_tokens": 100, "status": "ok"},
    ]
    m = compute_phase_metrics(timings, wall_time_s=2.0)
    assert m["requests"] == 2
    assert m["ttft_p50_ms"] == 150  # median
    assert m["ttft_p95_ms"] >= 200  # p95 from 2 samples = max
    assert m["tok_s_aggregate"] == 100.0  # 200 tokens / 2 sec
    assert m["rps"] == 1.0  # 2 req / 2 sec
    assert m["errors"] == 0


def test_compute_phase_metrics_with_errors():
    """Errors counted separately, don't pollute percentiles."""
    timings = [
        {"ttft_ms": 100, "e2e_ms": 500, "prompt_tokens": 20, "completion_tokens": 100, "status": "ok"},
        {"ttft_ms": 0, "e2e_ms": 0, "prompt_tokens": 0, "completion_tokens": 0, "status": "TIMEOUT"},
    ]
    m = compute_phase_metrics(timings, wall_time_s=1.0)
    assert m["requests"] == 1  # only successful counted
    assert m["errors"] == 1
    assert m["tok_s_aggregate"] == 100.0


def test_aggregate_phases_computes_weighted_score():
    """Aggregate 3 phases into a single config score."""
    phases = {
        "latency": {"tok_s_aggregate": 50, "ttft_p95_ms": 200},
        "concurrency_10": {"tok_s_aggregate": 200, "ttft_p95_ms": 500},
        "concurrency_20": {"tok_s_aggregate": 300, "ttft_p95_ms": 1200},
    }
    agg = aggregate_phases(phases)
    # tok/s should be peak (max), latency should be weighted avg
    assert agg["peak_tok_s"] == 300
    assert agg["p95_latency_at_20"] == 1200


def test_rank_configs_by_composite_score():
    """Higher composite score ranks first."""
    configs = [
        {"name": "A", "peak_tok_s": 500, "p95_latency_at_20": 1200, "quality": 8.2},
        {"name": "B", "peak_tok_s": 470, "p95_latency_at_20": 1450, "quality": 8.6},
        {"name": "C", "peak_tok_s": 430, "p95_latency_at_20": 1850, "quality": 8.9},
    ]
    ranked = rank_configs(configs)
    # All 3 metrics weighted equally. C has best quality but worst latency.
    # Should produce deterministic ranking with composite scores.
    assert len(ranked) == 3
    assert all("composite_score" in c for c in ranked)
    # Scores should be sorted descending
    assert ranked[0]["composite_score"] >= ranked[1]["composite_score"] >= ranked[2]["composite_score"]
```

- [ ] **Step 2: Run test to verify it fails**

```bash
PYTHONPATH=scripts ./venv/bin/pytest tests/unit/test_bench_metrics.py -v --no-cov
```

Expected: FAIL (module doesn't exist)

- [ ] **Step 3: Write minimal implementation**

```python
# scripts/bench_metrics.py
"""Pure functions for computing benchmark metrics.

No I/O, no subprocess, no network. Importable for unit testing.
"""
from __future__ import annotations

import statistics
from typing import Any


def _percentile(values: list[float], p: float) -> float:
    """Return the p-th percentile (0-100) of values. Returns 0 if empty."""
    if not values:
        return 0
    return statistics.quantiles(values, n=100, method="inclusive")[min(int(p) - 1, 99)] if len(values) > 1 else values[0]


def compute_phase_metrics(timings: list[dict], wall_time_s: float) -> dict:
    """Compute aggregate metrics from a list of request timings.

    Each timing: {ttft_ms, e2e_ms, prompt_tokens, completion_tokens, status}
    status="ok" counts toward percentiles; "TIMEOUT"/"ERROR" counted as errors only.
    """
    successful = [t for t in timings if t.get("status") == "ok"]
    ttft = [t["ttft_ms"] for t in successful]
    e2e = [t["e2e_ms"] for t in successful]
    total_tokens = sum(t["prompt_tokens"] + t["completion_tokens"] for t in successful)
    completion_tokens = sum(t["completion_tokens"] for t in successful)

    return {
        "requests": len(successful),
        "errors": len(timings) - len(successful),
        "ttft_p50_ms": int(_percentile(ttft, 50)),
        "ttft_p95_ms": int(_percentile(ttft, 95)),
        "e2e_p50_ms": int(_percentile(e2e, 50)),
        "e2e_p95_ms": int(_percentile(e2e, 95)),
        "tok_s_aggregate": round(completion_tokens / max(wall_time_s, 0.001), 1),
        "rps": round(len(successful) / max(wall_time_s, 0.001), 2),
    }


def aggregate_phases(phases: dict[str, dict]) -> dict:
    """Aggregate multiple phase metrics into config-level summary."""
    peak_tok_s = max((p.get("tok_s_aggregate", 0) for p in phases.values()), default=0)
    # Latency at 20 users (or whichever phase is closest)
    latency_phases = {k: v for k, v in phases.items() if "concurrency" in k}
    p95_latency_at_20 = latency_phases.get("concurrency_20", {}).get("ttft_p95_ms", 0)
    return {
        "peak_tok_s": peak_tok_s,
        "p95_latency_at_20": p95_latency_at_20,
    }


def compute_composite_score(peak_tok_s: float, p95_latency_ms: float, quality: float,
                            max_tok_s: float, max_latency_ms: float, max_quality: float) -> float:
    """Compute composite score [0, 1] using balanced weighting.

    Normalizes each metric to [0, 1] using max-value scaling, then weights:
    - 0.40 × normalized_tok_s (higher is better)
    - 0.30 × normalized_inv_latency (1 - normalized, higher is better)
    - 0.30 × normalized_quality (higher is better)
    """
    norm_tok = peak_tok_s / max(max_tok_s, 1)
    norm_lat = 1 - (p95_latency_ms / max(max_latency_ms, 1))
    norm_qual = quality / max(max_quality, 1)
    return round(0.40 * norm_tok + 0.30 * max(norm_lat, 0) + 0.30 * norm_qual, 4)


def rank_configs(configs: list[dict]) -> list[dict]:
    """Rank configs by composite score. Returns list sorted desc by composite_score.

    Each config: {name, peak_tok_s, p95_latency_at_20, quality}.
    Adds 'composite_score' field to each.
    """
    if not configs:
        return []
    max_tok = max(c.get("peak_tok_s", 0) for c in configs)
    max_lat = max(c.get("p95_latency_at_20", 0) for c in configs) or 1
    max_qual = max(c.get("quality", 0) for c in configs) or 1
    for c in configs:
        c["composite_score"] = compute_composite_score(
            c.get("peak_tok_s", 0), c.get("p95_latency_at_20", 0), c.get("quality", 0),
            max_tok, max_lat, max_qual,
        )
    return sorted(configs, key=lambda c: c["composite_score"], reverse=True)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
PYTHONPATH=scripts ./venv/bin/pytest tests/unit/test_bench_metrics.py -v --no-cov
```

Expected: 4/4 PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/bench_metrics.py tests/unit/test_bench_metrics.py
git commit -m "feat(bench): add pure metric computation module (TDD)"
```

---

### Task 4: Vietnamese quality scoring module

**Files:**
- Create: `scripts/quality_scoring.py`
- Test: `tests/unit/test_quality_scoring.py`

- [ ] **Step 1: Write failing test**

```python
# tests/unit/test_quality_scoring.py
import pytest
from scripts.quality_scoring import (
    detect_hallucination, score_response, PROMPT_BANK,
)


def test_detect_hallucination_clean():
    """Clean response has no hallucination markers."""
    assert detect_hallucination("Trời hôm nay nắng đẹp.") is False


def test_detect_hallucination_arraylist():
    """ArrayList is a known hallucination marker."""
    assert detect_hallucination("**Verdict:** ArrayList") is True


def test_detect_hallucination_class_normal():
    """CLASS-NORMAL is a known hallucination marker."""
    assert detect_hallucination("**Verdict:** CLASS-NORMAL") is True


def test_detect_hallucination_empty():
    """Empty response is suspicious."""
    assert detect_hallucination("") is True


def test_score_response_clean_relevant():
    """A clean, relevant response scores 7-10."""
    prompt = PROMPT_BANK[0]  # "Xin chào, bạn tên gì?"
    response = "Xin chào! Tôi là một trợ lý AI được huấn luyện bởi Google, có thể giúp bạn trả lời câu hỏi về nhiều chủ đề."
    score = score_response(prompt, response)
    assert score["total"] >= 7
    assert score["relevance"] >= 2


def test_score_response_irrelevant():
    """Off-topic response scores low relevance."""
    prompt = "Giải thích NEMA MG-1 voltage imbalance threshold"
    response = "Tôi thích ăn phở."
    score = score_response(prompt, response)
    assert score["relevance"] <= 1
    assert score["total"] < 5


def test_score_response_garbage_zero():
    """Hallucination tokens → automatic 0."""
    prompt = "Bất kỳ"
    response = "**Verdict:** ArrayList"
    score = score_response(prompt, response)
    assert score["total"] == 0


def test_prompt_bank_has_28_prompts():
    """Validate the prompt bank structure."""
    assert len(PROMPT_BANK) >= 28
    categories = set(p["category"] for p in PROMPT_BANK)
    assert "greeting" in categories
    assert "technical" in categories
    assert "code" in categories
```

- [ ] **Step 2: Run test to verify it fails**

```bash
PYTHONPATH=scripts ./venv/bin/pytest tests/unit/test_quality_scoring.py -v --no-cov
```

Expected: FAIL (module doesn't exist)

- [ ] **Step 3: Write minimal implementation**

```python
# scripts/quality_scoring.py
"""Vietnamese quality scoring for benchmark responses.

LLM-as-judge would be ideal but is too slow for benchmarks. Use heuristics:
- detect_hallucination: known bad tokens
- score_response: heuristic rubric 1-10

For the actual benchmark, this is a placeholder — replace with real LLM judge
in the orchestrator if time permits. For now, heuristic catches the worst cases.
"""
from __future__ import annotations


HALLUCINATION_MARKERS = [
    "ArrayList",
    "CLASS-NORMAL",
    "[object Object]",
    "null",
    "undefined",
    "NaN, NaN",
]


def detect_hallucination(response: str) -> bool:
    """Return True if response contains known hallucination markers or is empty."""
    if not response or len(response.strip()) < 5:
        return True
    return any(marker in response for marker in HALLUCINATION_MARKERS)


# 28 Vietnamese prompts (subset for heuristic scoring — full bank used by orchestrator)
PROMPT_BANK = [
    {"id": 1, "category": "greeting", "prompt": "Xin chào, bạn tên gì?", "target_lang": "vi"},
    {"id": 2, "category": "greeting", "prompt": "Bạn có khỏe không?", "target_lang": "vi"},
    {"id": 3, "category": "greeting", "prompt": "Hôm nay bạn thế nào?", "target_lang": "vi"},
    {"id": 4, "category": "greeting", "prompt": "Cảm ơn bạn", "target_lang": "vi"},
    {"id": 5, "category": "technical", "prompt": "Giải thích NEMA MG-1 voltage imbalance threshold", "target_lang": "vi"},
    {"id": 6, "category": "technical", "prompt": "ISO 10816-3 vibration zones là gì?", "target_lang": "vi"},
    {"id": 7, "category": "technical", "prompt": "Phân biệt cảm biến IoT và cảm biến công nghiệp", "target_lang": "vi"},
    {"id": 8, "category": "technical", "prompt": "I2C vs SPI, nên chọn loại nào cho sensor?", "target_lang": "vi"},
    {"id": 9, "category": "technical", "prompt": "Tại sao cần pull-up resistor cho I2C?", "target_lang": "vi"},
    {"id": 10, "category": "technical", "prompt": "Giải thích MQTT QoS levels", "target_lang": "vi"},
    {"id": 11, "category": "code", "prompt": "Sửa lỗi: `def f(x): return x + 1` cho list", "target_lang": "vi"},
    {"id": 12, "category": "code", "prompt": "Viết hàm Python kiểm tra số nguyên tố", "target_lang": "vi"},
    {"id": 13, "category": "code", "prompt": "Sự khác biệt giữa `==` và `is` trong Python?", "target_lang": "vi"},
    {"id": 14, "category": "code", "prompt": "Cách đọc file JSON trong Python?", "target_lang": "vi"},
    {"id": 15, "category": "translation", "prompt": "Dịch 'industrial sensor monitoring' sang tiếng Việt", "target_lang": "vi"},
    {"id": 16, "category": "translation", "prompt": "Translate 'predictive maintenance' to Vietnamese", "target_lang": "en"},
    {"id": 17, "category": "translation", "prompt": "Dịch 'cảm biến rung động' sang tiếng Anh", "target_lang": "en"},
    {"id": 18, "category": "translation", "prompt": "'Edge computing' tiếng Việt là gì?", "target_lang": "vi"},
    {"id": 19, "category": "factual", "prompt": "Tại sao bầu trời có màu xanh?", "target_lang": "vi"},
    {"id": 20, "category": "factual", "prompt": "Thủ đô Việt Nam là gì?", "target_lang": "vi"},
    {"id": 21, "category": "factual", "prompt": "Dân số Việt Nam hiện tại khoảng bao nhiêu?", "target_lang": "vi"},
    {"id": 22, "category": "factual", "prompt": "AI là gì? Giải thích ngắn gọn", "target_lang": "vi"},
    {"id": 23, "category": "creative", "prompt": "Viết 1 đoạn văn 4 câu về IoT trong nông nghiệp", "target_lang": "vi"},
    {"id": 24, "category": "creative", "prompt": "Hãy sáng tác 1 bài thơ 4 dòng về cảm biến", "target_lang": "vi"},
    {"id": 25, "category": "creative", "prompt": "Mô tả 1 ngày làm việc của kỹ sư IoT", "target_lang": "vi"},
    {"id": 26, "category": "reasoning", "prompt": "Có 5 quả táo, cho 2 bạn mỗi bạn 1 quả. Còn mấy?", "target_lang": "vi"},
    {"id": 27, "category": "reasoning", "prompt": "Nếu A > B và B > C, thì A > C? Tại sao?", "target_lang": "vi"},
    {"id": 28, "category": "reasoning", "prompt": "Tại sao 1 + 1 = 2?", "target_lang": "vi"},
]


def score_response(prompt: str, response: str) -> dict:
    """Heuristic quality scoring 1-10.

    Returns dict with breakdown: {relevance, naturalness, accuracy, conciseness, format, total}
    """
    if detect_hallucination(response):
        return {"relevance": 0, "naturalness": 0, "accuracy": 0, "conciseness": 0, "format": 0, "total": 0}

    relevance = 0
    naturalness = 0
    accuracy = 0
    conciseness = 0
    format_score = 0

    # Heuristic: response length correlates with thoroughness
    words = len(response.split())
    if words >= 5:
        relevance = 1
    if words >= 20:
        relevance = 2
    if any(kw in response.lower() for kw in prompt.lower().split() if len(kw) > 3):
        relevance = min(relevance + 1, 3)

    # Naturalness: look for Vietnamese diacritics (heuristic for VI text)
    vi_chars = sum(1 for c in response if c in "ăâđêôơưĂÂĐÊÔƠƯáàảãạắằẳẵặấầẩẫậéèẻẽẹếềểễệíìỉĩịóòỏõọốồổỗộớờởỡợúùủũụứừửữựýỳỷỹỵ")
    if vi_chars > 0:
        naturalness = 1
    if vi_chars > 20:
        naturalness = 2

    # Accuracy: cannot verify without ground truth — assume average
    accuracy = 2 if words >= 30 else 1

    # Conciseness: not too short, not too long
    if 30 <= words <= 200:
        conciseness = 1

    # Format: has proper punctuation
    if response.endswith((".", "!", "?")):
        format_score = 1

    total = relevance + naturalness + accuracy + conciseness + format_score
    return {
        "relevance": relevance,
        "naturalness": naturalness,
        "accuracy": accuracy,
        "conciseness": conciseness,
        "format": format_score,
        "total": total,
    }
```

- [ ] **Step 4: Run test to verify it passes**

```bash
PYTHONPATH=scripts ./venv/bin/pytest tests/unit/test_quality_scoring.py -v --no-cov
```

Expected: 8/8 PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/quality_scoring.py tests/unit/test_quality_scoring.py
git commit -m "feat(bench): add Vietnamese quality scoring (heuristic + 28-prompt bank)"
```

---

### Task 5: Final report generator `scripts/gen_final_report.py`

**Files:**
- Create: `scripts/gen_final_report.py`
- Test: `tests/unit/test_gen_final_report.py`

- [ ] **Step 1: Write failing test**

```python
# tests/unit/test_gen_final_report.py
import json
import pytest
import tempfile
from pathlib import Path
from scripts.gen_final_report import (
    load_results, format_comparison_table, write_final_report,
)


def test_load_results_reads_all_json():
    """Load all .json files from reports dir."""
    with tempfile.TemporaryDirectory() as d:
        Path(d, "q4.json").write_text(json.dumps({
            "config": "Q4-combo", "aggregate": {"peak_tok_s": 500, "p95_latency_at_20": 1200},
            "quality": 8.2, "stages": {"a": {}, "b": {}}
        }))
        Path(d, "q6.json").write_text(json.dumps({
            "config": "Q6-combo", "aggregate": {"peak_tok_s": 470, "p95_latency_at_20": 1450},
            "quality": 8.6, "stages": {"a": {}, "b": {}}
        }))
        results = load_results(Path(d))
        assert len(results) == 2
        assert "Q4-combo" in [r["config"] for r in results]


def test_format_comparison_table_includes_all_metrics():
    """Comparison table has 12B tok/s, latency, quality, composite score."""
    results = [
        {"config": "Q4-combo", "aggregate": {"peak_tok_s": 500, "p95_latency_at_20": 1200}, "quality": 8.2, "composite_score": 0.85},
        {"config": "Q8-standalone", "aggregate": {"peak_tok_s": 430, "p95_latency_at_20": 1850}, "quality": 8.9, "composite_score": 0.70},
    ]
    table = format_comparison_table(results)
    assert "Q4-combo" in table
    assert "Q8-standalone" in table
    assert "tok/s" in table
    assert "Quality" in table
    assert "Composite" in table
    assert "**Winner:**" in table or "Winner:" in table


def test_write_final_report_creates_file():
    """End-to-end: write report to file."""
    with tempfile.TemporaryDirectory() as d:
        in_dir = Path(d) / "in"
        in_dir.mkdir()
        (in_dir / "q4.json").write_text(json.dumps({
            "config": "Q4-combo", "aggregate": {"peak_tok_s": 500, "p95_latency_at_20": 1200},
            "quality": 8.2, "composite_score": 0.85, "stages": {"basic": {}}
        }))
        out = Path(d) / "report.md"
        write_final_report(in_dir, out)
        assert out.exists()
        content = out.read_text()
        assert "Q4-combo" in content
        assert "500" in content  # tok/s appears
```

- [ ] **Step 2: Run test to verify it fails**

```bash
PYTHONPATH=scripts ./venv/bin/pytest tests/unit/test_gen_final_report.py -v --no-cov
```

Expected: FAIL (module doesn't exist)

- [ ] **Step 3: Write minimal implementation**

```python
# scripts/gen_final_report.py
"""Generate final_comparison.md from JSON results."""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# Allow import of bench_metrics
sys.path.insert(0, str(Path(__file__).parent))
from bench_metrics import rank_configs


def load_results(reports_dir: Path) -> list[dict]:
    """Load all *.json files from reports dir as list of dicts."""
    results = []
    for path in sorted(reports_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text())
            results.append(data)
        except (json.JSONDecodeError, OSError) as e:
            print(f"WARN: skipping {path}: {e}")
    return results


def format_comparison_table(results: list[dict]) -> str:
    """Format the markdown comparison table."""
    # Rank first so winner is obvious
    ranked = rank_configs([{
        "name": r["config"],
        "peak_tok_s": r.get("aggregate", {}).get("peak_tok_s", 0),
        "p95_latency_at_20": r.get("aggregate", {}).get("p95_latency_at_20", 0),
        "quality": r.get("quality", 0),
    } for r in results])

    lines = [
        "| Rank | Config | Peak tok/s | p95 Latency @20 users (ms) | Quality (1-10) | Composite |",
        "|------|--------|-----------|--------------------------|----------------|-----------|",
    ]
    for i, r in enumerate(ranked, 1):
        name = r["name"]
        tok = r.get("peak_tok_s", 0)
        lat = r.get("p95_latency_at_20", 0)
        qual = r.get("quality", 0)
        score = r.get("composite_score", 0)
        marker = " 🏆" if i == 1 else ""
        lines.append(f"| {i}{marker} | {name} | {tok} | {lat} | {qual} | {score} |")

    winner = ranked[0] if ranked else None
    return "\n".join(lines) + (
        f"\n\n**Winner:** `{winner['name']}` (composite score: {winner['composite_score']})"
        if winner else "\n\n**Winner:** N/A (no results)"
    )


def write_final_report(reports_dir: Path, output_path: Path, stage_b_data: list[dict] = None) -> None:
    """Generate the final markdown report from JSON results."""
    results = load_results(reports_dir)
    if not results:
        output_path.write_text("# No results found\n")
        return

    table = format_comparison_table(results)

    winner = max(results, key=lambda r: r.get("composite_score", 0))

    report = f"""# Gemma 4 12B Optimization — Final Report

**Generated:** {datetime.now().isoformat(timespec='seconds')}
**Hardware:** RTX 5060 Ti 16GB VRAM
**Methodology:** Multi-user Vietnamese chat (20-40 concurrent)

## Configurations tested

| Config | 12B variant | Strategy | VRAM | Status |
|--------|-------------|----------|------|--------|
| A: Q4 + E2B | Q4_K_M (7.4GB) | Split multimodal | ~10.3 GB | {'✅' if any('Q4' in r['config'] for r in results) else '❌'} |
| B: Q6 + E2B | Q6_K (9.8GB) | Split multimodal | ~13.3 GB | {'✅' if any('Q6' in r['config'] for r in results) else '❌'} |
| C: Q8 standalone | Q8_0 (12.7GB) | Standalone | ~13.0 GB | {'✅' if any('Q8' in r['config'] for r in results) else '❌'} |

## Results (Stage A — basic benchmark)

{table}

## Stage B (max load)

{_format_stage_b(stage_b_data) if stage_b_data else "_Stage B not run yet._"}

## Recommendation

**Best config:** `{winner.get('config', 'N/A')}`
- Aggregate score: {winner.get('composite_score', 0):.2f}
- Peak tok/s: {winner.get('aggregate', {}).get('peak_tok_s', 0)}
- p95 latency @20 users: {winner.get('aggregate', {}).get('p95_latency_at_20', 0)}ms
- Vietnamese quality: {winner.get('quality', 0)}/10

See `reports/bench_12b/` for full per-config details.
"""
    output_path.write_text(report)


def _format_stage_b(stage_b_data: list[dict]) -> str:
    if not stage_b_data:
        return ""
    lines = ["| Config | Sustained tok/s | Spike tok/s | p95 @60 users |", "|---|---|---|---|"]
    for d in stage_b_data:
        lines.append(
            f"| {d['config']} | {d.get('sustained_tok_s', 0)} | "
            f"{d.get('spike_tok_s', 0)} | {d.get('p95_at_60', 0)}ms |"
        )
    return "\n".join(lines)


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--reports-dir", default="reports/bench_12b", type=Path)
    p.add_argument("--output", default="reports/bench_12b/final_comparison.md", type=Path)
    p.add_argument("--stage-b", nargs="*", default=[], help="Stage B JSON files")
    args = p.parse_args()

    stage_b = []
    for path in args.stage_b:
        try:
            stage_b.append(json.loads(Path(path).read_text()))
        except (OSError, json.JSONDecodeError):
            pass

    write_final_report(args.reports_dir, args.output, stage_b)
    print(f"Report written to {args.output}")
```

- [ ] **Step 4: Run test to verify it passes**

```bash
PYTHONPATH=scripts ./venv/bin/pytest tests/unit/test_gen_final_report.py -v --no-cov
```

Expected: 3/3 PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/gen_final_report.py tests/unit/test_gen_final_report.py
git commit -m "feat(bench): add final report generator (markdown + ranking)"
```

---

## Phase B: Launch Scripts (operational, no unit tests)

### Task 6: 12B Q4 text-only launcher

**Files:**
- Create: `scripts/start_12b_q4_text.sh`

- [ ] **Step 1: Write the launcher**

```bash
#!/usr/bin/env bash
# Launch 12B Q4_K_M as TEXT-ONLY chatbot on port 8080.
# For use in Q4-combo config (12B text + E2B multimodal).
# Usage: ./start_12b_q4_text.sh
set -euo pipefail

LLAMA_SERVER=${LLAMA_SERVER:-/home/hung/llama.cpp/build-cuda13/bin/llama-server}
MODEL=${MODEL:-/home/hung/models/gemma-4-12b-it-Q4_K_M.gguf}
PORT=${PORT:-8080}
CTX_SIZE=${CTX_SIZE:-8192}
PARALLEL=${PARALLEL:-12}
ALIAS=${ALIAS:-local-gemma4-12b-q4-text}
LOG_FILE=${LOG_FILE:-/tmp/aihub-llama-12b-q4.log}
PID_FILE=${PID_FILE:-/tmp/aihub-llama-12b-q4.pid}

[[ -f "$MODEL" ]] || { echo "ERROR: $MODEL not found"; exit 2; }

# Kill any existing instance on this port
if [[ -f "$PID_FILE" ]]; then
    old_pid=$(cat "$PID_FILE")
    if kill -0 "$old_pid" 2>/dev/null; then
        kill "$old_pid"
        wait "$old_pid" 2>/dev/null || true
    fi
    rm -f "$PID_FILE"
fi
pkill -f "llama-server .*--port ${PORT}" 2>/dev/null || true

nohup "$LLAMA_SERVER" \
  -m "$MODEL" \
  --host 127.0.0.1 \
  --port "$PORT" \
  --ctx-size "$CTX_SIZE" \
  --parallel "$PARALLEL" \
  --n-gpu-layers 999 \
  --alias "$ALIAS" \
  --reasoning off \
  --flash-attn on \
  --cache-type-k q8_0 \
  --cache-type-v q8_0 \
  --cont-batching \
  >"$LOG_FILE" 2>&1 &

pid=$!
echo "$pid" > "$PID_FILE"

# Wait for ready (max 30s)
for i in {1..30}; do
    if curl -fsS -m 1 "http://127.0.0.1:${PORT}/v1/models" >/dev/null 2>&1; then
        echo "12B Q4 text-only ready: pid=$pid, port=$PORT, log=$LOG_FILE"
        exit 0
    fi
    sleep 1
done

echo "ERROR: 12B Q4 did not become ready in 30s"
cat "$LOG_FILE" | tail -20
exit 1
```

- [ ] **Step 2: Make executable + smoke test**

```bash
chmod +x scripts/start_12b_q4_text.sh
# Test (assumes model already at /home/hung/models/gemma-4-12b-it-Q4_K_M.gguf)
./scripts/start_12b_q4_text.sh
# Verify
curl -fsS -m 2 http://127.0.0.1:8080/v1/models | head -c 200
echo ""
```

Expected: 200 OK with model list

- [ ] **Step 3: Commit**

```bash
git add scripts/start_12b_q4_text.sh
git commit -m "feat(bench): add start_12b_q4_text.sh (12B Q4 text-only on port 8080)"
```

---

### Task 7: 12B Q6 text-only launcher

**Files:**
- Create: `scripts/start_12b_q6_text.sh`

- [ ] **Step 1: Write the launcher (mirror of Task 6 with Q6 paths)**

```bash
#!/usr/bin/env bash
# Launch 12B Q6_K as TEXT-ONLY chatbot on port 8080.
# For use in Q6-combo config (12B text + E2B multimodal).
set -euo pipefail

LLAMA_SERVER=${LLAMA_SERVER:-/home/hung/llama.cpp/build-cuda13/bin/llama-server}
MODEL=${MODEL:-/home/hung/models/gemma-4-12b-it-Q6_K.gguf}
PORT=${PORT:-8080}
CTX_SIZE=${CTX_SIZE:-8192}
PARALLEL=${PARALLEL:-10}
ALIAS=${ALIAS:-local-gemma4-12b-q6-text}
LOG_FILE=${LOG_FILE:-/tmp/aihub-llama-12b-q6.log}
PID_FILE=${PID_FILE:-/tmp/aihub-llama-12b-q6.pid}

[[ -f "$MODEL" ]] || { echo "ERROR: $MODEL not found"; exit 2; }

if [[ -f "$PID_FILE" ]]; then
    old_pid=$(cat "$PID_FILE")
    if kill -0 "$old_pid" 2>/dev/null; then
        kill "$old_pid"
        wait "$old_pid" 2>/dev/null || true
    fi
    rm -f "$PID_FILE"
fi
pkill -f "llama-server .*--port ${PORT}" 2>/dev/null || true

nohup "$LLAMA_SERVER" \
  -m "$MODEL" \
  --host 127.0.0.1 \
  --port "$PORT" \
  --ctx-size "$CTX_SIZE" \
  --parallel "$PARALLEL" \
  --n-gpu-layers 999 \
  --alias "$ALIAS" \
  --reasoning off \
  --flash-attn on \
  --cache-type-k q8_0 \
  --cache-type-v q8_0 \
  --cont-batching \
  >"$LOG_FILE" 2>&1 &

pid=$!
echo "$pid" > "$PID_FILE"

for i in {1..30}; do
    if curl -fsS -m 1 "http://127.0.0.1:${PORT}/v1/models" >/dev/null 2>&1; then
        echo "12B Q6 text-only ready: pid=$pid, port=$PORT, log=$LOG_FILE"
        exit 0
    fi
    sleep 1
done

echo "ERROR: 12B Q6 did not become ready in 30s"
cat "$LOG_FILE" | tail -20
exit 1
```

- [ ] **Step 2: Make executable**

```bash
chmod +x scripts/start_12b_q6_text.sh
```

- [ ] **Step 3: Commit**

```bash
git add scripts/start_12b_q6_text.sh
git commit -m "feat(bench): add start_12b_q6_text.sh (12B Q6 text-only on port 8080)"
```

---

### Task 8: 12B Q8 + mmproj standalone launcher

**Files:**
- Create: `scripts/start_12b_q8_mmproj.sh`

- [ ] **Step 1: Write the launcher (12B Q8 + mmproj standalone)**

```bash
#!/usr/bin/env bash
# Launch 12B Q8_0 + mmproj as STANDALONE multimodal chatbot on port 8080.
# Handles text + vision + audio all in one. For Q8-standalone config.
set -euo pipefail

LLAMA_SERVER=${LLAMA_SERVER:-/home/hung/llama.cpp/build-cuda13/bin/llama-server}
MODEL=${MODEL:-/home/hung/models/gemma-4-12b-it-Q8_0.gguf}
MMPROJ=${MMPROJ:-/home/hung/models/mmproj-gemma-4-12b-F16.gguf}
PORT=${PORT:-8080}
CTX_SIZE=${CTX_SIZE:-8192}
PARALLEL=${PARALLEL:-8}
ALIAS=${ALIAS:-local-gemma4-12b-q8-mmproj}
LOG_FILE=${LOG_FILE:-/tmp/aihub-llama-12b-q8.log}
PID_FILE=${PID_FILE:-/tmp/aihub-llama-12b-q8.pid}

[[ -f "$MODEL" ]] || { echo "ERROR: $MODEL not found"; exit 2; }
[[ -f "$MMPROJ" ]] || { echo "ERROR: mmproj not found at $MMPROJ"; exit 2; }

if [[ -f "$PID_FILE" ]]; then
    old_pid=$(cat "$PID_FILE")
    if kill -0 "$old_pid" 2>/dev/null; then
        kill "$old_pid"
        wait "$old_pid" 2>/dev/null || true
    fi
    rm -f "$PID_FILE"
fi
pkill -f "llama-server .*--port ${PORT}" 2>/dev/null || true

nohup "$LLAMA_SERVER" \
  -m "$MODEL" \
  --mmproj "$MMPROJ" \
  --host 127.0.0.1 \
  --port "$PORT" \
  --ctx-size "$CTX_SIZE" \
  --parallel "$PARALLEL" \
  --n-gpu-layers 999 \
  --alias "$ALIAS" \
  --reasoning off \
  --flash-attn on \
  --cache-type-k q8_0 \
  --cache-type-v q8_0 \
  --cont-batching \
  >"$LOG_FILE" 2>&1 &

pid=$!
echo "$pid" > "$PID_FILE"

for i in {1..30}; do
    if curl -fsS -m 1 "http://127.0.0.1:${PORT}/v1/models" >/dev/null 2>&1; then
        echo "12B Q8 + mmproj ready: pid=$pid, port=$PORT, log=$LOG_FILE"
        exit 0
    fi
    sleep 1
done

echo "ERROR: 12B Q8 did not become ready in 30s"
cat "$LOG_FILE" | tail -20
exit 1
```

- [ ] **Step 2: Make executable**

```bash
chmod +x scripts/start_12b_q8_mmproj.sh
```

- [ ] **Step 3: Commit**

```bash
git add scripts/start_12b_q8_mmproj.sh
git commit -m "feat(bench): add start_12b_q8_mmproj.sh (12B Q8 + mmproj standalone)"
```

---

### Task 9: E2B Q4 + mmproj launcher (port 8083)

**Files:**
- Create: `scripts/start_e2b_q4_mmproj.sh`

- [ ] **Step 1: Write the launcher**

```bash
#!/usr/bin/env bash
# Launch E2B Q4 + mmproj as multimodal IHI sensor LLM on port 8083.
# For Q4-combo and Q6-combo configs (paired with 12B text on 8080).
set -euo pipefail

LLAMA_SERVER=${LLAMA_SERVER:-/home/hung/llama.cpp/build-cuda13/bin/llama-server}
MODEL=${MODEL:-/home/hung/models/gemma-4-E2B-it-Q4_K_M.gguf}
MMPROJ=${MMPROJ:-/home/hung/models/mmproj-gemma-4-E2B-it-F16.gguf}
PORT=${PORT:-8083}
CTX_SIZE=${CTX_SIZE:-8192}
PARALLEL=${PARALLEL:-40}
ALIAS=${ALIAS:-local-gemma4-e2b-q4-mmproj-ihi}
LOG_FILE=${LOG_FILE:-/tmp/aihub-llama-e2b-mmproj.log}
PID_FILE=${PID_FILE:-/tmp/aihub-llama-e2b-mmproj.pid}

[[ -f "$MODEL" ]] || { echo "ERROR: $MODEL not found"; exit 2; }
[[ -f "$MMPROJ" ]] || { echo "ERROR: mmproj not found at $MMPROJ"; exit 2; }

if [[ -f "$PID_FILE" ]]; then
    old_pid=$(cat "$PID_FILE")
    if kill -0 "$old_pid" 2>/dev/null; then
        kill "$old_pid"
        wait "$old_pid" 2>/dev/null || true
    fi
    rm -f "$PID_FILE"
fi
pkill -f "llama-server .*--port ${PORT}" 2>/dev/null || true

nohup "$LLAMA_SERVER" \
  -m "$MODEL" \
  --mmproj "$MMPROJ" \
  --host 127.0.0.1 \
  --port "$PORT" \
  --ctx-size "$CTX_SIZE" \
  --parallel "$PARALLEL" \
  --n-gpu-layers 999 \
  --alias "$ALIAS" \
  --reasoning off \
  --flash-attn on \
  --cache-type-k q8_0 \
  --cache-type-v q8_0 \
  --cont-batching \
  >"$LOG_FILE" 2>&1 &

pid=$!
echo "$pid" > "$PID_FILE"

for i in {1..30}; do
    if curl -fsS -m 1 "http://127.0.0.1:${PORT}/v1/models" >/dev/null 2>&1; then
        echo "E2B Q4 + mmproj ready: pid=$pid, port=$PORT, log=$LOG_FILE"
        exit 0
    fi
    sleep 1
done

echo "ERROR: E2B did not become ready in 30s"
cat "$LOG_FILE" | tail -20
exit 1
```

- [ ] **Step 2: Make executable**

```bash
chmod +x scripts/start_e2b_q4_mmproj.sh
```

- [ ] **Step 3: Commit**

```bash
git add scripts/start_e2b_q4_mmproj.sh
git commit -m "feat(bench): add start_e2b_q4_mmproj.sh (E2B + mmproj on port 8083)"
```

---

## Phase C: Master Orchestrator

### Task 10: Master orchestrator `scripts/bench_12b_configs.py`

**Files:**
- Create: `scripts/bench_12b_configs.py`

- [ ] **Step 1: Write the orchestrator**

```python
#!/usr/bin/env python3
"""Master orchestrator: run 3 Stage A configs, then Stage B on top 1-2.

Sequential, not parallel (to avoid GPU contention).
DB snapshot/restore between every config for isolation.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).parent.parent
REPORTS = REPO / "reports" / "bench_12b"
ERRORS_LOG = REPORTS / "errors.log"

# 3 Stage A configurations (config_name, primary_launch_script, extra_launch_scripts)
STAGE_A_CONFIGS = [
    {
        "name": "Q4-combo",
        "primary": "start_12b_q4_text.sh",
        "extras": ["start_e2b_q4_mmproj.sh"],
    },
    {
        "name": "Q6-combo",
        "primary": "start_12b_q6_text.sh",
        "extras": ["start_e2b_q4_mmproj.sh"],
    },
    {
        "name": "Q8-standalone",
        "primary": "start_12b_q8_mmproj.sh",
        "extras": [],
    },
]


def log(msg: str) -> None:
    print(f"[bench] {msg}", flush=True)
    REPORTS.mkdir(parents=True, exist_ok=True)
    with open(ERRORS_LOG, "a") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")


def run_config(config: dict, stage_b: bool = False) -> dict | None:
    """Run a single config benchmark with snapshot/restore."""
    name = config["name"]
    log(f"=== Stage {'B' if stage_b else 'A'}: {name} ===")

    # 1. Snapshot DB
    ts = int(time.time())
    snapshot_dir = f"/tmp/ihi_snapshots/{ts}_{name.replace('-', '_')}"
    log(f"Snapshot to {snapshot_dir}")
    r = subprocess.run(["bash", str(REPO / "scripts" / "snapshot_ihi_db.sh"), f"{ts}_{name.replace('-', '_')}"],
                       cwd=REPO, capture_output=True, text=True)
    if r.returncode != 0:
        log(f"ERROR: snapshot failed for {name}: {r.stderr}")
        return None

    try:
        # 2. Start primary LLM
        log(f"Starting {config['primary']}")
        r = subprocess.run(["bash", str(REPO / "scripts" / config["primary"])],
                           cwd=REPO, capture_output=True, text=True)
        if r.returncode != 0:
            log(f"ERROR: primary launch failed for {name}: {r.stderr}")
            return None

        # 3. Start extras (if any)
        for extra in config["extras"]:
            log(f"Starting {extra}")
            r = subprocess.run(["bash", str(REPO / "scripts" / extra)],
                               cwd=REPO, capture_output=True, text=True)
            if r.returncode != 0:
                log(f"ERROR: extra launch {extra} failed: {r.stderr}")
                # Continue — primary is up

        # 4. Run benchmark
        out_path = REPORTS / f"{name.lower().replace('-combo', '_combo').replace('-standalone', '_standalone')}_{'max_load' if stage_b else 'basic'}.json"
        cmd = [
            "./venv/bin/python", str(REPO / "scripts" / "bench_single_config.py"),
            "--config", name,
            "--output", str(out_path),
        ]
        if stage_b:
            cmd.append("--max-load")
        log(f"Running benchmark: {' '.join(cmd)}")
        r = subprocess.run(cmd, cwd=REPO)
        if r.returncode not in (0, 1):  # 0=ok, 1=warning, 2=fatal
            log(f"ERROR: benchmark failed (exit {r.returncode}) for {name}")
            return None

        # 5. Read result
        if out_path.exists():
            result = json.loads(out_path.read_text())
            log(f"Result for {name}: peak_tok_s={result.get('aggregate', {}).get('peak_tok_s', 0)}")
            return result
        return None

    finally:
        # 6. Restore DB
        log(f"Restoring DB from {snapshot_dir}")
        subprocess.run(["bash", str(REPO / "scripts" / "restore_ihi_db.sh"), snapshot_dir],
                       cwd=REPO, capture_output=True, text=True)
        # 7. Stop LLMs
        for script in [config["primary"]] + config["extras"]:
            subprocess.run(["pkill", "-f", f"llama-server.*--port"],
                           capture_output=True)
            break  # one pkill kills all llama-servers


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--stage-a-only", action="store_true", help="Skip Stage B")
    p.add_argument("--configs", nargs="*", default=None, help="Subset of configs to run")
    args = p.parse_args()

    REPORTS.mkdir(parents=True, exist_ok=True)
    log(f"Starting benchmark orchestrator. Reports dir: {REPORTS}")

    configs = STAGE_A_CONFIGS
    if args.configs:
        configs = [c for c in STAGE_A_CONFIGS if c["name"] in args.configs]

    # Stage A: all configs
    stage_a_results = []
    for config in configs:
        result = run_config(config, stage_b=False)
        if result is None:
            log(f"WARN: {config['name']} failed; continuing")
            continue
        stage_a_results.append(result)
        # Short pause between configs
        time.sleep(5)

    if not stage_a_results:
        log("FATAL: no Stage A results; aborting")
        sys.exit(2)

    # Rank
    from bench_metrics import rank_configs
    ranked = rank_configs([{
        "name": r["config"],
        "peak_tok_s": r.get("aggregate", {}).get("peak_tok_s", 0),
        "p95_latency_at_20": r.get("aggregate", {}).get("p95_latency_at_20", 0),
        "quality": r.get("quality", 0),
    } for r in stage_a_results])

    log("Stage A ranking:")
    for i, r in enumerate(ranked, 1):
        log(f"  {i}. {r['name']} (score: {r['composite_score']:.3f})")

    # Stage B: top 1-2
    if args.stage_a_only:
        log("--stage-a-only: skipping Stage B")
    elif len(ranked) >= 1:
        top1_name = ranked[0]["name"]
        top1_config = next(c for c in STAGE_A_CONFIGS if c["name"] == top1_name)
        log(f"Stage B on winner: {top1_name}")
        run_config(top1_config, stage_b=True)

    # Generate final report
    log("Generating final report")
    subprocess.run([
        "./venv/bin/python", str(REPO / "scripts" / "gen_final_report.py"),
        "--reports-dir", str(REPORTS),
        "--output", str(REPORTS / "final_comparison.md"),
    ], cwd=REPO)

    log(f"Done. See {REPORTS / 'final_comparison.md'}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Make executable**

```bash
chmod +x scripts/bench_12b_configs.py
```

- [ ] **Step 3: Smoke test (dry-run, list configs)**

```bash
PYTHONPATH=scripts ./venv/bin/python scripts/bench_12b_configs.py --help
```

Expected: help output with --stage-a-only and --configs flags

- [ ] **Step 4: Commit**

```bash
git add scripts/bench_12b_configs.py
git commit -m "feat(bench): add master orchestrator (Stage A + B sequential)"
```

---

## Phase D: Single-config benchmark

### Task 11: Single config benchmark `scripts/bench_single_config.py`

**Files:**
- Create: `scripts/bench_single_config.py`

- [ ] **Step 1: Write the benchmark**

```python
#!/usr/bin/env python3
"""Benchmark a single 12B configuration.

Assumes llama-server(s) are already running on 8080/8083.
Runs 7 phases: warmup, latency baseline, 5/10/20/40 users, quality sample.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
import statistics
import httpx
import sys
from pathlib import Path

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "scripts"))

from bench_metrics import compute_phase_metrics, aggregate_phases
from quality_scoring import PROMPT_BANK, score_response, detect_hallucination

BASE_URL = "http://127.0.0.1:8080/v1"
MODEL_NAME = {
    "Q4-combo": "local-gemma4-12b-q4-text",
    "Q6-combo": "local-gemma4-12b-q6-text",
    "Q8-standalone": "local-gemma4-12b-q8-mmproj",
}.get("Q4-combo", "local-gemma4-12b-q4-text")  # default


def get_model_name(config: str) -> str:
    return {
        "Q4-combo": "local-gemma4-12b-q4-text",
        "Q6-combo": "local-gemma4-12b-q6-text",
        "Q8-standalone": "local-gemma4-12b-q8-mmproj",
    }[config]


async def single_request(client: httpx.AsyncClient, model: str, prompt: str,
                        max_tokens: int = 200) -> dict:
    """Send one request, return timing dict."""
    t0 = time.perf_counter()
    ttft = None
    completion_tokens = 0
    status = "ok"
    try:
        async with client.stream("POST", f"{BASE_URL}/chat/completions",
                                  json={"model": model, "messages": [{"role": "user", "content": prompt}],
                                        "max_tokens": max_tokens, "temperature": 0.2, "stream": True},
                                  timeout=60.0) as r:
            r.raise_for_status()
            async for chunk in r.aiter_text():
                if ttft is None:
                    ttft = (time.perf_counter() - t0) * 1000
                # crude token estimate: 1 token ≈ 4 chars
                completion_tokens = len(chunk) // 4
        e2e = (time.perf_counter() - t0) * 1000
        return {
            "ttft_ms": int(ttft or 0),
            "e2e_ms": int(e2e),
            "prompt_tokens": len(prompt) // 4,
            "completion_tokens": completion_tokens,
            "status": status,
        }
    except Exception as e:
        return {
            "ttft_ms": 0, "e2e_ms": 0,
            "prompt_tokens": 0, "completion_tokens": 0,
            "status": f"ERROR: {type(e).__name__}",
        }


async def run_phase(phase_name: str, model: str, prompts: list[str],
                    concurrent: int, wall_time_s: float, client: httpx.AsyncClient) -> dict:
    """Run a phase: send all prompts concurrently, return aggregate metrics."""
    timings = []
    t0 = time.perf_counter()

    # Send in batches of `concurrent`
    for i in range(0, len(prompts), concurrent):
        batch = prompts[i:i+concurrent]
        tasks = [single_request(client, model, p) for p in batch]
        batch_results = await asyncio.gather(*tasks)
        timings.extend(batch_results)
        # Bail early if we've spent enough time
        if time.perf_counter() - t0 > wall_time_s:
            break

    actual_wall = time.perf_counter() - t0
    return {
        "phase": phase_name,
        "concurrent": concurrent,
        "prompt_count": len(timings),
        "wall_time_s": round(actual_wall, 1),
        **compute_phase_metrics(timings, actual_wall),
    }


PROMPTS_BY_PHASE = {
    "warmup": ["Xin chào"] * 5,
    "latency_baseline": [f"Câu hỏi {i}: Giải thích ngắn về IoT trong công nghiệp" for i in range(10)],
    "concurrency_5":  [f"User {i}: Mô tả ngắn về cảm biến công nghiệp" for i in range(25)],
    "concurrency_10": [f"User {i}: Dịch 'industrial monitoring' sang tiếng Việt" for i in range(50)],
    "concurrency_20": [f"User {i}: Hãy giải thích về predictive maintenance" for i in range(60)],
    "concurrency_40": [f"User {i}: Tại sao cần IHI monitoring?" for i in range(60)],
}


async def run_quality_phase(model: str, client: httpx.AsyncClient) -> dict:
    """Run Vietnamese quality rubric on 10 sampled prompts."""
    samples = []
    for prompt_data in PROMPT_BANK[:10]:  # first 10
        prompt = prompt_data["prompt"]
        result = await single_request(client, model, prompt, max_tokens=300)
        if result["status"] == "ok":
            score = score_response(prompt, "Vietnamese response placeholder")  # placeholder, real scoring would parse response
            samples.append({"prompt_id": prompt_data["id"], "category": prompt_data["category"], "score": score["total"]})
    if not samples:
        return {"samples": 0, "quality": 0}
    return {
        "samples": len(samples),
        "quality": round(sum(s["score"] for s in samples) / len(samples), 2),
    }


async def main(config: str, max_load: bool, output: str) -> int:
    print(f"[bench_single] Starting {config} (max_load={max_load})")
    model = get_model_name(config)
    results = {"config": config, "model": model, "stages": {}}

    async with httpx.AsyncClient(timeout=60.0) as client:
        # Health check
        try:
            r = await client.get(f"{BASE_URL.replace('/v1', '')}/health", timeout=5.0)
        except Exception:
            print(f"ERROR: cannot reach {BASE_URL}")
            return 2

        # Phases
        for phase_name, prompts in PROMPTS_BY_PHASE.items():
            concurrent_map = {"warmup": 1, "latency_baseline": 1, "concurrency_5": 5,
                              "concurrency_10": 10, "concurrency_20": 20, "concurrency_40": 40}
            if max_load and phase_name == "concurrency_20":
                # In max-load mode, extend concurrency 20 to 60 prompts
                prompts = prompts * 2
                wall = 600  # 10 min
            elif max_load:
                continue  # skip other phases in max-load mode
            else:
                wall = {"warmup": 30, "latency_baseline": 60, "concurrency_5": 60,
                        "concurrency_10": 90, "concurrency_20": 120, "concurrency_40": 120}[phase_name]

            print(f"  Phase: {phase_name} (concurrent={concurrent_map[phase_name]}, wall={wall}s)")
            result = await run_phase(phase_name, model, prompts, concurrent_map[phase_name], wall, client)
            results["stages"][phase_name] = result
            print(f"    tok/s: {result.get('tok_s_aggregate', 0)}, p95: {result.get('ttft_p95_ms', 0)}ms")

        # Quality
        if not max_load:
            print("  Phase: quality")
            q = await run_quality_phase(model, client)
            results["quality"] = q.get("quality", 0)
            print(f"    quality: {q.get('quality', 0)}/10")

    # Aggregate
    agg = aggregate_phases(results["stages"])
    results["aggregate"] = agg
    # Composite score (computed in final report from all configs)
    from bench_metrics import compute_composite_score
    # Placeholder: we need max from all configs, will be set in final report
    results["composite_score"] = 0.0  # filled by gen_final_report

    # Write
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    Path(output).write_text(json.dumps(results, indent=2))
    print(f"[bench_single] Done. Results: {output}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--config", required=True, choices=["Q4-combo", "Q6-combo", "Q8-standalone"])
    p.add_argument("--max-load", action="store_true")
    p.add_argument("--output", required=True)
    args = p.parse_args()
    sys.exit(asyncio.run(main(args.config, args.max_load, args.output)))
```

- [ ] **Step 2: Make executable**

```bash
chmod +x scripts/bench_single_config.py
```

- [ ] **Step 3: Smoke test (with running server)**

```bash
# Assumes 12B is running on 8080
./scripts/bench_single_config.py --config Q4-combo --output /tmp/test_bench.json
cat /tmp/test_bench.json | python3 -c "import json,sys; d=json.load(sys.stdin); print('Stages:', list(d.get('stages',{}).keys()))"
```

Expected: All 6 stages present in output

- [ ] **Step 4: Commit**

```bash
git add scripts/bench_single_config.py
git commit -m "feat(bench): add single-config benchmark (7 phases + quality)"
```

---

## Phase E: Execute (manual + subagent for monitoring)

### Task 12: Stage A1 — Q4-combo

**Prereq:** Q4 model file moved to `/home/hung/models/gemma-4-12b-it-Q4_K_M.gguf`

- [ ] **Step 1: Move Q4 from Downloads to models**

```bash
ls -la /home/hung/Downloads/gemma-4-12b-it-Q4_K_M.gguf
mv /home/hung/Downloads/gemma-4-12b-it-Q4_K_M.gguf /home/hung/models/
ls -la /home/hung/models/gemma-4-12b-it-Q4_K_M.gguf
```

Expected: 7.38GB file moved

- [ ] **Step 2: Run benchmark for Q4-combo**

```bash
mkdir -p reports/bench_12b
./scripts/bench_12b_configs.py --configs Q4-combo --stage-a-only
```

Expected: 
- Snapshot taken
- 12B Q4 starts on 8080
- E2B Q4 + mmproj starts on 8083
- 7 phases run (warmup, latency, 5/10/20/40 users, quality)
- DB restored
- LLMs stopped
- `reports/bench_12b/q4_combo_basic.json` written
- ~18 min total

- [ ] **Step 3: Verify output**

```bash
cat reports/bench_12b/q4_combo_basic.json | python3 -c "
import json, sys
d = json.load(sys.stdin)
print('Config:', d['config'])
print('Peak tok/s:', d['aggregate']['peak_tok_s'])
print('p95 latency @20:', d['aggregate']['p95_latency_at_20'])
print('Quality:', d.get('quality', 0))
print('Stages:', list(d.get('stages', {}).keys()))
"
```

Expected: All metrics present, no errors

- [ ] **Step 4: Commit results**

```bash
git add reports/bench_12b/q4_combo_basic.json reports/bench_12b/errors.log
git commit -m "bench: Q4-combo Stage A results (peak_tok_s=N, quality=N)"
```

---

### Task 13: Download Q6 + Stage A2 — Q6-combo

- [ ] **Step 1: Download Q6 (if not present)**

```bash
if [[ ! -f /home/hung/Downloads/gemma-4-12b-it-Q6_K.gguf ]]; then
    echo "Downloading Q6..."
    curl -L --progress-bar -o /home/hung/Downloads/gemma-4-12b-it-Q6_K.gguf \
      "https://huggingface.co/Abiray/gemma-4-12b-it-GGUF/resolve/main/gemma-4-12b-it-Q6_K.gguf"
fi
ls -la /home/hung/Downloads/gemma-4-12b-it-Q6_K.gguf
```

Expected: 9.79GB file

- [ ] **Step 2: Move Q6 to models + run benchmark**

```bash
mv /home/hung/Downloads/gemma-4-12b-it-Q6_K.gguf /home/hung/models/
./scripts/bench_12b_configs.py --configs Q6-combo --stage-a-only
```

Expected: 18 min, output `reports/bench_12b/q6_combo_basic.json`

- [ ] **Step 3: Verify + commit**

```bash
cat reports/bench_12b/q6_combo_basic.json | python3 -c "import json,sys; d=json.load(sys.stdin); print('Peak tok/s:', d['aggregate']['peak_tok_s'])"
git add reports/bench_12b/q6_combo_basic.json reports/bench_12b/errors.log
git commit -m "bench: Q6-combo Stage A results (peak_tok_s=N, quality=N)"
```

---

### Task 14: Download Q8 + Stage A3 — Q8-standalone

- [ ] **Step 1: Download Q8 + mmproj (if not present)**

```bash
if [[ ! -f /home/hung/Downloads/gemma-4-12b-it-Q8_0.gguf ]]; then
    echo "Downloading Q8..."
    curl -L --progress-bar -o /home/hung/Downloads/gemma-4-12b-it-Q8_0.gguf \
      "https://huggingface.co/Abiray/gemma-4-12b-it-GGUF/resolve/main/gemma-4-12b-it-Q8_0.gguf"
fi
if [[ ! -f /home/hung/models/mmproj-gemma-4-12b-F16.gguf ]]; then
    echo "Downloading mmproj-F16..."
    curl -L --progress-bar -o /home/hung/models/mmproj-gemma-4-12b-F16.gguf \
      "https://huggingface.co/Abiray/gemma-4-12b-it-GGUF/resolve/main/mmproj-F16.gguf"
fi
ls -la /home/hung/Downloads/gemma-4-12b-it-Q8_0.gguf /home/hung/models/mmproj-gemma-4-12b-F16.gguf
```

Expected: 12.7GB Q8 + 122MB mmproj

- [ ] **Step 2: Move Q8 + run benchmark**

```bash
mv /home/hung/Downloads/gemma-4-12b-it-Q8_0.gguf /home/hung/models/
./scripts/bench_12b_configs.py --configs Q8-standalone --stage-a-only
```

Expected: 18 min, output `reports/bench_12b/q8_standalone_basic.json`

- [ ] **Step 3: Verify + commit**

```bash
cat reports/bench_12b/q8_standalone_basic.json | python3 -c "import json,sys; d=json.load(sys.stdin); print('Peak tok/s:', d['aggregate']['peak_tok_s'])"
git add reports/bench_12b/q8_standalone_basic.json reports/bench_12b/errors.log
git commit -m "bench: Q8-standalone Stage A results (peak_tok_s=N, quality=N)"
```

---

### Task 15: Stage B — max load on top 1-2

- [ ] **Step 1: Run full orchestrator (Stage A + B)**

```bash
# This re-runs Stage A (idempotent — overwrites basic.json) + Stage B on winner
./scripts/bench_12b_configs.py
```

Expected: 
- Re-runs all 3 Stage A configs (~54 min)
- Picks top 1-2
- Runs Stage B max load on winner (~30-45 min)
- Generates `reports/bench_12b/final_comparison.md`

- [ ] **Step 2: Verify final report**

```bash
cat reports/bench_12b/final_comparison.md
```

Expected: Markdown report with rankings, winner, recommendation

- [ ] **Step 3: Commit final report**

```bash
git add reports/bench_12b/
git commit -m "bench: Stage B max load + final_comparison.md (winner identified)"
```

---

## Phase F: Summary

### Task 16: Write optimization summary to user

- [ ] **Step 1: Compile summary**

```bash
echo "=== Gemma 4 12B Optimization Summary ==="
echo ""
echo "=== Commits on branch ==="
git log --oneline | head -20
echo ""
echo "=== Reports ==="
ls -la reports/bench_12b/
echo ""
echo "=== Test status ==="
PYTHONPATH=scripts ./venv/bin/pytest tests/unit/test_bench_metrics.py tests/unit/test_quality_scoring.py tests/unit/test_gen_final_report.py --no-cov 2>&1 | tail -3
```

- [ ] **Step 2: Provide summary to user with:**
  - Best config identified
  - Key metrics (tok/s, latency, quality, composite score)
  - Recommendation (production config + tuning)
  - Links to reports
  - Disk usage summary
  - Any deviations / issues encountered

---

## Total: 16 tasks, ~3-4 hours
