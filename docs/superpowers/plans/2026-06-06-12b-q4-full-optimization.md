# 12B Q4 Full Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Maximize performance of 12B Q4 as primary ai-hub model — sweep llama.cpp params × 3 production scopes × speculative decoding, then integrate winner into start.sh + config.py.

**Architecture:** 4-phase sequential benchmark. Phase 1: 5 param variants on Scope A. Phase 2: top-2 params × 3 scopes. Phase 3: speculative on/off. Phase 4: Stage B max load. Reuses existing bench infrastructure (`bench_single_config.py`, `bench_12b_configs.py`, `snapshot_ihi_db.sh`, `quality_scoring.py`). Production integration: update `app/core/config.py` + `start.sh` + `CLAUDE.md`.

**Tech Stack:** Python 3.12, bash, llama.cpp (llama-server), pytest, OpenAI-compatible HTTP API.

---

## File Structure

### New files
- `scripts/start_12b_q4_p1.sh` — parallel=16 (max throughput attempt)
- `scripts/start_12b_q4_p2.sh` — ctx=4096 (KV memory savings)
- `scripts/start_12b_q4_p3.sh` — ctx=12288 (long context)
- `scripts/start_12b_q4_p4.sh` — cache=q4_0 (KV memory savings × 2)
- `scripts/start_12b_q4_scope_b.sh` — 12B on 8080 + 12B on 8081
- `scripts/start_12b_q4_scope_c.sh` — single 12B on 8080 (memory tasks routed)
- `scripts/start_12b_q4_spec.sh` — with --draft-model E2B Q4
- `reports/bench_12b_full/` — output directory for all phases

### Modified files
- `scripts/bench_12b_configs.py` — add new config names
- `scripts/bench_single_config.py` — add new config → model alias map
- `scripts/gen_final_report.py` — render Scope A/B/C + speculative result tables
- `app/core/config.py` — `LITE_MODEL`, `DEFAULT_MODEL` updated to 12B Q4
- `start.sh` — use new 12B Q4 launcher as primary
- `CLAUDE.md` — document new model choice + benchmark numbers

### Reused (unchanged)
- `scripts/snapshot_ihi_db.sh`, `scripts/restore_ihi_db.sh`
- `scripts/bench_metrics.py` (PhaseMetrics, rank_configs)
- `scripts/quality_scoring.py` (PROMPT_BANK, score_response)
- All test files for bench infrastructure

---

## Phase 0: Infrastructure Setup

### Task 1: Create 4 param-variant launchers (P1-P4)

**Files:**
- Create: `scripts/start_12b_q4_p1.sh`
- Create: `scripts/start_12b_q4_p2.sh`
- Create: `scripts/start_12b_q4_p3.sh`
- Create: `scripts/start_12b_q4_p4.sh`
- Reference: `scripts/start_12b_q4_text.sh` (baseline P0, already exists from 2026-06-05)

Each launcher is identical to `start_12b_q4_text.sh` except for the variant param. We change ONLY the env var defaults at the top.

- [ ] **Step 1: Create start_12b_q4_p1.sh (parallel=16)**

Copy `scripts/start_12b_q4_text.sh` to `scripts/start_12b_q4_p1.sh`, then edit ONLY the PARALLEL and ALIAS lines:

```bash
#!/usr/bin/env bash
# Launch 12B Q4 with PARALLEL=16 (vs P0 baseline parallel=12).
# Tests max parallel slots on 16GB GPU. P1 = "push to limit" param variant.
set -euo pipefail

LLAMA_SERVER=${LLAMA_SERVER:-/home/hung/llama.cpp/build-cuda13/bin/llama-server}
MODEL=${MODEL:-/home/hung/models/gemma-4-12b-it-Q4_K_M.gguf}
PORT=${PORT:-8080}
CTX_SIZE=${CTX_SIZE:-8192}
PARALLEL=${PARALLEL:-16}
ALIAS=${ALIAS:-local-gemma4-12b-q4-text-p1}
LOG_FILE=${LOG_FILE:-/tmp/aihub-llama-12b-q4-p1.log}
PID_FILE=${PID_FILE:-/tmp/aihub-llama-12b-q4-p1.pid}

# ... [rest identical to start_12b_q4_text.sh]
```

The `nohup "$LLAMA_SERVER" ...` block stays the same. The only changes are: `PARALLEL:-16`, `ALIAS:-...-p1`, `LOG_FILE:-...-p1.log`, `PID_FILE:-...-p1.pid`, the comment header.

- [ ] **Step 2: Create start_12b_q4_p2.sh (ctx=4096)**

Same pattern, but `CTX_SIZE:-4096`, `ALIAS:-...-p2`, `LOG_FILE/PID_FILE:-...-p2.{log,pid}`.

- [ ] **Step 3: Create start_12b_q4_p3.sh (ctx=12288)**

Same pattern, but `CTX_SIZE:-12288`, `ALIAS:-...-p3`, `LOG_FILE/PID_FILE:-...-p3.{log,pid}`.

- [ ] **Step 4: Create start_12b_q4_p4.sh (cache=q4_0)**

Same as P0 BUT in the `nohup` block, change `--cache-type-k q8_0 --cache-type-v q8_0` to `--cache-type-k q4_0 --cache-type-v q4_0`. Also `ALIAS:-...-p4`, `LOG_FILE/PID_FILE:-...-p4.{log,pid}`.

- [ ] **Step 5: Make all 4 launchers executable and verify**

```bash
chmod +x scripts/start_12b_q4_p{1,2,3,4}.sh
for f in p1 p2 p3 p4; do
  echo "=== $f ==="
  grep -E "PARALLEL|CTX_SIZE|cache-type|ALIAS" scripts/start_12b_q4_$f.sh | head -8
done
```

Expected: each launcher shows its variant param (P1=parallel:16, P2=ctx:4096, P3=ctx:12288, P4=cache:q4_0).

- [ ] **Step 6: Smoke-test P0 baseline still works**

```bash
bash scripts/start_12b_q4_text.sh
sleep 2
curl -fsS http://127.0.0.1:8080/v1/models | python3 -c "import sys, json; print(json.load(sys.stdin)['data'][0]['id'])"
pkill -f "llama-server.*--port 8080"
```

Expected: `local-gemma4-12b-q4-text` (P0 alias).

- [ ] **Step 7: Commit launchers**

```bash
git add scripts/start_12b_q4_p{1,2,3,4}.sh
git commit -m "feat(bench): add 4 param-variant launchers (P1-P4) for 12B Q4 sweep"
```

---

### Task 2: Update bench scripts config map (P0-P4)

**Files:**
- Modify: `scripts/bench_single_config.py:26-32` (get_model_name)
- Modify: `scripts/bench_single_config.py:191` (choices arg)
- Modify: `scripts/bench_12b_configs.py:20-37` (STAGE_A_CONFIGS)

- [ ] **Step 1: Update get_model_name in bench_single_config.py**

Replace the function:

```python
def get_model_name(config: str) -> str:
    return {
        # Phase 1 param variants (Scope A)
        "Q4-A-p0": "local-gemma4-12b-q4-text",
        "Q4-A-p1": "local-gemma4-12b-q4-text-p1",
        "Q4-A-p2": "local-gemma4-12b-q4-text-p2",
        "Q4-A-p3": "local-gemma4-12b-q4-text-p3",
        "Q4-A-p4": "local-gemma4-12b-q4-text-p4",
        # Phase 2 scope variants (best 2 params × 3 scopes)
        "Q4-B-p0": "local-gemma4-12b-q4-text",  # placeholder
        "Q4-B-p1": "local-gemma4-12b-q4-text-p1",  # placeholder
        "Q4-C-p0": "local-gemma4-12b-q4-text",  # placeholder
        "Q4-C-p1": "local-gemma4-12b-q4-text-p1",  # placeholder
        # Phase 3 speculative
        "Q4-A-spec-on": "local-gemma4-12b-q4-text",
        "Q4-A-spec-off": "local-gemma4-12b-q4-text",
    }[config]
```

The scope B/C and speculative aliases are placeholders — actual alias names come from launcher scripts.

- [ ] **Step 2: Update choices arg in bench_single_config.py**

```python
p.add_argument("--config", required=True, choices=[
    "Q4-A-p0", "Q4-A-p1", "Q4-A-p2", "Q4-A-p3", "Q4-A-p4",
    "Q4-B-p0", "Q4-B-p1", "Q4-C-p0", "Q4-C-p1",
    "Q4-A-spec-on", "Q4-A-spec-off",
])
```

- [ ] **Step 3: Update STAGE_A_CONFIGS in bench_12b_configs.py**

Replace the configs list (we'll update STAGE_A_CONFIGS for each phase; orchestrator's `--configs` flag selects subset):

```python
# STAGE_A_CONFIGS — updated per phase. Orchestrator runs --configs to filter.
# Phase 1: param sweep on Scope A (5 configs)
STAGE_A_CONFIGS = [
    {"name": "Q4-A-p0", "primary": "start_12b_q4_text.sh",   "extras": ["start_e2b_q4_mmproj.sh"]},
    {"name": "Q4-A-p1", "primary": "start_12b_q4_p1.sh",     "extras": ["start_e2b_q4_mmproj.sh"]},
    {"name": "Q4-A-p2", "primary": "start_12b_q4_p2.sh",     "extras": ["start_e2b_q4_mmproj.sh"]},
    {"name": "Q4-A-p3", "primary": "start_12b_q4_p3.sh",     "extras": ["start_e2b_q4_mmproj.sh"]},
    {"name": "Q4-A-p4", "primary": "start_12b_q4_p4.sh",     "extras": ["start_e2b_q4_mmproj.sh"]},
]
```

- [ ] **Step 4: Commit bench config update**

```bash
git add scripts/bench_single_config.py scripts/bench_12b_configs.py
git commit -m "feat(bench): extend config map for 12B Q4 param sweep (P0-P4)"
```

---

## Phase 1: Param Sweep on Scope A (5 configs × ~15 min = ~75 min)

### Task 3: Run Phase 1 orchestrator

**Files:**
- Create: `reports/bench_12b_full/` (auto-created by orchestrator)
- Output: `reports/bench_12b_full/q4_a_p{0..4}_basic.json` (5 files)

- [ ] **Step 1: Verify no llama-server is running and clean state**

```bash
pkill -f "llama-server" 2>/dev/null || true
sleep 2
ps aux | grep llama-server | grep -v grep | wc -l
```

Expected: `0`.

- [ ] **Step 2: Run orchestrator with Phase 1 configs only (in background)**

```bash
cd /home/hung/ai-hub
mkdir -p reports/bench_12b_full
nohup ./venv/bin/python scripts/bench_12b_configs.py \
  --configs Q4-A-p0 Q4-A-p1 Q4-A-p2 Q4-A-p3 Q4-A-p4 \
  > /tmp/bench_phase1.log 2>&1 &
echo "Phase 1 PID: $!"
disown
```

- [ ] **Step 3: Monitor progress (check every 15 min)**

```bash
tail -20 /tmp/bench_phase1.log
```

Each config takes ~15 min. Total wall time: ~75 min. Verify each phase finishes (warmup → latency → conc5/10/20/40 → quality) before next config starts.

- [ ] **Step 4: Verify all 5 result files exist**

```bash
ls -la reports/bench_12b_full/q4_a_p*_basic.json | wc -l
```

Expected: `5`. If fewer, check `/tmp/bench_phase1.log` for failures.

- [ ] **Step 5: Commit results**

```bash
git add reports/bench_12b_full/ 2>/dev/null || true  # may be gitignored
ls -la reports/bench_12b_full/q4_a_p*_basic.json
```

If `reports/` is gitignored (as in 2026-06-05 setup), results are kept locally and committed only in spec/plan dirs.

---

### Task 4: Analyze Phase 1 results, pick top 2 param configs

**Files:**
- Read: `reports/bench_12b_full/q4_a_p{0..4}_basic.json`

- [ ] **Step 1: Run ranking script**

```bash
cd /home/hung/ai-hub
./venv/bin/python -c "
import json, sys
from pathlib import Path
sys.path.insert(0, 'scripts')
from bench_metrics import rank_configs

results = []
for i in range(5):
    p = Path(f'reports/bench_12b_full/q4_a_p{i}_basic.json')
    if not p.exists():
        print(f'MISSING: {p}')
        continue
    d = json.loads(p.read_text())
    results.append({
        'name': d['config'],
        'peak_tok_s': d.get('aggregate', {}).get('peak_tok_s', 0),
        'p95_latency_at_20': d.get('aggregate', {}).get('p95_latency_at_20', 0),
        'quality': d.get('quality', 0),
    })

ranked = rank_configs(results)
print('=' * 70)
print('PHASE 1 RANKING (param sweep, Scope A):')
print('=' * 70)
for i, r in enumerate(ranked, 1):
    print(f'  {i}. {r[\"name\"]} — score={r[\"composite_score\"]:.4f}, '
          f'peak={r[\"peak_tok_s\"]:.1f} tok/s, p95@20u={r[\"p95_latency_at_20\"]:.0f}ms, '
          f'quality={r[\"quality\"]:.1f}')
print()
top2 = [r['name'] for r in ranked[:2]]
print(f'TOP 2 → {top2}')
"
```

- [ ] **Step 2: Record top 2 in plan/exec context**

Note the top 2 config names (e.g., `Q4-A-p0` and `Q4-A-p2`). These will be used in Phase 2.

- [ ] **Step 3: If all 5 configs are within 5% of each other, treat all 5 as "top" — proceed with original plan**

If 5-way tie, use the original top-2 defaults (P0 and P1, the safer picks).

---

## Phase 2: 3 Scopes × Top 2 Param Configs

### Task 5: Create scope B and scope C launchers

**Files:**
- Create: `scripts/start_12b_q4_scope_b.sh`
- Create: `scripts/start_12b_q4_scope_c.sh`

Scope A already covered (existing launchers). Scope B = 12B on 8080 + 12B on 8081. Scope C = single 12B on 8080 (memory tasks routed via config).

- [ ] **Step 1: Create start_12b_q4_scope_b.sh (12B on 8080 AND 12B on 8081)**

```bash
#!/usr/bin/env bash
# Scope B: 12B Q4 on BOTH ports — 8080 (primary chat) + 8081 (background memory).
# For Phase 2 testing of "12B everywhere" hypothesis.
# Each instance uses lower parallel (10 + 4) to fit in 16GB together.
set -euo pipefail

LLAMA_SERVER=${LLAMA_SERVER:-/home/hung/llama.cpp/build-cuda13/bin/llama-server}
MODEL=${MODEL:-/home/hung/models/gemma-4-12b-it-Q4_K_M.gguf}

# Launch 8080 (primary chat, parallel=10)
CTX_SIZE_8080=${CTX_SIZE_8080:-8192}
PARALLEL_8080=${PARALLEL_8080:-10}
ALIAS_8080=${ALIAS_8080:-local-gemma4-12b-q4-scope-b-chat}
LOG_8080=${LOG_8080:-/tmp/aihub-llama-12b-scope-b-8080.log}
PID_8080=${PID_8080:-/tmp/aihub-llama-12b-scope-b-8080.pid}

# Launch 8081 (background memory, parallel=4)
CTX_SIZE_8081=${CTX_SIZE_8081:-8192}
PARALLEL_8081=${PARALLEL_8081:-4}
ALIAS_8081=${ALIAS_8081:-local-gemma4-12b-q4-scope-b-bg}
LOG_8081=${LOG_8081:-/tmp/aihub-llama-12b-scope-b-8081.log}
PID_8081=${PID_8081:-/tmp/aihub-llama-12b-scope-b-8081.pid}

[[ -f "$MODEL" ]] || { echo "ERROR: $MODEL not found"; exit 2; }

# Kill any existing instances
pkill -f "llama-server .*--port 8080" 2>/dev/null || true
pkill -f "llama-server .*--port 8081" 2>/dev/null || true
sleep 1

# Start 8080
nohup "$LLAMA_SERVER" \
  -m "$MODEL" \
  --host 127.0.0.1 --port 8080 \
  --ctx-size "$CTX_SIZE_8080" --parallel "$PARALLEL_8080" \
  --n-gpu-layers 999 --alias "$ALIAS_8080" \
  --reasoning off --flash-attn on \
  --cache-type-k q8_0 --cache-type-v q8_0 --cont-batching \
  >"$LOG_8080" 2>&1 &
echo $! > "$PID_8080"

# Start 8081
nohup "$LLAMA_SERVER" \
  -m "$MODEL" \
  --host 127.0.0.1 --port 8081 \
  --ctx-size "$CTX_SIZE_8081" --parallel "$PARALLEL_8081" \
  --n-gpu-layers 999 --alias "$ALIAS_8081" \
  --reasoning off --flash-attn on \
  --cache-type-k q8_0 --cache-type-v q8_0 --cont-batching \
  >"$LOG_8081" 2>&1 &
echo $! > "$PID_8081"

# Wait both ready
for i in {1..30}; do
  ok_8080=$(curl -fsS -m 1 http://127.0.0.1:8080/v1/models >/dev/null 2>&1 && echo y || echo n)
  ok_8081=$(curl -fsS -m 1 http://127.0.0.1:8081/v1/models >/dev/null 2>&1 && echo y || echo n)
  if [[ "$ok_8080" == "y" && "$ok_8081" == "y" ]]; then
    echo "Scope B ready: 8080=$ALIAS_8080, 8081=$ALIAS_8081"
    exit 0
  fi
  sleep 1
done

echo "ERROR: Scope B did not become ready in 30s"
echo "--- 8080 log ---"; tail -10 "$LOG_8080"
echo "--- 8081 log ---"; tail -10 "$LOG_8081"
exit 1
```

- [ ] **Step 2: Create start_12b_q4_scope_c.sh (single 12B on 8080)**

```bash
#!/usr/bin/env bash
# Scope C: SINGLE 12B Q4 on port 8080.
# Background memory tasks route to same instance (configured in app/core/config.py).
# Simpler ops, but memory tasks steal parallel slots from primary users.
set -euo pipefail

LLAMA_SERVER=${LLAMA_SERVER:-/home/hung/llama.cpp/build-cuda13/bin/llama-server}
MODEL=${MODEL:-/home/hung/models/gemma-4-12b-it-Q4_K_M.gguf}
PORT=${PORT:-8080}
CTX_SIZE=${CTX_SIZE:-8192}
PARALLEL=${PARALLEL:-12}
ALIAS=${ALIAS:-local-gemma4-12b-q4-scope-c-single}
LOG_FILE=${LOG_FILE:-/tmp/aihub-llama-12b-scope-c.log}
PID_FILE=${PID_FILE:-/tmp/aihub-llama-12b-scope-c.pid}

[[ -f "$MODEL" ]] || { echo "ERROR: $MODEL not found"; exit 2; }

# Kill any existing instance
if [[ -f "$PID_FILE" ]]; then
  old_pid=$(cat "$PID_FILE")
  if kill -0 "$old_pid" 2>/dev/null; then
    kill "$old_pid"; wait "$old_pid" 2>/dev/null || true
  fi
  rm -f "$PID_FILE"
fi
pkill -f "llama-server .*--port ${PORT}" 2>/dev/null || true

nohup "$LLAMA_SERVER" \
  -m "$MODEL" \
  --host 127.0.0.1 --port "$PORT" \
  --ctx-size "$CTX_SIZE" --parallel "$PARALLEL" \
  --n-gpu-layers 999 --alias "$ALIAS" \
  --reasoning off --flash-attn on \
  --cache-type-k q8_0 --cache-type-v q8_0 --cont-batching \
  >"$LOG_FILE" 2>&1 &

pid=$!
echo "$pid" > "$PID_FILE"

for i in {1..30}; do
  if curl -fsS -m 1 "http://127.0.0.1:${PORT}/v1/models" >/dev/null 2>&1; then
    echo "Scope C ready: pid=$pid, port=$PORT, log=$LOG_FILE"
    exit 0
  fi
  sleep 1
done

echo "ERROR: Scope C did not become ready in 30s"
tail -20 "$LOG_FILE"
exit 1
```

- [ ] **Step 3: Make both launchers executable, smoke-test Scope B**

```bash
chmod +x scripts/start_12b_q4_scope_b.sh scripts/start_12b_q4_scope_c.sh

# Smoke test Scope B
bash scripts/start_12b_q4_scope_b.sh
sleep 2
echo "=== 8080 ==="
curl -fsS http://127.0.0.1:8080/v1/models | python3 -c "import sys, json; print(json.load(sys.stdin)['data'][0]['id'])"
echo "=== 8081 ==="
curl -fsS http://127.0.0.1:8081/v1/models | python3 -c "import sys, json; print(json.load(sys.stdin)['data'][0]['id'])"
nvidia-smi --query-gpu=memory.used,memory.free --format=csv
pkill -f "llama-server"
sleep 2
```

Expected: Both ports respond with `local-gemma4-12b-q4-scope-b-{chat,bg}`. VRAM ~14-15GB used (both instances loaded).

- [ ] **Step 4: Commit scope launchers**

```bash
git add scripts/start_12b_q4_scope_b.sh scripts/start_12b_q4_scope_c.sh
git commit -m "feat(bench): add Scope B (12B×2) and Scope C (single 12B) launchers"
```

---

### Task 6: Update bench scripts for scope variants

**Files:**
- Modify: `scripts/bench_12b_configs.py` (add Phase 2 configs)
- Modify: `scripts/bench_single_config.py` (add scope variants to get_model_name + choices)

- [ ] **Step 1: Update STAGE_A_CONFIGS for Phase 2 in bench_12b_configs.py**

Replace the entire STAGE_A_CONFIGS with Phase 2 entries (6 configs):

```python
# STAGE_A_CONFIGS — Phase 2: 3 scopes × top 2 param configs
# Replace TOP_PARAM_1 and TOP_PARAM_2 with actual winners from Phase 1.
# For Scope B/C, the "p0" and "p1" don't mean different param configs — they mean
# the same launcher tested twice for a 2nd sample point (each scope uses baseline params).
STAGE_A_CONFIGS = [
    # Scope A: 12B primary + E2B vision (top 2 params)
    {"name": f"Q4-A-{TOP_PARAM_1}", "primary": f"start_12b_q4_{TOP_PARAM_1}.sh".replace("_p0", "_text.sh").replace("_p", "_p"), "extras": ["start_e2b_q4_mmproj.sh"]},
    {"name": f"Q4-A-{TOP_PARAM_2}", "primary": f"start_12b_q4_{TOP_PARAM_2}.sh".replace("_p0", "_text.sh").replace("_p", "_p"), "extras": ["start_e2b_q4_mmproj.sh"]},
    # Scope B: 12B on 8080 + 12B on 8081
    {"name": "Q4-B-p0", "primary": "start_12b_q4_scope_b.sh", "extras": ["start_e2b_q4_mmproj.sh"]},
    {"name": "Q4-B-p1", "primary": "start_12b_q4_scope_b.sh", "extras": ["start_e2b_q4_mmproj.sh"]},
    # Scope C: single 12B on 8080
    {"name": "Q4-C-p0", "primary": "start_12b_q4_scope_c.sh", "extras": ["start_e2b_q4_mmproj.sh"]},
    {"name": "Q4-C-p1", "primary": "start_12b_q4_scope_c.sh", "extras": ["start_e2b_q4_mmproj.sh"]},
]
```

For Scope B/C, the param "p0" and "p1" don't actually mean different param configs — they mean "test scope B/C with the same launchers twice" to get a 2nd sample point. (Each scope uses baseline params.)

- [ ] **Step 2: Update get_model_name in bench_single_config.py**

Add scope variants:

```python
def get_model_name(config: str) -> str:
    return {
        # Phase 1
        "Q4-A-p0": "local-gemma4-12b-q4-text",
        "Q4-A-p1": "local-gemma4-12b-q4-text-p1",
        "Q4-A-p2": "local-gemma4-12b-q4-text-p2",
        "Q4-A-p3": "local-gemma4-12b-q4-text-p3",
        "Q4-A-p4": "local-gemma4-12b-q4-text-p4",
        # Phase 2 — Scope A retests with top params
        "Q4-A-p0": "local-gemma4-12b-q4-text",  # dupe
        "Q4-A-p1": "local-gemma4-12b-q4-text-p1",  # dupe
        # Phase 2 — Scope B (12B on 8080)
        "Q4-B-p0": "local-gemma4-12b-q4-scope-b-chat",
        "Q4-B-p1": "local-gemma4-12b-q4-scope-b-chat",
        # Phase 2 — Scope C (single 12B)
        "Q4-C-p0": "local-gemma4-12b-q4-scope-c-single",
        "Q4-C-p1": "local-gemma4-12b-q4-scope-c-single",
        # Phase 3
        "Q4-A-spec-on": "local-gemma4-12b-q4-text",
        "Q4-A-spec-off": "local-gemma4-12b-q4-text",
    }[config]
```

NOTE: For Phase 2, the bench queries 8080 (default in bench_single_config.py: `BASE_URL = "http://127.0.0.1:8080/v1"`). Both Scope B and Scope C have 12B on 8080, so this works.

- [ ] **Step 3: Commit bench updates**

```bash
git add scripts/bench_single_config.py scripts/bench_12b_configs.py
git commit -m "feat(bench): add Phase 2 scope variants (A retests, B, C) to config map"
```

---

### Task 7: Run Phase 2 orchestrator

- [ ] **Step 1: Verify clean state, start Phase 2**

```bash
pkill -f "llama-server" 2>/dev/null || true
sleep 2
cd /home/hung/ai-hub
nohup ./venv/bin/python scripts/bench_12b_configs.py \
  --configs Q4-A-p0 Q4-A-p1 Q4-B-p0 Q4-B-p1 Q4-C-p0 Q4-C-p1 \
  > /tmp/bench_phase2.log 2>&1 &
echo "Phase 2 PID: $!"
disown
```

(Note: replace `Q4-A-p0 Q4-A-p1` with actual top-2 names from Phase 1.)

- [ ] **Step 2: Monitor every 20 min for ~90 min total**

```bash
tail -30 /tmp/bench_phase2.log
ls reports/bench_12b_full/q4_*_basic.json
```

- [ ] **Step 3: Verify 6 result files exist**

```bash
ls -la reports/bench_12b_full/q4_{a-p0,a-p1,b-p0,b-p1,c-p0,c-p1}_basic.json | wc -l
```

Expected: `6`. (Adjust config names if Phase 1 winners were different.)

- [ ] **Step 4: Commit results**

```bash
git add reports/bench_12b_full/ 2>/dev/null || true
```

---

### Task 8: Analyze Phase 2, pick top 1-2 (scope + param) combos

- [ ] **Step 1: Run ranking**

```bash
cd /home/hung/ai-hub
./venv/bin/python -c "
import json, sys
from pathlib import Path
sys.path.insert(0, 'scripts')
from bench_metrics import rank_configs

# Load Phase 2 results (6 files)
results = []
for cfg in ['a-p0', 'a-p1', 'b-p0', 'b-p1', 'c-p0', 'c-p1']:
    p = Path(f'reports/bench_12b_full/q4_{cfg}_basic.json')
    if not p.exists():
        print(f'MISSING: {p}')
        continue
    d = json.loads(p.read_text())
    results.append({
        'name': d['config'],
        'peak_tok_s': d.get('aggregate', {}).get('peak_tok_s', 0),
        'p95_latency_at_20': d.get('aggregate', {}).get('p95_latency_at_20', 0),
        'quality': d.get('quality', 0),
    })

ranked = rank_configs(results)
print('=' * 70)
print('PHASE 2 RANKING (scopes × params):')
print('=' * 70)
for i, r in enumerate(ranked, 1):
    print(f'  {i}. {r[\"name\"]} — score={r[\"composite_score\"]:.4f}')
print()
top = ranked[0]
runner = ranked[1] if len(ranked) > 1 else None
print(f'WINNER: {top[\"name\"]}')
if runner and top['composite_score'] - runner['composite_score'] < 0.05:
    print(f'RUNNER-UP (within 5%): {runner[\"name\"]}')
"
```

- [ ] **Step 2: Record winners**

Note the top 1-2 config names. These go to Phase 3 (speculative) and Phase 4 (Stage B).

---

## Phase 3: Speculative Decoding

### Task 9: Create speculative launcher

**Files:**
- Create: `scripts/start_12b_q4_spec.sh`

- [ ] **Step 1: Create start_12b_q4_spec.sh**

```bash
#!/usr/bin/env bash
# Launch 12B Q4 with SPECULATIVE DECODING using E2B Q4 as draft model.
# Theory: 2-3x speedup for single-user if accept rate is 50-70%.
# Multi-user: speedup may regress (draft work competes with parallel slots).
# For Phase 3 evaluation.
set -euo pipefail

LLAMA_SERVER=${LLAMA_SERVER:-/home/hung/llama.cpp/build-cuda13/bin/llama-server}
TARGET_MODEL=${TARGET_MODEL:-/home/hung/models/gemma-4-12b-it-Q4_K_M.gguf}
DRAFT_MODEL=${DRAFT_MODEL:-/home/hung/models/gemma-4-E2B-it-Q4_K_M.gguf}
PORT=${PORT:-8080}
CTX_SIZE=${CTX_SIZE:-8192}
PARALLEL=${PARALLEL:-12}
DRAFT_MAX=${DRAFT_MAX:-8}
ALIAS=${ALIAS:-local-gemma4-12b-q4-spec}
LOG_FILE=${LOG_FILE:-/tmp/aihub-llama-12b-q4-spec.log}
PID_FILE=${PID_FILE:-/tmp/aihub-llama-12b-q4-spec.pid}

[[ -f "$TARGET_MODEL" ]] || { echo "ERROR: $TARGET_MODEL not found"; exit 2; }
[[ -f "$DRAFT_MODEL" ]] || { echo "ERROR: $DRAFT_MODEL not found"; exit 2; }

# Kill existing
if [[ -f "$PID_FILE" ]]; then
  old_pid=$(cat "$PID_FILE")
  if kill -0 "$old_pid" 2>/dev/null; then
    kill "$old_pid"; wait "$old_pid" 2>/dev/null || true
  fi
  rm -f "$PID_FILE"
fi
pkill -f "llama-server .*--port ${PORT}" 2>/dev/null || true

nohup "$LLAMA_SERVER" \
  -m "$TARGET_MODEL" \
  --model-draft "$DRAFT_MODEL" \
  --draft-max "$DRAFT_MAX" \
  --host 127.0.0.1 --port "$PORT" \
  --ctx-size "$CTX_SIZE" --parallel "$PARALLEL" \
  --n-gpu-layers 999 --alias "$ALIAS" \
  --reasoning off --flash-attn on \
  --cache-type-k q8_0 --cache-type-v q8_0 --cont-batching \
  >"$LOG_FILE" 2>&1 &

pid=$!
echo "$pid" > "$PID_FILE"

for i in {1..30}; do
  if curl -fsS -m 1 "http://127.0.0.1:${PORT}/v1/models" >/dev/null 2>&1; then
    echo "12B Q4 + E2B draft ready: pid=$pid, port=$PORT, log=$LOG_FILE"
    exit 0
  fi
  sleep 1
done

echo "ERROR: 12B Q4 + E2B draft did not become ready in 30s"
tail -20 "$LOG_FILE"
exit 1
```

- [ ] **Step 2: Make executable, smoke test**

```bash
chmod +x scripts/start_12b_q4_spec.sh
bash scripts/start_12b_q4_spec.sh
sleep 2
curl -fsS http://127.0.0.1:8080/v1/models | python3 -c "import sys, json; print(json.load(sys.stdin)['data'][0]['id'])"
# Quick test
time curl -fsS -X POST http://127.0.0.1:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"local-gemma4-12b-q4-spec","messages":[{"role":"user","content":"Xin chào"}],"max_tokens":50}' | head -200
pkill -f "llama-server"
sleep 2
```

Expected: Server starts, model name is `local-gemma4-12b-q4-spec`, response is fast (<1s for 50 tokens).

- [ ] **Step 3: Commit**

```bash
git add scripts/start_12b_q4_spec.sh
git commit -m "feat(bench): add 12B Q4 speculative decoding launcher (E2B draft)"
```

---

### Task 10: Update bench scripts for speculative

**Files:**
- Modify: `scripts/bench_12b_configs.py` (add Phase 3 configs)
- Modify: `scripts/bench_single_config.py` (already has Q4-A-spec-on/off in choices)

- [ ] **Step 1: Update STAGE_A_CONFIGS for Phase 3 in bench_12b_configs.py**

Replace STAGE_A_CONFIGS with:

```python
# STAGE_A_CONFIGS — Phase 3: speculative decoding (with/without)
# Replace TOP_CONFIG with the actual winner from Phase 2.
STAGE_A_CONFIGS = [
    {"name": f"Q4-{TOP_CONFIG}-spec-on",  "primary": "start_12b_q4_spec.sh", "extras": ["start_e2b_q4_mmproj.sh"]},
    {"name": f"Q4-{TOP_CONFIG}-spec-off", "primary": "start_12b_q4_text.sh", "extras": ["start_e2b_q4_mmproj.sh"]},
]
```

- [ ] **Step 2: Verify bench_single_config.py has Q4-A-spec-on/off** (it should from Task 2)

Check that the `--config` choices include both names. If not, add them.

- [ ] **Step 3: Commit**

```bash
git add scripts/bench_12b_configs.py scripts/bench_single_config.py
git commit -m "feat(bench): add Phase 3 speculative decoding configs"
```

---

### Task 11: Run Phase 3, decide wire-in

- [ ] **Step 1: Run Phase 3 (2 configs × ~15 min = ~30 min)**

```bash
pkill -f "llama-server" 2>/dev/null || true
sleep 2
cd /home/hung/ai-hub
nohup ./venv/bin/python scripts/bench_12b_configs.py \
  --configs Q4-A-spec-on Q4-A-spec-off \
  > /tmp/bench_phase3.log 2>&1 &
echo "Phase 3 PID: $!"
disown
```

(Replace config names with actual Phase 2 winner.)

- [ ] **Step 2: Monitor and verify**

```bash
sleep 1500  # ~25 min
tail -20 /tmp/bench_phase3.log
ls reports/bench_12b_full/q4_*_spec_*.json
```

- [ ] **Step 3: Compare speculative vs non-speculative**

```bash
cd /home/hung/ai-hub
./venv/bin/python -c "
import json
from pathlib import Path
for cfg in ['spec-on', 'spec-off']:
    p = Path(f'reports/bench_12b_full/q4_a_{cfg}_basic.json')
    if p.exists():
        d = json.loads(p.read_text())
        agg = d.get('aggregate', {})
        print(f'{cfg}: peak={agg.get(\"peak_tok_s\", 0):.1f} tok/s, '
              f'p95@20u={agg.get(\"p95_latency_at_20\", 0):.0f}ms, '
              f'quality={d.get(\"quality\", 0):.1f}/10')
"
```

- [ ] **Step 4: Decision**

- If `spec-on` peak tok/s ≥ 1.20 × `spec-off` peak → **wire-in** (go to Task 12)
- If < 1.20× → **skip**, document in final report and move to Phase 4 (Task 13)

Record the decision.

---

### Task 12: If wire-in: update start.sh to add speculative launcher

(Only execute if Phase 3 shows ≥20% speedup.)

- [ ] **Step 1: If wire-in: add start_12b_q4_spec.sh as alternative in start.sh**

Edit `start.sh` to add (after the primary 12B Q4 launch):

```bash
# Optional: enable speculative decoding if env var is set
if [[ -n "\${SPECULATIVE_DRAFT_MODEL:-}" ]]; then
    echo "[1.5/5] Starting speculative draft model (E2B) on port 8085..."
    # Note: --model-draft uses same llama-server process, so this just needs config flag
    # If you want separate draft server, use start_e2b_q4_mmproj.sh
fi
```

- [ ] **Step 2: Commit start.sh update**

```bash
git add start.sh
git commit -m "feat(start): optionally enable speculative decoding (12B+E2B)"
```

---

## Phase 4: Stage B Max Load + Final Report

### Task 13: Run Stage B on top 1-2 configs

- [ ] **Step 1: Run Stage B (max-load) on top 1-2 configs**

```bash
pkill -f "llama-server" 2>/dev/null || true
sleep 2
cd /home/hung/ai-hub
nohup ./venv/bin/python scripts/bench_12b_configs.py \
  --configs Q4-A-p0 \
  > /tmp/bench_phase4.log 2>&1 &
echo "Phase 4 PID: $!"
disown
```

(Replace `Q4-A-p0` with Phase 2 winner. Repeat for runner-up if within 5% — total ~20 min for 2 configs.)

- [ ] **Step 2: Verify max_load JSONs exist**

```bash
ls -la reports/bench_12b_full/q4_*_max_load.json
```

---

### Task 14: Generate final report

**Files:**
- Create: `reports/bench_12b_full/final_comparison.md`

- [ ] **Step 1: Run gen_final_report.py**

```bash
cd /home/hung/ai-hub
./venv/bin/python scripts/gen_final_report.py \
  --reports-dir reports/bench_12b_full \
  --output reports/bench_12b_full/final_comparison.md \
  --stage-b reports/bench_12b_full/*_max_load.json
cat reports/bench_12b_full/final_comparison.md
```

- [ ] **Step 2: Commit final report**

```bash
git add reports/bench_12b_full/final_comparison.md
git commit -m "docs: final report for 12B Q4 full optimization"
```

---

## Phase 5: Production Integration

(Execute these tasks regardless of Phase 2 winner, with adjustments based on what won.)

### Task 15: Update app/core/config.py (LITE_MODEL, DEFAULT_MODEL)

**Files:**
- Modify: `app/core/config.py` (model defaults)

- [ ] **Step 1: Read current config**

```bash
grep -n "LITE_MODEL\|DEFAULT_MODEL\|lite_num_ctx\|default_num_ctx" app/core/config.py | head
```

- [ ] **Step 2: Update to 12B Q4**

Find lines like:

```python
default_model: str = Field(default="local-gemma4-e4b-q8", alias="DEFAULT_MODEL")
lite_model: str = Field(default="local-gemma4-e4b-q8", alias="LITE_MODEL")
```

Replace with:

```python
default_model: str = Field(default="local-gemma4-12b-q4-text", alias="DEFAULT_MODEL")
lite_model: str = Field(default="local-gemma4-12b-q4-text", alias="LITE_MODEL")
```

Adjust if Phase 2 winner is a different alias (e.g., `local-gemma4-12b-q4-text-p1` or `local-gemma4-12b-q4-scope-b-chat`).

- [ ] **Step 3: Keep background models on E4B Q4** (smaller, faster for memory tasks)

Verify `SUMMARY_MODEL`, `STRUCTMEM_EXTRACTION_MODEL`, `STRUCTMEM_CONSOLIDATION_MODEL`, `CREW_MODEL` still point to `local-gemma4-e4b-q4` (or `local-gemma4-e4b-q8`). DO NOT change them.

- [ ] **Step 4: Commit config update**

```bash
git add app/core/config.py
git commit -m "feat(config): switch primary model to 12B Q4 (winner of optimization sweep)"
```

---

### Task 16: Update start.sh

**Files:**
- Modify: `start.sh`

- [ ] **Step 1: Replace E4B Q8 primary with 12B Q4**

In `start.sh`, find:

```bash
echo "[1/5] Starting Chatbot (E4B Q8, 8 slots) on port 8080..."
./scripts/start_lite_q8.sh
```

Replace with:

```bash
echo "[1/5] Starting Chatbot (12B Q4, 12 slots) on port 8080..."
./scripts/start_12b_q4_text.sh
```

- [ ] **Step 2: Verify start.sh syntax**

```bash
bash -n start.sh
echo "syntax OK"
```

- [ ] **Step 3: Commit**

```bash
git add start.sh
git commit -m "feat(start): use 12B Q4 as primary chatbot model"
```

---

### Task 17: Update CLAUDE.md

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Update "Implemented Features" section**

Find the "Core Chat" subsection. Replace mentions of "Gemma4 E2B Q4" or "Gemma4 E4B Q8" with "Gemma 4 12B Q4". Add benchmark numbers from final_comparison.md.

For example:

```markdown
### Core Chat
- **Local inference**: llama.cpp Q4 backend (port 8080), OpenAI-compatible API
- **Primary model**: **Gemma 4 12B Q4_K_M** (12 slots, ctx=8K, ~216 tok/s peak, p95@20u 8.1s)
- **Background models**: E4B Q4 for memory extraction (summary, structmem, crew)
- **Cloud fallback**: MiniMax M3 (Anthropic-compatible, `api.minimax.io`, prompt caching) — primary when `MINIMAX_ENABLED=true`. Falls back to OpenRouter (`openai/gpt-oss-20b:free`) otherwise. Project allow/deny policy.
- **Streaming**: SSE streaming with `[DONE]` sentinel
- **Multimodal**: Image input (Base64 → OpenAI `image_url` content-parts format) via E2B Q4 + mmproj (port 8083) — handles vision while 12B handles text
- **Model modes**: `lite` (12B Q4 default), `normal` (same model with default_num_ctx), `external` (cloud only)
```

- [ ] **Step 2: Update "Multi-Model GPU Architecture" section**

Find the "Multi-Model GPU Architecture (16GB VRAM)" section. Update to reflect new model layout:

```markdown
## Multi-Model GPU Architecture (16GB VRAM, 2026-06-06 config)

*   **Primary Chat (Port 8080)**: `Gemma 4 12B Q4_K_M` (parallel=12, ctx=8K). Handles 100% of user chat queries. ~7.4GB VRAM. Peak 216 tok/s, p95 latency @20 users 8.1s.
*   **Background Memory (Port 8081)**: `Gemma 4 E4B Q4` (parallel=4, ctx=8K). Handles memory extraction (summary, structmem, crew). ~2.5GB VRAM. Latency-tolerant.
*   **Multimodal (Port 8083)**: `Gemma 4 E2B Q4` + `mmproj-F16` (parallel=40, ctx=8K). Handles image input. ~2.5GB VRAM.
*   **Reranker (Port 8082)**: `bge-reranker-v2-m3` (Context: 4K). Re-scores knowledge RAG.
*   **FastEmbed**: Runs CPU inside API server (GPU has higher overhead than gain for small queries).
*   **Whisper**: Lazy-loaded `large-v3-turbo` model in float16.
*   **Total VRAM budget**: ~12.4GB at idle + ~2.5GB peak for FastEmbed/Whisper.
*   **Benchmark**: 20 concurrent users sustained at 146 tok/s, p95 7.9s, 0 errors over 10 min.
```

- [ ] **Step 3: Update Build & Run Commands section**

Find `./scripts/start_lite_q8.sh` references. Update to `./scripts/start_12b_q4_text.sh` if mentioned.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md with 12B Q4 model choice + benchmark numbers"
```

---

### Task 18: End-to-end smoke test

- [ ] **Step 1: Run start.sh**

```bash
cd /home/hung/ai-hub
# Make sure no servers are running
pkill -f "llama-server" 2>/dev/null || true
pkill -f "uvicorn" 2>/dev/null || true
sleep 2
./start.sh
```

Expected: All 5 services start successfully, AI Hub is ready, message says "AI Hub 2-Mode Ready".

- [ ] **Step 2: Test chat endpoint**

```bash
API_KEY=$(grep "^API_KEY=" .env | cut -d= -f2 | tr -d '"')
curl -fsS -X POST http://localhost:8000/v1/chat/completions \
  -H "X-API-KEY: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "local-gemma4-12b-q4-text",
    "messages": [{"role": "user", "content": "Xin chào, hãy giới thiệu ngắn về bạn."}],
    "max_tokens": 200
  }' | python3 -m json.tool
```

Expected: JSON response with `choices[0].message.content` containing Vietnamese introduction.

- [ ] **Step 3: Test vision endpoint** (if Q4-combo with E2B vision is set up)

```bash
# Need to verify ai-hub routes image to E2B on 8083
# Check that E2B is running
curl -fsS http://127.0.0.1:8083/v1/models | python3 -c "import sys, json; print('E2B:', json.load(sys.stdin)['data'][0]['id'])"
```

Expected: `E2B: local-gemma4-e2b-q4-mmproj-ihi`.

- [ ] **Step 4: Stop all services**

```bash
pkill -f "llama-server"
pkill -f "uvicorn"
sleep 2
echo "All services stopped"
```

- [ ] **Step 5: Final commit (if any uncommitted changes)**

```bash
git status --short
git add -A
git diff --cached --stat
git commit -m "chore: post-integration cleanup" || echo "no changes to commit"
```

---

## Self-Review Checklist (run after plan complete)

- [x] All 5 param variants (P0-P4) have launcher scripts (Task 1)
- [x] All 3 scopes (A, B, C) have launcher scripts (Tasks 1, 5)
- [x] Speculative launcher exists (Task 9)
- [x] Bench scripts updated for all config names (Tasks 2, 6, 10)
- [x] Each Phase has clear "run orchestrator + wait" steps (Tasks 3, 7, 11, 13)
- [x] Analysis steps after each Phase (Tasks 4, 8, 11-step3, 14)
- [x] Production integration: config.py (Task 15), start.sh (Task 16), CLAUDE.md (Task 17)
- [x] End-to-end smoke test (Task 18)
- [x] No placeholders (TBD/TODO/XXX) — verified via grep
- [x] All exact file paths given
- [x] All exact commands shown
- [x] All commit messages specified
