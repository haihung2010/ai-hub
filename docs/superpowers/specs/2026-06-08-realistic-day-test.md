# Realistic-Day Test — Design Spec
**Date:** 2026-06-08  
**Status:** Approved (in-session 2026-06-08)  
**Goal:** Measure 3 things under realistic workday traffic on /home/hung/ai-hub

## 1. Background

Yesterday's 5K-request test showed:
- 95% VRAM sustained (15.5/16 GB)
- p95 latency 44s (queue-bound, not memory-bound)
- 0% 12B Q4 usage (static BRANE classifier routed everything to E2B-bg/E4B)

Adaptive routing shipped 2026-06-07 fixed routing. Now we need a **realistic-day test** to validate:
1. Sustained load capacity with adaptive routing on
2. Memory summary quality after 1h+ gap
3. Learning curve — repeated-topic traffic should get faster across cycles

User constraint: **must feel realistic**, not synthetic stress. 6-hour total duration, can stop anytime.

## 2. Goal & non-goals

**Measure:**
- Max users the system holds with p95 ≤ 25s
- Memory recall accuracy per cycle
- Latency delta between first-time and Nth-time for same intent

**Out of scope:**
- Comparing models head-to-head
- Cloud-fallback stress (only test local)
- Search/MCP tool reliability (use `enable_search=False`)

## 3. Cycle model

- Total: 6 cycles, 60 min each = 6 hours
- Per cycle, 3 phases:
  - **Active phase (50 min)**: N user personas, each up to 10 requests sequentially, 3-8s random gap
  - **Memory verification (5 min)**: for each prior-cycle user, ask recap question derived from their summary; score recall
  - **Learning probe (5 min)**: for each prior-cycle intent, ask a paraphrased version; compare latency vs first occurrence

## 4. Adaptive scaler

Every 60s reads `/v1/admin/queue` and rolling p95 from `/v1/admin/usage`:

| Condition | Action |
|---|---|
| queue > 8 OR p95 > 25s | concurrency -= 20% (min 1) |
| queue < 3 AND p95 < 15s AND VRAM < 14.5 GB | concurrency += 20% (max 200) |
| VRAM > 15.4 GB sustained | concurrency -= 30% immediately |

N starts at 20, max 200. Adaptive loop writes to `adaptive_scaler.log` with timestamp + decision.

## 5. Question generation

Use `/v1/chat` model_mode=lite to generate batches. Topic pool:
- `fanpage_consulting`, `fanpage_buy_sell`, `fanpage_product_info`
- `fanpage_complaint`, `fanpage_promo`
- `ihi_safety_query`, `iot_dashboard`, `legal_qa`

Per cycle: 1-2 topics picked; 1 question batch per user (10 questions); recap+paraphrase generated on demand.

**Fallback:** seed questions hardcoded per topic, used when /v1/chat fails (timeout/error). This keeps test running even if local LLM has an outage.

## 6. IHI pulse loop (parallel, independent)

Every 30 minutes, on minute 15 and 30 of each cycle:
- GET `/v1/ihi/cycles?limit=1` 
- Log scrape_id, started_at, finished_at, status, rows_added, phases[].verdict_text
- Write to `ihi_pulses.jsonl`

This validates the iHi sensor scheduler is alive independently of the main chat load.

## 7. Termination (any one stops)

1. 6 hours elapsed
2. `reports/realistic-day-2026-06-08/stop_signal.txt` exists
3. 5 consecutive cycles p95 > 60s
4. VRAM > 16 GB sustained 2 min
5. uvicorn crash 3 times

## 8. State & reporting

```
/home/hung/ai-hub/reports/realistic-day-2026-06-08/
├── cycles/
│   ├── cycle_00.jsonl           # one line per request
│   ├── cycle_01.jsonl
│   ├── ...
├── cycle_summaries.jsonl        # one summary line per cycle
├── memory_recall.jsonl          # one line per (cycle, user) recall check
├── learning_curve.jsonl         # one line per (cycle, intent) probe
├── ihi_pulses.jsonl             # one line per 30-min pulse
├── adaptive_scaler.log          # human-readable scaler decisions
├── stop_signal.txt              # touch this to stop
├── pid                          # runner pid
└── SUMMARY.md                   # final report
```

## 9. Per-request JSONL line

```json
{
  "ts": "2026-06-08T09:01:23+07:00",
  "cycle": 0,
  "phase": "active|memory_verify|learning_probe",
  "user": "simu_00_user_03",
  "topic": "fanpage_consulting",
  "request_idx": 3,
  "intent_id": "fanpage_consulting_q03",
  "latency_ms": 12500,
  "status": 200,
  "tokens_in": 412,
  "tokens_out": 188,
  "model": "local-gemma4-12b-q4-text",
  "content_preview": "...",
  "summary_seen": ["tư vấn", "sản phẩm A"]
}
```

## 10. Components

- `scripts/realistic_day.py` — main runner (this file's reference impl)
- `scripts/realistic_day_generator.py` — LLM-driven question generator
- `scripts/realistic_day_reporter.py` — per-cycle summary, recall scorer, learning delta
- `scripts/realistic_day_state.py` — JSONL persistence + reload
- `scripts/realistic_day_scaler.py` — adaptive concurrency controller
- `scripts/rday_status.sh` — quick status (terminal shortcut for telegram)
- `scripts/mmx_quota.sh` — manual MiniMax quota check (no auto)

## 11. Telegram integration

User explicitly wants manual control, not auto. He will use:
- `/rday` → calls `rday_status.sh` (returns latest cycle summary + scaler state)
- `/mmx` → calls `mmx_quota.sh` (returns MiniMax account usage)

Both scripts read state files and print concise status. No new gateway code needed.

## 12. Testing

Smoke test: 1 cycle, 3 users, 10 req each + memory verify + learning probe (~15 min). Verify all JSONL files written, no crash.

Production: 6h as above, monitor via /rday every cycle, send Telegram report at 1h15 with first memory quality assessment.

## 13. References

- /home/hung/ai-hub/docs/superpowers/specs/2026-06-07-adaptive-routing.md
- /home/hung/ai-hub/scripts/loadtest.py (existing patterns)
- /home/hung/ai-hub/app/routes/ihi.py:666 (GET /v1/ihi/cycles)
- /home/hung/ai-hub/SYSTEM_SNAPSHOT_2026-06-07.md (current stack config)
