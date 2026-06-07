# AI Hub System Snapshot — 2026-06-07 (end of session)

**Main branch:** `ed2a740 fix(tests): use exact DB-name match instead of substring in DSN guard`
**Last 9 commits on main (today's merges):**

```
ed2a740 fix(tests): use exact DB-name match instead of substring in DSN guard
4b6d5ea Merge feat/rag-scrape — 112 RAG cards via MiniMax MCP (Rank 6)
d2de976 Merge ship/inflight-concerns — Langfuse + Contextual Retrieval + PinnedMemory + admin UI
a8cb4da Merge fix/rank3-rank8 — E2B-bg PARALLEL 8→16 (Rank 3)
a0caab6 Merge fix/rank5-risk-action-gap — /v1/admin/risk/gap endpoint (Rank 5)
6ece08e Merge fix/pg-audit-fallback — PG audit writers (Rank 7)
b798b7f Merge fix/summary-service — wire SummaryService (Rank 4)
d85c844 Merge fix/usage-events-writer — populate token/cost/api_key_id (Rank 1)
ad1af5d chore(pytest): register no_isolated_db marker
a1dff4c Merge fix/rank10-conftest-dsn-guard — prevent production DB wipe (Rank 10)
```

---

## Current production config (4 servers + 1 API)

| Port | Model | Parallel | Ctx | Cache | Alias | Launcher |
|---|---|---|---|---|---|---|
| 8000 | (uvicorn ai-hub) | — | — | — | — | `start.sh` |
| 8080 | 12B Q4_K_M (primary) | **8** | **24576** | q4_0 | `local-gemma4-12b-q4-text` | `scripts/start_lite_q8.sh` |
| 8081 | E2B Q4 + mmproj (background) | **16** ⬆️ | 32768 | q8_0 | `local-gemma4-e2b-q4-bg` | `scripts/start_background_q4.sh` |
| 8082 | bge-reranker-v2-m3 | 1 | 4096 | n/a | `bge-reranker-v2-m3` | `scripts/start_reranker.sh` |
| 8083 | E2B Q4 (iHi sensor) | 40 | 8192 | q8_0 | `local-gemma4-e2b-q4-ihi` | `scripts/start_ihi_sensor.sh` |

**VRAM:** ~15.5–15.8 / 16 GB (**97–99% — DANGEROUSLY tight**)

| Process | VRAM |
|---|---|
| 12B Q4 (8080) | ~8.9 GB |
| E2B bg + mmproj (8081) | ~3.5 GB |
| E2B iHi (8083) | ~2.2 GB |
| Reranker (8082) | ~0.5 GB |
| **Total** | **~15.1 GB** |

---

## Multi-user load test (2026-06-07 09:58–10:13)

| Concurrency | Duration | Requests | RPM | p50 | p95 | Errors | VRAM peak |
|---|---|---|---|---|---|---|---|
| 4 | ~6 min | 184 | 21.6 | 9.6s | **25.1s** | 0 | 15580 MiB |
| 8 | ~4.5 min | 124 | 27.7 | 15.3s | **38.2s** | 0 | 15646 MiB |

**Verdict: NO OOM at 8 concurrent.** p95 latency is the real problem (queue-bound, not memory-bound).

---

## Endpoints

| Endpoint | Status | Notes |
|---|---|---|
| `GET /health` | ✅ | Returns `{"status":"ok","local":{"models":["local-gemma4-12b-q4-text"]}}` |
| `POST /v1/chat` | ✅ | Live tested: 107/31/138 token usage verified (Rank 1 fix) |
| `GET /v1/admin/queue` | ✅ | `{capacity:16, active:0, waiting:0}` |
| **`GET /v1/admin/risk/gap`** | ✅ NEW (Rank 5) | Returns mode + summary + per-action breakdown |
| `GET /v1/admin/usage` | ✅ | Token fields populated (Rank 1 fix live) |
| `POST /v1/admin/keys` | ✅ | Tested (mint + PG persist works) |
| `GET /v1/knowledge/search` | ✅ | RAG KB has 112 cards (Rank 6) |

---

## How to RESTART (sau khi tắt máy / reboot)

```bash
cd /home/hung/ai-hub

# 1. Verify PG + Redis up
pg_isready -h localhost && redis-cli ping

# 2. Verify .env keys (MiniMax MCP required for /search)
grep -E "^(MINIMAX_API_KEY|MINIMAX_ENABLED|MINIMAX_MCP_ENABLED|MINIMAX_BASE_URL|MINIMAX_MODEL)" .env

# 3. Kill any old processes
pkill -f llama-server; pkill -f "uvicorn app.main"

# 4. Start stack
./start.sh
# Output ends with "=== AI Hub 2-Mode Ready ===" when all 5 services up
```

---

## Code state — 8 fix branches MERGED to main

| Branch | What |
|---|---|
| `fix/rank10-conftest-dsn-guard` | **Critical** — refuse TRUNCATE on prod DB (Rank 10) |
| `fix/usage-events-writer` | Populate `prompt_tokens/completion_tokens/cost_usd` (Rank 1) |
| `fix/summary-service` | Removed early return that prevented SummaryService from firing (Rank 4) |
| `fix/pg-audit-fallback` | New `security_audit.py` ThreadPoolExecutor writer for `auth_failures` + `rate_limit_buckets` (Rank 7) |
| `fix/rank5-risk-action-gap` | New `/v1/admin/risk/gap` endpoint + startup log (Rank 5) |
| `fix/rank3-rank8` | `start_background_q4.sh`: PARALLEL 8→16 (Rank 3) |
| `feat/rag-scrape` | 112 RAG knowledge cards ingested (53 IHI + 59 vi-fanpage, Rank 6) |
| `ship/inflight-concerns` | 4 features: Langfuse tracing + Contextual Retrieval + PinnedMemory + admin UI redesign |

---

## Test DB

- **`ai_hub_test`** created 2026-06-07, schema initialized (20 tables)
- Missing `pgvector` extension (needs superuser to install)
- Run: `AI_HUB_ALLOW_DB_TRUNCATE_FOR_TESTS=1 DATABASE_URL=postgresql://aihub:aihub_pass@localhost:5432/ai_hub_test ./venv/bin/pytest tests/unit/`
- Result: 181/200 pass, 19 fail (3 pgvector, 16 pre-existing)

---

## Untracked / cleanup

- 30+ untracked files in main checkout (evals/, research/, scripts/, static/ pre-existing)
- All stashes dropped (content now in main via merges)
- 12 worktrees removed; remaining: main + `ihi-rag-audit` (kept for record) + 1 auto harness worktree
- Uncommitted modification to `app/services/ai_service.py` and `tests/integration/test_usage_tracking.py` from the in-flight work — now superseded by merged code

---

## Known follow-ups

1. **VRAM dangerously tight** (97-99%). No OOM at 8 concurrent but only 10 MiB headroom. Consider reducing ctx 24576→8192 for safety.
2. **pgvector** not installed in `ai_hub_test` (needs superuser). 3 contextual retrieval tests fail because of this.
3. **16 pre-existing test failures** unrelated to today's merges.
4. **Rank 9 retention** was explained as Rank 10's data-wipe race — no separate fix needed.
5. **Rank 2 slot saturation** dispelled by Agent H's bench (HOLD on launcher changes).

---

## Useful commands

```bash
# Watch VRAM
watch -n 5 nvidia-smi --query-gpu=memory.used,memory.free --format=csv

# Live usage events (post-Rank 1 fix)
PGPASSWORD=aihub_pass psql -U aihub -d ai_hub -h localhost -c "SELECT prompt_tokens, completion_tokens, total_tokens, cost_usd, model, created_at FROM usage_events ORDER BY created_at DESC LIMIT 10;"

# Check failure_risk gap (Rank 5 fix)
curl -H "X-API-KEY: $(grep ^API_KEY= .env | cut -d= -f2 | tr -d '\"')" http://localhost:8000/v1/admin/risk/gap | jq .

# Tail llama-server logs
tail -f /tmp/aihub-llama-lite-q8.log /tmp/aihub-llama-background.log /tmp/aihub-llama-ihi.log

# Quick load test
./venv/bin/python scripts/loadtest.py 2 4   # 2 min, 4 concurrent
```
