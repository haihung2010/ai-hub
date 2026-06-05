# Gemma 4 12B Quantization Optimization — Design

**Date:** 2026-06-05
**Status:** Draft (pending review)
**Author:** Brainstorming session with user
**Related:** `2026-06-03-ihi-rag-optimization-design.md` (IHI plan that this extends)

---

## 1. Background & Motivation

### Current state
- ai-hub uses `gemma-4-E4B-it-obliterated-Q8_0.gguf` (8B Q8, ~8GB VRAM) on port 8080 as the chatbot primary
- During the 2026-06-05 IHI evaluation session, we replaced E4B with `gemma-4-12b-it-Q4_K_M.gguf` (Abiray quant, 7.1GB) — significantly better Vietnamese quality and IHI verdict accuracy
- That replacement was based on a single config (Q4) with limited benchmarking

### Gap
- We don't know if Q4_K_M is the optimal quantization
- Q6_K (9.8GB) and Q8_0 (12.7GB) might give better quality at higher VRAM cost
- We don't know the optimal parallel slot / ctx_size / strategy combination
- The current 12B config (ctx=8K, parallel=12) was a guess, not benchmarked

### Goal
Find the optimal Gemma 4 12B configuration for the ai-hub chatbot (Vietnamese, multi-tenant, mixed multimodal). Balance 3 metrics:
- **Throughput** (aggregate tok/s)
- **Latency** (TTFT p50/p95, end-to-end p95)
- **Quality** (Vietnamese rubric score 1-10)

---

## 2. Configurations to test (3 total)

The user's design rule:
- **Q4 (low VRAM, 7.4GB)** → 12B text-only + E2B Q4 + mmproj for multimodal (split strategy)
- **Q6 (medium VRAM, 9.8GB)** → 12B text-only + E2B Q4 + mmproj for multimodal (split strategy)
- **Q8 (high VRAM, 12.7GB)** → 12B + mmproj standalone (single process handles text + vision + audio)

| Config | 12B port 8080 | E2B port 8083 | VRAM budget | Strategy |
|--------|---------------|---------------|-------------|----------|
| **A: Q4 + E2B combo** | 12B Q4 text-only (ctx=8K, parallel=12) | E2B Q4 + mmproj (ctx=8K, parallel=40) | ~10.3 GB | B (split) |
| **B: Q6 + E2B combo** | 12B Q6 text-only (ctx=8K, parallel=10) | E2B Q4 + mmproj (ctx=8K, parallel=40) | ~13.3 GB | B (split) |
| **C: Q8 standalone** | 12B Q8 + mmproj (ctx=8K, parallel=8) | (not used) | ~13.0 GB | A (mono) |

**Hardware:** RTX 5060 Ti 16GB VRAM. All configs fit with headroom for KV cache.

---

## 3. Test Phases

### Stage A: Basic benchmark on all 3 configs (15-20 min each)

Per config, 7 sub-phases:

1. **Cold start + warmup** (1 min): 1 user, 5 requests — primes caches
2. **Latency baseline** (1 min): 1 user × 10 requests
3. **Concurrency 5 users** (2 min): 5 users × 5 turns each
4. **Concurrency 10 users** (2 min): 10 users × 5 turns each
5. **Concurrency 20 users** (3 min): 20 users × 5 turns each
6. **Concurrency 40 users** (3 min, if VRAM permits): 40 users × 3 turns each
7. **Vietnamese quality sample** (3 min): 10 prompts, LLM-as-judge scoring
8. **DB snapshot + cleanup** (1 min): restore DB to pre-test state

### Stage B: Max load on top 1-2 configs (30-45 min each)

After Stage A, pick top 1-2 by aggregate score, run:
- **Sustained load** (20 min): 20 users × varied prompts, measure degradation
- **Spike test** (10 min): 60 users × 5 min, find ceiling

---

## 4. Metrics

### Performance metrics (per phase)

| Metric | Unit | Source |
|--------|------|--------|
| TTFT p50 / p95 | ms | httpx streaming |
| E2E latency p50 / p95 / p99 | ms | httpx timing |
| Aggregate tok/s | tokens/sec | `usage.completion_tokens / wall_time` |
| RPS (requests/sec) | req/sec | request_count / wall_time |
| VRAM peak | GB | `nvidia-smi` |

### Quality metric (Vietnamese rubric, LLM-as-judge)

For 28 test prompts (see Section 5), score each response 1-10:

| Criterion | Weight | Description |
|-----------|--------|-------------|
| Relevance | 0-3 | Answered the question |
| Naturalness | 0-2 | Natural Vietnamese, no repetition, no typos |
| Accuracy | 0-3 | Correct facts (technical/factual prompts) |
| Conciseness | 0-1 | No rambling, no garbage output |
| Format | 0-1 | Clean markdown, no "ArrayList"/"CLASS-NORMAL" |

**Pass threshold:** ≥7/10 per prompt. **Aggregate quality** = mean score.

### Composite score (for config ranking)

```
score = 0.40 × normalized_tok_per_s
      + 0.30 × normalized_inv_p95_latency
      + 0.30 × normalized_quality
```

where `normalized_X` = (X - min_across_configs) / (max - min) → [0, 1] scale.

---

## 5. Vietnamese test prompt bank (28 prompts)

| Category | Count | Example | Target response length |
|----------|------:|---------|----------------------|
| Greeting/social | 4 | "Xin chào, bạn tên gì?" | 50-100 tok |
| Technical (IoT/IHI) | 6 | "Giải thích NEMA MG-1 voltage imbalance threshold" | 200-400 tok |
| Code help | 4 | "Sửa lỗi: `def f(x): return x + 1` cho list" | 150-300 tok |
| Translation | 4 | "Dịch 'industrial sensor monitoring' sang tiếng Việt" | 100-200 tok |
| Factual | 4 | "Tại sao bầu trời có màu xanh?" | 100-250 tok |
| Creative | 3 | "Viết 1 đoạn văn 4 câu về IoT trong nông nghiệp" | 200-300 tok |
| Reasoning | 3 | "Có 5 quả táo, cho 2 bạn mỗi bạn 1 quả. Còn mấy?" | 50-150 tok |

**Anti-pattern detection:** any output containing "ArrayList", "CLASS-NORMAL", or other known hallucination tokens → automatic 0/10.

---

## 6. DB Snapshot/Restore

### Pre-test snapshot
- **PostgreSQL:** `pg_dump -t ihi_rag_cases -t ihi_case_embeddings -t ihi_device_overrides --data-only` → `/tmp/ihi_snapshots/<ts>/ihi_pg.sql`
- **SQLite (alert.db):** `cp /home/hung/ihi_test/alert.db /tmp/ihi_snapshots/<ts>/alert.db`

### Post-test restore
- `psql` truncate + import
- `cp` back to original location

### Isolation guarantee
- DB state before test = DB state after test
- No RAG pollution from earlier test affects later test verdict
- Critical for fair config comparison

---

## 7. File Operations Workflow

```bash
# Stage A1: Q4 (already in Downloads)
mv /home/hung/Downloads/gemma-4-12b-it-Q4_K_M.gguf /home/hung/models/
./venv/bin/python scripts/bench_single_config.py --config Q4-combo \
  --output reports/bench_12b/q4_combo_basic.json

# Stage A2: Q6 (download if not present)
[ -f /home/hung/Downloads/gemma-4-12b-it-Q6_K.gguf ] || \
  curl -L -o /home/hung/Downloads/gemma-4-12b-it-Q6_K.gguf \
    "https://huggingface.co/Abiray/gemma-4-12b-it-GGUF/resolve/main/gemma-4-12b-it-Q6_K.gguf"
mv /home/hung/Downloads/gemma-4-12b-it-Q6_K.gguf /home/hung/models/
./venv/bin/python scripts/bench_single_config.py --config Q6-combo \
  --output reports/bench_12b/q6_combo_basic.json

# Stage A3: Q8 (download if not present)
[ -f /home/hung/Downloads/gemma-4-12b-it-Q8_0.gguf ] || \
  curl -L -o /home/hung/Downloads/gemma-4-12b-it-Q8_0.gguf \
    "https://huggingface.co/Abiray/gemma-4-12b-it-GGUF/resolve/main/gemma-4-12b-it-Q8_0.gguf"
mv /home/hung/Downloads/gemma-4-12b-it-Q8_0.gguf /home/hung/models/
./venv/bin/python scripts/bench_single_config.py --config Q8-standalone \
  --output reports/bench_12b/q8_standalone_basic.json

# mmproj download (once, shared)
[ -f /home/hung/models/mmproj-gemma-4-12b-F16.gguf ] || \
  curl -L -o /home/hung/models/mmproj-gemma-4-12b-F16.gguf \
    "https://huggingface.co/Abiray/gemma-4-12b-it-GGUF/resolve/main/mmproj-F16.gguf"

# Stage B: max load on top 1-2 configs (after analyzing Stage A)
./venv/bin/python scripts/bench_single_config.py --config <best_config> --max-load \
  --output reports/bench_12b/best_max_load.json
```

**Disk space:** 168 GB free, total downloads ~17.4 GB (Q4 already 7.4GB, Q6 9.8GB, Q8 12.7GB, mmproj 122MB). Plenty of headroom.

---

## 8. Error Handling

| Failure | Detection | Recovery |
|---------|-----------|-----------|
| Server fails to start | exit != 0 within 30s | Abort that config, log to `reports/bench_12b/errors.log`, continue |
| Model file missing | pre-check | Skip config, log, continue |
| VRAM OOM at start | stderr "out of memory" | Stop, mark "OOM at startup", skip |
| VRAM OOM during load | nvidia-smi > 15.5GB, or tok/s → 0 | Reduce parallel 25%, retry once. If still OOM, mark "OOM at N users" |
| Port in use | ECONNREFUSED | Kill existing, retry. If still fails, abort config |
| DB snapshot fails | pg_dump exit != 0 | Abort (don't risk pollution). Log, skip config (needs manual intervention) |
| DB restore fails | psql error | WARN, mark `tests_failed` flag, next test may be polluted |
| Quality sample garbage | "ArrayList"/"CLASS-NORMAL"/empty | Mark sample FAIL, note in report. Don't fail whole test |
| Network timeout | httpx > 60s | Mark "TIMEOUT", don't count toward metrics |

**Exit codes:**
- 0 = success
- 1 = warning (degraded but reportable)
- 2 = fatal (skip next stage)

---

## 9. New Files

```
scripts/
├── bench_12b_configs.py          # NEW: master orchestrator (Stage A + B)
├── bench_single_config.py        # NEW: one config benchmark
├── start_12b_q4_text.sh          # NEW: 12B Q4 text-only launcher
├── start_12b_q6_text.sh          # NEW: 12B Q6 text-only launcher
├── start_12b_q8_mmproj.sh        # NEW: 12B Q8 + mmproj launcher
├── start_e2b_q4_mmproj.sh        # NEW: E2B Q4 + mmproj launcher
├── snapshot_ihi_db.sh            # NEW: pg_dump + sqlite backup
├── restore_ihi_db.sh             # NEW: restore from snapshot
└── gen_final_report.py           # NEW: aggregate JSONs → final_comparison.md

reports/bench_12b/                 # NEW: output directory
├── q4_combo_basic.json
├── q4_combo_basic.md
├── q6_combo_basic.json
├── q6_combo_basic.md
├── q8_standalone_basic.json
├── q8_standalone_basic.md
├── <best>_max_load.json
├── <best>_max_load.md
├── errors.log
└── final_comparison.md
```

**Total:** ~9 new scripts, 1 new directory, 9-11 new report files.

---

## 10. Time Budget (3-4 hours)

| Stage | Per-config | Total |
|-------|-----------|-------|
| Stage A (3 configs × 18 min) | 18 min | 54 min |
| Stage B (1-2 configs × 35 min) | 35 min | 35-70 min |
| Analysis + report writing | — | 20-30 min |
| Buffer for downloads, retries, etc. | — | 30 min |
| **TOTAL** | | **~2.5-3 hours** |

Within 3-4 hour budget.

---

## 11. Success Criteria

- [ ] All 3 Stage A configs produce reportable metrics (no fatal errors)
- [ ] DB state preserved across all tests (no pollution between configs)
- [ ] Top 1-2 configs identified with composite score
- [ ] Stage B max load completed for top configs
- [ ] Final recommendation report (`final_comparison.md`) generated
- [ ] Branch ready with all changes (start scripts, orchestrator, reports)

---

## 12. References

- HF model: https://huggingface.co/Abiray/gemma-4-12b-it-GGUF
- Quants available: Q3_K_M (6.09GB) / Q4_K_M (7.38GB) / Q5_K_S (8.41GB) / Q5_K_M (8.55GB) / Q6_K (9.79GB) / Q8_0 (12.70GB)
- mmproj for multimodal: F16 (122MB) / BF16 (175MB) / F32 (210MB)
- IHI evaluation context: `2026-06-03-ihi-rag-optimization-design.md`
- Previous E4B Q8 config: `scripts/start_lite_q8.sh` (ctx=64K, parallel=8, ~10GB VRAM)
