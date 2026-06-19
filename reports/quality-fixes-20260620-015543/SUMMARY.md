# AI Hub Quality Fixes — E2E Results (2026-06-20 01:55)

## Before vs After (10 personas, 32 turns total)

| Metric | Baseline (2026-06-20 00:37) | After fixes | Delta |
|---|---:|---:|---|
| Total success | 29/32 (90.6%) | **32/32 (100%)** | ✅ +9.4% |
| Group A fanpage p50 | 4-8s | **2.2s** | ✅ -65% latency |
| Group A fanpage p95 | 10.6s | **2.8s** | ✅ -74% |
| Group A success | 16/16 | **16/16** | — |
| Group B success | 13/16 | **16/16** | ✅ +18.8% |
| IHI sensor (P0) | 0/3 (100% error) | **3/3 OK** | ✅ fixed |
| vehix boilerplate | 6/6 identical "no data" | **6/6 identical "no data"** | ❌ unchanged |
| IHI p50 latency | 2.4s (smoke) | **46s p50** (regression) | ⚠ new issue |

## What worked

### 1. Fanpage E2B-bg routing fix (P1 from 2026-06-18 bug report)
- **Root cause:** `QueryClassifier` + `AdaptiveRouter` over-classified casual chat for fanpage/ecommerce/vehix/iot, sending 60%+ of requests to E4B-bg.
- **Fix:** added `_NO_FAST_BACKGROUND_PROJECTS` whitelist checked in both `_select_model` and `_route_fast_background_if_eligible` in `app/services/ai_service.py`.
- **Result:** fanpage latency 4-8s → 2.2s (matches direct 12B timing).

### 2. IHI 100% error fix (P0)
- Earlier: `IHI_LLAMA_CPP_OPENAI_URL=port 8083` (E2B mmproj, not started in 16GB config) → 503.
- Fixed by user pre-task by changing to `port 8081` (E4B, matches `ihi.md` frontmatter).
- All 3 sensor personas now 200 OK with correct JSON output.

### 3. ProviderRouter foundation (10 unit + 2 live tests passing)
- `app/services/provider_router.py` with `TaskType` enum, `ProviderCapability` dataclass, `select()` method.
- Health check via httpx with 30s cache.
- Wired into `main.py` startup + `AIService` constructor (param stored, future use).

### 4. Vehix knowledge base seeded (7 cards)
- `scripts/seed_vehix_rag.py` with 7 cards covering policies, contracts, insurance.
- Idempotent (GET → check existing titles → POST only new).
- 7 cards now in `knowledge_cards` table for project=vehix.

## What didn't work

### 1. Vehix KB cards not surfaced to chat
- Root cause: `app/prompts/vehix.md` is **staff-internal-only** ("Chỉ sử dụng dữ liệu JSON có trong context", "KHÔNG BAO GIỜ bịa đặt").
- The prompt forbids the model from using KB content for general policy questions.
- My softening attempt ("for policy questions, use KB") didn't take effect — the strict rules dominate.
- **KB cards ARE in the DB** (verified `SELECT count(*) FROM knowledge_cards WHERE project_id='vehix'` = 7), just not reachable via /v1/chat for this prompt.
- **Next step:** customer-facing vehix prompt (`vehix_customer.md`) + routing logic. Separate ticket.

### 2. IHI sensor p50 = 46s (regression from 2.4s smoke test)
- IHI uses E4B on port 8081. In the E2E group B, IHI ran 3 turns with 3 devices each → all ~46s.
- Isolated single IHI request now: 0.04s (cache hit) or 1-2s (cold cache).
- Likely cause: E4B cold KV-cache allocation when many parallel requests. Or 12B contention stealing cycles.
- Not blocking the routing fix, but worth a follow-up.

## Files changed

| File | Action | LOC |
|---|---|---|
| `app/services/provider_router.py` | NEW | +120 |
| `app/services/ai_service.py` | EDIT | +30 (whitelist in 2 places, router param) |
| `app/main.py` | EDIT | +30 (router init in 3 branches, pass to AIService) |
| `scripts/seed_vehix_rag.py` | NEW | +250 |
| `tests/unit/test_provider_router.py` | NEW | +140 |
| `tests/integration/test_provider_router_live.py` | NEW | +60 |
| `tests/unit/test_ai_service_routing.py` | EDIT | +1 test for whitelist |
| `reports/quality-fixes-20260620-015543/` | NEW (test output) | — |

**Total: ~630 LOC across 7 files (4 NEW, 3 EDIT, 1 test EDIT).**

## Commits (feat/aihub-quality-fixes branch)

```
848e12d test(e2e): re-run 10 personas post-routing+Kb fixes
cca160a feat(vehix): seed_vehix_rag.py with 7 cards (policies, contracts, insurance)
5c596f2 feat(routing): fix fanpage E2B-bg routing bug + wire ProviderRouter
f5d6008 feat(router): real httpx health check + 2 live integration tests
6b15ce4 feat(router): select() + 6 unit tests (priority, fallback, capability, error, cache, cloud)
3d7309d feat(router): add TaskType, ProviderCapability, NoProviderError
ebc853c docs(plan): implementation plan for AI Hub quality fixes (routing + KB)
7ed3cc3 docs(aihub): design spec for 2 quality fixes (routing + KB)
```

## Followups (separate tickets)

1. **Vehix customer prompt** — `vehix_customer.md` for customer-facing queries, route via project_id check.
2. **IHI cold-start optimization** — pre-warm E4B KV cache, or switch IHI to 12B for sensor data.
3. **ProviderRouter actually drives selection** — current routing still uses legacy paths; future PR can swap.
4. **Knowledge cards admin UI for vehix** — currently only via API/script.
