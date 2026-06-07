# 12B Q4 Full Optimization — Design

**Date:** 2026-06-06
**Status:** Approved
**Author:** Brainstorming session with user
**Related:** `2026-06-05-12b-quantization-optimization-design.md` (sweep that picked Q4-combo)

---

## 1. Background & Motivation

Stage A sweep (2026-06-05) chốt **Q4-combo thắng** với composite 0.877 (peak 216 tok/s, p95@20u 8.1s, quality 7.7/10). Bây giờ cần **maximize performance** của config này trong production context (chạy song song với E2B vision + background memory tasks + reranker + FastEmbed + Whisper trên 16GB GPU).

Câu hỏi trung tâm: **Trong 16GB VRAM constraint, allocation thế nào giữa primary chat, background memory, vision là tối ưu nhất?** Kèm theo: **llama.cpp param nào cho peak tok/s**, và **speculative decoding với E2B draft có speedup thực không**?

---

## 2. Architecture: 4-Phase Sequential Test

```
Phase 1: Param sweep on Scope A (5 param configs × ~15 min = ~75 min)
  → Pick top 2 param configs
Phase 2: Best 2 param configs × 3 Scopes (6 runs × ~15 min = ~90 min)
  → Pick top 2 (param + scope) combos
Phase 3: Speculative decoding on top combo (with/without E2B draft = 2 runs × ~15 min = ~30 min)
  → Decide wire-in based on speedup
Phase 4: Stage B max load on top 1-2 configs (2 × 10 min = ~20 min)
  → Final ranking + recommendation
Total: ~3.5 hours
```

---

## 3. The 3 Scopes (production deployment options)

| Scope | Port 8080 (primary chat) | Port 8081 (background memory) | Port 8083 (vision) | Notes |
|-------|--------------------------|-------------------------------|---------------------|-------|
| **A: Selective 12B** | 12B Q4 text (parallel=12) | E4B Q4 (parallel=4) | E2B Q4 + mmproj | Hiện tại best-known từ bench. Background xài model nhỏ vì latency-tolerant. |
| **B: 12B everywhere** | 12B Q4 text (parallel=10) | 12B Q4 text (parallel=4) | E2B Q4 + mmproj | Quality cao nhất cho memory extraction. Nhưng 2x 12B load cùng GPU → giảm primary throughput. |
| **C: Single model** | 12B Q4 text (parallel=12) | (none — memory tasks route to 8080) | E2B Q4 + mmproj | Đơn giản nhất. Memory tasks steal slots từ primary users → có thể giảm UX. |

Hypothesis: **Scope A thắng** vì background tasks (summary every 20 msg, structmem every 5) latency-tolerant và không cần 12B quality. Nhưng verify bằng data.

---

## 4. Param Sweep Matrix (Phase 1, applied to Scope A)

| ID | parallel | ctx-size | cache-type-k/v | mlock | n-gpu-layers | flash-attn | Motivation |
|----|----------|----------|----------------|-------|--------------|------------|------------|
| **P0** | 12 | 8192 | q8_0 | off | 999 | on | **Baseline** (Q4-combo winner từ 2026-06-05) |
| **P1** | 16 | 8192 | q8_0 | off | 999 | on | Max parallel — push 16GB limit |
| **P2** | 12 | 4096 | q8_0 | off | 999 | on | Smaller ctx → less KV memory → more headroom |
| **P3** | 12 | 12288 | q8_0 | off | 999 | on | Bigger ctx cho long context (chat dài) |
| **P4** | 12 | 8192 | q4_0 | off | 999 | on | q4_0 KV cache giảm 50% memory so với q8_0 |

Sau Phase 1, pick **top 2 param configs** (by composite score) để đưa vào Phase 2.

---

## 5. Test Protocol (per config)

### 5.1 Bench phases (each ~15 min total)
1. **Warmup** (1 user, 5 prompts, wall=30s) — load model into VRAM cache
2. **Latency baseline** (1 user, 10 prompts, wall=60s) — single-user best case
3. **Concurrency 5** (5 users, 25 prompts, wall=60s) — light load
4. **Concurrency 10** (10 users, 50 prompts, wall=90s) — medium load
5. **Concurrency 20** (20 users, 60 prompts, wall=120s) — heavy load
6. **Concurrency 40** (40 users, 60 prompts, wall=120s) — stress test
7. **Vietnamese quality** (10 prompts × max_tokens=300) — heuristic scoring 1-10

### 5.2 Metrics per config
- **tok/s aggregate** (across all phases) — throughput
- **TTFT p50/p95 ms** — time to first token (latency)
- **E2E p50/p95 ms** — end-to-end latency
- **Peak tok/s** — best throughput across phases
- **p95 latency @20 users** — heavy-load latency
- **Quality 1-10** — Vietnamese rubric score
- **VRAM peak GB** — memory budget
- **Speculative only: accept_rate %**, **draft_speedup_ratio**

### 5.3 Composite score (same formula as 2026-06-05)
```
composite = 0.40 × norm_tok + 0.30 × (1 - norm_lat) + 0.30 × norm_quality
where each is normalized against max value across all configs in this run
```

---

## 6. DB Isolation (reused from 2026-06-05)

Same `snapshot_ihi_db.sh` + `restore_ihi_db.sh` — wrap mỗi config để RAG state không bị pollute giữa tests. Snapshot pre-test, restore post-test.

---

## 7. Speculative Decoding (Phase 3)

### 7.1 Theory
- Draft model: E2B Q4 (3x faster than 12B Q4)
- Target model: 12B Q4
- Accept rate: typical 50-70% cho well-matched draft
- Expected speedup: 1.5-2.5x cho single-user

### 7.2 Risk với multi-user
- llama-server's `--draft-model` runs in same process — speculative work competes với parallel slots
- Accept rate giảm khi concurrent users tăng (vì draft tokens shared)
- May show NEGATIVE speedup ở concurrency 20+

### 7.3 Decision criteria
- Phase 3 test trên best Scope + best param config
- Nếu tok/s @ concurrency 20 cải thiện ≥20% → wire vào start.sh
- Nếu <20% hoặc regress → skip, log lý do

### 7.4 Implementation (khi wire)
- New launcher: `start_12b_q4_spec.sh` with `--draft-model /home/hung/models/gemma-4-E2B-it-Q4_K_M.gguf --draft-max 8`
- start.sh: thêm 1 line launch draft llama-server (nếu cần separate) — actually llama.cpp supports draft on same process
- Test runtime: existing parallel slots unaffected by `--draft-max`

---

## 8. Production Integration (sau khi có winner)

### 8.1 Scope A winner (most likely)
- Update `app/core/config.py`:
  - `LITE_MODEL = "local-gemma4-12b-q4-text"` (was `local-gemma4-e4b-q8`)
  - `DEFAULT_MODEL = "local-gemma4-12b-q4-text"` (was `local-gemma4-e4b-q8`)
  - Keep `SUMMARY_MODEL = "local-gemma4-e4b-q4"` (background, smaller)
  - Keep `STRUCTMEM_*_MODEL = "local-gemma4-e4b-q4"`
  - Keep `CREW_MODEL = "local-gemma4-e4b-q4"`
- Update `start.sh`:
  - `[1/5] Starting Chatbot (12B Q4, 12 slots) on port 8080...` — call `start_12b_q4_text.sh`
  - `[2/5] Starting Background E4B Q4 on port 8081...` — keep as is
  - Other steps unchanged
- Update `scripts/start_lite_q8.sh` → deprecate, redirect to `start_12b_q4_text.sh`

### 8.2 Scope B/C winner (unlikely)
- Scope B: Update `SUMMARY_MODEL = "local-gemma4-12b-q4-text"`, keep separate background 12B launcher
- Scope C: Add `BACKGROUND_LLM_HOST = "http://127.0.0.1:8080"` to config, update memory services to use primary endpoint

### 8.3 Speculative wire-in
- New env var in `.env`: `SPECULATIVE_DRAFT_MODEL_PATH=...`
- New launcher `start_12b_q4_spec.sh` invoked from start.sh if env set
- Rollback: comment out the line, fallback to `start_12b_q4_text.sh`

### 8.4 Documentation
- Update `CLAUDE.md` to reflect:
  - Primary model: 12B Q4 (was E4B Q8)
  - Background: E4B Q4 (unchanged)
  - New benchmark results: peak tok/s, p95 latency, quality
  - Speculative (if wired): expected speedup

---

## 9. File Structure (new + modified)

### New files
```
scripts/
├── start_12b_q4_p1.sh           # parallel=16
├── start_12b_q4_p2.sh           # ctx=4K
├── start_12b_q4_p3.sh           # ctx=12K
├── start_12b_q4_p4.sh           # cache=q4_0
├── start_12b_q4_spec.sh         # with --draft-model E2B
├── start_12b_q4_scope_b.sh      # 12B on 8080 + 12B on 8081
└── start_12b_q4_scope_c.sh      # single 12B on 8080

reports/bench_12b_full/
├── phase1_param_sweep.json      # 5 param configs
├── phase1_param_sweep.md
├── phase2_scopes.json           # 2 best params × 3 scopes = 6 configs
├── phase2_scopes.md
├── phase3_speculative.json      # 2 configs (with/without)
├── phase3_speculative.md
├── phase4_max_load.json         # 1-2 configs
├── phase4_max_load.md
└── final_comparison.md          # recommendation + production integration
```

### Modified files
```
scripts/bench_12b_configs.py     # Add new config names
scripts/bench_single_config.py   # Add param/scope config map
app/core/config.py               # Update LITE_MODEL/DEFAULT_MODEL (if Scope A wins)
start.sh                         # Use new launcher
CLAUDE.md                        # Document new model choice
```

---

## 10. Risk & Mitigation

| Risk | Mitigation |
|------|------------|
| Phase 1 takes longer than 75 min (per-config > 15 min) | Reduce concurrency phases (drop conc_40 if slow) |
| Scope B/C runs into VRAM OOM | Pre-check VRAM before launch; auto-skip if < 2GB headroom |
| Speculative decoding makes things slower (multi-user) | Decision criteria: only wire if ≥20% improvement; else document "tested, not adopted" |
| Background tasks in Scope C pollute primary slots | Bench background tasks separately as `concurrency=1` injection during primary bench |
| Start.sh integration breaks existing | Test on bench infrastructure first; have rollback path: re-enable start_lite_q8.sh |

---

## 11. Time Budget

- Phase 1: ~75 min
- Phase 2: ~90 min
- Phase 3: ~30 min
- Phase 4: ~20 min
- Total: ~3.5 hours
- Buffer: 30 min for retries, OOM recovery, manual fixes

---

## 12. Success Criteria

- [ ] 5 param configs benchmarked in Phase 1 with composite scores
- [ ] Top 2 param configs × 3 scopes = 6 configs benchmarked in Phase 2
- [ ] Speculative decoding tested on best combo (with/without)
- [ ] Top 1-2 configs validated under Stage B max load (10 min sustained)
- [ ] Final report at `reports/bench_12b_full/final_comparison.md` with:
  - Recommended config
  - Production integration plan
  - Speculative wire-in (yes/no) with data
- [ ] start.sh + config.py updated if Scope A wins (likely)
- [ ] CLAUDE.md updated with new model choice + benchmark numbers
- [ ] DB isolation verified (no RAG pollution between configs)

---

## 13. References

- `2026-06-05-12b-quantization-optimization-design.md` — Q4-combo winner rationale
- `bench_12b_2026-06-06` memory — key finding: parallel slots > quantization quality
- llama.cpp server docs: https://github.com/ggml-org/llama.cpp/tree/master/examples/server
- Speculative decoding guide: https://github.com/ggml-org/llama.cpp/pull/8924
