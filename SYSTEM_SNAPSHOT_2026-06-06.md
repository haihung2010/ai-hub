# AI Hub System Snapshot — 2026-06-06 (end of session)

**Last commit on `feat/ihi-rag-optimization` branch:**

```
959881c fix(admin): auto-refresh dashboard by default (3s interval)
b116f66 fix(launcher): bump 12B Q4 ctx to 24576 (search context + history overflow)
b6ea52c fix(admin): default to Dashboard tab on F5 refresh
0332b05 fix(admin): forward API key to IHI dashboard links
0291e7f fix(admin): bump service worker cache to v4
...

Latest summary: 12B Q4 primary, E4B bg fast chat, E2B+mmproj iHi
```

---

## Current production config (4 servers + 1 API)

| Port | Model | Parallel | Ctx | Cache | Alias | Launcher |
|---|---|---|---|---|---|---|
| 8000 | (uvicorn ai-hub) | — | — | — | — | `./start.sh` |
| 8080 | 12B Q4_K_M (P4 winner) | 8 | 24576 | q4_0 | `local-gemma4-12b-q4-text` | `scripts/start_lite_q8.sh` |
| 8081 | E2B Q4_K_M (background) | 16 | 32768 | q8_0 | `local-gemma4-e2b-q4-bg` | `scripts/start_background_q4.sh` |
| 8082 | bge-reranker-v2-m3 (rerank) | 1 | 4096 | n/a | `bge-reranker-v2-m3` | `scripts/start_reranker.sh` |
| 8083 | E2B Q4 + mmproj (iHi) | 40 | 8192 | q8_0 | `local-gemma4-e2b-q4-ihi` | `scripts/start_ihi_sensor.sh` |

**VRAM:** ~15.7 / 16 GB (99% — chật nhưng ổn định)

---

## How to RESTART (sau khi tắt máy / reboot)

```bash
cd /home/hung/ai-hub

# 1. Start PostgreSQL + Redis (nếu dùng Docker)
#    hoặc khởi động bằng systemctl / brew services
#    pg_isready -h localhost  # confirm PG up
#    redis-cli ping           # confirm Redis up

# 2. Verify .env has all needed keys
grep -E "^(MINIMAX_API_KEY|MINIMAX_ENABLED|MINIMAX_MCP|MINIMAX_BASE_URL|MINIMAX_MODEL)" .env
# Should show: MINIMAX_ENABLED=true, MINIMAX_API_KEY=sk-cp-..., MINIMAX_MCP_ENABLED=true, etc.

# 3. Start ai-hub (orchestrates everything)
./start.sh

# That's it. start.sh launches all 4 llama-servers + uvicorn in order.
# Waits for each ready before next. Final output: "AI Hub 2-Mode Ready"
```

**Manual launch (if start.sh has issues):**
```bash
# Start servers in parallel
nohup bash scripts/start_lite_q8.sh > /tmp/aihub-12b.log 2>&1 &
nohup bash scripts/start_background_q4.sh > /tmp/aihub-bg.log 2>&1 &
nohup bash scripts/start_reranker.sh > /tmp/aihub-rerank.log 2>&1 &
nohup bash scripts/start_ihi_sensor.sh > /tmp/aihub-ihi.log 2>&1 &

# Start uvicorn LAST (after all 4 servers ready)
./venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
```

---

## API + Public Domain

**Local:** `http://127.0.0.1:8000`
**Public:** `https://api-aiserver.htechlabsvn.com`

**API key (master):** `1XteCCQ_s_UbrqOGEIYybmDBnokWhxYap90D6_Jojx8`
**Stored in:** `/home/hung/ai-hub/.env` as `API_KEY=...`

**UI pages (all on port 8000, served by ai-hub):**
- `/` — main chat UI (Vietnamese, with /search: prefix for MCP)
- `/admin.html` — admin OS (Dashboard, GPU, Keys, RAG, Tenants, iHi, etc.)
- `/ihi-charts-v2.html` — iHi sensor dashboard
- `/ihi-feed-v3.html` — iHi alert feed
- `/key.html` — API key management

---

## MCP integration (MiniMax WebSearch)

**State:** ENABLED + working end-to-end
- Package: `minimax-coding-plan-mcp` (via `uvx`)
- Spawned on ai-hub startup if `MINIMAX_ENABLED=true` AND `MINIMAX_API_KEY` set
- 4 subprocesses (parent uv wrapper + Python runtime) typically running
- Trigger: `/search:` prefix in user message (also works with `/search` no colon)
- Response includes `sources: [list of URLs]`

**Tested:** 9/9 MCP searches succeeded in last session
- ✅ `/search: thủ đô của Nhật Bản` → "Tokyo" + 5 sources (mia.vn, youtube, agoda)
- ✅ `/search hom nay la ngay bao nhieu` → lịch VN + 3 sources
- ✅ English queries work
- ✅ Multi-turn works

**If MCP needs to be re-installed (after uvx wipe):**
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
# verify
uvx --from minimax-coding-plan-mcp minimax-coding-plan-mcp --help
# Should print help text. If "Failed to perform search: 404", check
# MINIMAX_BASE_URL in .env (should be https://api.minimax.io WITHOUT /v1)
```

---

## Key files to preserve

```
app/core/config.py          — All settings (default_model, ALLOWED_ORIGINS, MINIMAX_MCP_*)
.env                        — Secrets: API_KEY, MINIMAX_API_KEY, DB credentials
scripts/start_lite_q8.sh    — 12B Q4 (P4) launcher
scripts/start_background_q4.sh — E2B Q4 bg launcher
scripts/start_ihi_sensor.sh — E2B+mmproj launcher
scripts/start_reranker.sh   — Reranker launcher
app/services/mcp/minimax_websearch.py — MCP client (16 passing tests)
static/admin.html           — Admin OS UI (Dashboard default, auto-refresh 3s)
static/admin-sw.js          — Service worker v6
```

---

## Benchmarks (this session, 2026-06-06)

### 12B Q4 vs E4B Q4 (10 users × 20 messages, direct to llama-server)

| Metric | **E4B Q4** (5.3GB) | **12B Q4** (7.4GB) |
|---|---|---|
| Throughput | **1.83 req/s** | 0.92 req/s |
| p50 latency | **5.6s** | 11.3s |
| p95 latency | **7.5s** | 15.2s |
| p99 latency | **8.5s** | 17.4s |
| Est. tok/s | **~24.8** | ~13.3 |
| Success rate | 100% | 100% |

**E4B 1.86× faster** but 12B has higher quality. Use E4B for fast_background (short replies), 12B for primary chat.

### Load tests (via ai-hub API, 60s timeout)

| Test | Total | Success | p50 | p95 | Note |
|---|---|---|---|---|---|
| 20×40 (12B+E2B only) | 800 | 95% | 21.6s | 28.9s | Long queue, ~5% timeout |
| 20×10 (4 servers) | 200 | 94% | 10.9s | 30.1s | iHi on |
| 20×20 (4 servers, heavy) | 400 | 90% | 15.7s | 26.6s | 10% timeout under stress |
| 20×20 (4 servers, café) | 400 | 100% | <3s | <10s | 1-2 users, OK |

---

## Open issues / known limitations

1. **VRAM 99%** — no headroom for heavy batch jobs. If OOM happens, kill E2B bg (8081) first, then iHi (8083).
2. **iHi server evaluate has 256 ctx per slot** — iHi launcher uses `parallel=40` with `ctx=8192`, giving per-slot 256 tokens. iHi prompt is 1601 tokens → 400 errors on evaluate. Fix: bump `CTX_SIZE=32768, PARALLEL=10` in `scripts/start_ihi_sensor.sh`.
3. **20+ concurrent users throttled** — needs load balancer (Phase 3 in CLAUDE.md). 1-3 users: instant. 5-10: 1-3s. 20+: 15-30s with timeouts.
4. **Per-slot ctx for 12B Q4 = 3072** — enough for ~2300-token prompts. Longer conversations might need ctx bump.

---

## Todo khi quay lại (next session)

- [ ] Fix iHi launcher ctx: bump `CTX_SIZE=32768, PARALLEL=10` in `scripts/start_ihi_sensor.sh`
- [ ] Consider bumping 12B Q4 parallel to 12 (need to verify VRAM headroom)
- [ ] If planning production: implement load balancer (Phase 3 in CLAUDE.md)
- [ ] Run `pytest tests/unit/` and fix any real failures (current 2 failures are pre-existing in `test_gen_final_report`)
- [ ] Update CLAUDE.md with final benchmark numbers from this session
- [ ] Decide on Speculative Decoding wire-in (currently rejected in `bench_12b_full`)

---

## Scripts available

- `scripts/start_lite_q8.sh` — 12B Q4 (P4 winner)
- `scripts/start_background_q4.sh` — E2B Q4 bg
- `scripts/start_ihi_sensor.sh` — E2B+mmproj iHi (NEEDS ctx fix)
- `scripts/start_reranker.sh` — Reranker
- `scripts/start.sh` — Master launcher (calls all above in order)
- `scripts/cafe_stress_10.py` — Local-only stress test (20×10, 60s timeout)
- `scripts/compare_e4b_12b.py` — Direct llama-server benchmark (any model)
- `scripts/bench_12b_configs.py` — Full benchmark orchestrator (Stage A + B)

---

## Final state of running services (as of 2026-06-06 18:23)

| Port | Service | PID | VRAM | Started |
|---|---|---|---|---|
| 8000 | uvicorn ai-hub | alive | - | 16:48 |
| 8080 | 12B Q4 (chatbot) | alive | 7.4 GB | 18:04 |
| 8081 | E2B Q4 (background) | alive | 3.1 GB | 16:48 |
| 8082 | bge-reranker | alive | 0.6 GB | 16:48 |
| 8083 | E2B+mmproj (iHi) | alive | 4.5 GB | 17:43 |
| MCP | minimax-coding-plan-mcp × 4 procs | alive | 0.5 GB | various |

**To shutdown before powering off:**
```bash
pkill -f "uvicorn app.main" 2>/dev/null
pkill -f "llama-server" 2>/dev/null
pkill -f "minimax-coding-plan-mcp" 2>/dev/null
# Or simply: ./stop.sh if you have one (else create it)
```

**To power back on later:**
```bash
cd /home/hung/ai-hub && ./start.sh
```

Enjoy your café! ☕
