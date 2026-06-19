# AI Hub Quality Fixes — Design (2026-06-20)

## Context

The 2026-06-20 10-persona parallel load test ([[session-2026-06-20-parallel-scenarios]]) and the prior 2026-06-18 ecom test both surfaced the same root cause: the AI Hub routes ~60% of fanpage / ecom requests to the **E2B-bg model** (port 8081) instead of the **12B Q4 primary** (port 8080). This inflates latency 3-5× and reduces response quality. The parallel test also confirmed a second gap: the `vehix` project has **0 knowledge cards indexed**, so the model defaults to "Tôi không có dữ liệu" boilerplate for every turn.

A previous fix attempt ([[bug-fastbackground-e2b-2026-06-18]]) was deferred. This spec addresses both gaps in one PR.

## Goals

- **G1:** `fanpage` (and other chat projects) route to 12B Q4 primary by default; bg only used for `summary` / `structmem` / `contextualize` tasks
- **G2:** `vehix` project produces non-boilerplate, context-aware answers for the 2 verified personas (`vehix_rental_01`, `vehix_contract_01`)
- **G3:** Existing 212 baseline tests remain green; no latency regression for IHI/IoT (which currently route to E4B by design)

## Non-goals

- 24GB+ multi-model stack tuning (separate ticket)
- Cross-tenant leakage audit (separate ticket)
- KB admin UI for vehix (separate ticket)
- Migration script to backfill contextual retrieval for existing cards (separate ticket)

## Design

### Sub-project A: ProviderRouter

**New file: `app/services/provider_router.py`** (~120 LOC)

A `ProviderRouter` class replaces ad-hoc provider selection in `ai_service.py` and `main.py`. It holds a list of `ProviderCapability` (name, base_url, priority, supported `TaskType`s) and exposes:

```python
class TaskType(str, Enum):
    CHAT = "chat"
    STRUCTMEM = "structmem"
    SUMMARY = "summary"
    CONTEXTUALIZE = "contextualize"
    VISION = "vision"

@dataclass(frozen=True)
class ProviderCapability:
    name: str
    base_url: str
    priority: int  # 1=highest
    supports: set[TaskType]
    health_url: str | None = None

class ProviderRouter:
    def __init__(self, providers: list[ProviderCapability], health_check_ttl_sec: int = 30): ...
    async def select(self, task: TaskType, project_id: str) -> ProviderCapability: ...
    async def _is_healthy(self, p: ProviderCapability) -> bool: ...  # cached 30s
```

**Selection algorithm:** sort by `priority` ascending, return first provider that (a) passes health check and (b) supports the requested `TaskType`. Raise `NoProviderError` if none.

**Provider registration** (in `main.py` startup, replacing the current ad-hoc init at lines 280-306):
- `llama_cpp_12b` (port 8080) — priority=1, supports {CHAT, CONTEXTUALIZE}
- `llama_cpp_e4b` (port 8081) — priority=2, supports {CHAT, STRUCTMEM, SUMMARY, CONTEXTUALIZE}
- `llama_cpp_e2b` (port 8083) — priority=3, supports {CHAT, VISION} (only if E2B started)
- `llama_cpp_reranker` (port 8082) — priority=4, supports {} (special, not used by select())
- `minimax_m3` (cloud) — priority=10, supports {CHAT} (fallback if all local down)

**TaskType inference** at the call sites in `ai_service.py`:
- existing `_load_summary` / `_load_structmem` paths → `SUMMARY` / `STRUCTMEM`
- existing `_build_knowledge_block` with `ENABLE_LLM_CONTEXTUALIZER=true` → `CONTEXTUALIZE`
- request contains `image_base64` → `VISION`
- default → `CHAT`

### Sub-project B: Vehix knowledge base

**New file: `scripts/seed_vehix_rag.py`** (~150 LOC)

Copy the structure of `scripts/seed_ihi_rag.py`. Seed 7 cards covering the most common vehix queries:

1. `Phí gia hạn hợp đồng` — 50-150k/ngày theo loại xe
2. `Phí trả xe trễ giờ` — 50k/giờ, 3h+ = 1 ngày
3. `Quy trình đặt cọc` — 30-50% giá trị xe
4. `Hợp đồng thuê xe ga` (Honda Vision, Lead) — điều khoản, bảo hiểm
5. `Hợp đồng thuê xe số` (Wave, Dream) — điều khoản, bảo hiểm
6. `Hợp đồng thuê xe điện` (VF3, VF8) — pin, sạc, bảo hiểm
7. `Bảo hiểm thuê xe` — 2 loại (TNDS bắt buộc + tự nguyện), mức bồi thường

Each card: `domain=vehix`, `subdomain=policies|contracts|insurance`, `trust_level=high`, `tags=[rental, contract, fee]`. Idempotent (uses ON CONFLICT DO NOTHING on `slug`).

**Edit `app/prompts/vehix.md`:** add a "Khi KB empty" section instructing the model to give generic guidance instead of refusing with "Tôi không có dữ liệu":

```markdown
## Khi không tìm thấy dữ liệu trong knowledge base
- VẪN đưa ra hướng dẫn chung (phí, thủ tục, chính sách phổ biến)
- KHÔNG từ chối với câu "Tôi không có dữ liệu này"
- Tham chiếu các policy phổ biến (phí gia hạn 50-150k/ngày, phí trễ 50k/giờ, cọc 30-50%) làm default
- Hỏi thêm thông tin cụ thể (biển số, mã hợp đồng) nếu cần tra cứu
```

**Reindex trigger:** `POST /v1/admin/knowledge/reindex?project=vehix&force=true` after seeding, so embeddings are generated.

## Error handling

- `ProviderRouter` raises `NoProviderError` when no healthy provider supports the task → propagates as HTTP 503 (same path as current `All connection attempts failed`)
- Health check failures cached for 30s to avoid hammering llama.cpp `/health` endpoints
- `seed_vehix_rag.py` exits non-zero on DB connection failure; partial seed is OK (idempotent re-run continues)
- Reindex failure logs error but does not block seed (cards remain queryable via FTS even without embeddings, just slower)

## Testing

### Unit tests (`tests/unit/test_provider_router.py`, ~150 LOC)

- `test_selects_highest_priority_healthy` — primary up → primary returned
- `test_falls_back_when_top_unhealthy` — primary down → e4b returned
- `test_capability_filter_skips_wrong_task` — vision request → e4b skipped, e2b returned
- `test_health_cache_avoids_recheck` — second call within 30s skips HTTP
- `test_no_provider_raises` — all down → NoProviderError
- `test_minimax_fallback_when_all_local_down` — local all down → minimax_m3 returned

### Integration test (`tests/integration/test_vehix_rag_seeded.py`, ~60 LOC)

- Run `seed_vehix_rag.py --dry-run` → assert 7 cards counted, 0 written
- Run `seed_vehix_rag.py` → assert 7 cards in DB with `domain=vehix`
- POST `/v1/chat` `project_id=vehix` with rental query → assert reply contains at least 1 policy term from the seed cards

### E2E

Re-run `scripts/loadtest_scenarios.py --group A --group B` (10 personas, ~5 min load + 8 min analyze). Compare with baseline `reports/scenario-parallel-20260620-003727/SUMMARY.md`:
- Target: 32/32 success (was 29/32)
- Target: fanpage p50 < 3s (was 4-8s, will improve with 12B serving)
- Target: vehix 0 boilerplate refusals (was 6/6 identical "no data" replies)
- Target: IHI still 3/3 sensor pass (regression check)

## Risks + mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| ProviderRouter breaks 212 baseline tests | Medium | High | Run full pytest before/after; revert if any test regresses |
| Health check TTL too short → hammers llama.cpp | Low | Medium | Default 30s, configurable via env, documented |
| Vehix cards duplicate existing fanpage cards | Low | Low | Distinct `domain=vehix` + RAG retrieval filters by project |
| Reindex 7 cards takes >5s blocking seed | Low | Low | Force=true with single project scope; no blocking dependency |
| Routing change breaks MiniMax cloud fallback | Low | High | MiniMax is separate code path (`providers/minimax.py`), not touched by router |
| E4B at port 8081 gets new load if 12B unreachable | Medium | Medium | Router caches health, so transient 12B hiccups → 30s E4B serve window only |

## Files touched

| File | Action | LOC delta |
|---|---|---|
| `app/services/provider_router.py` | NEW | +120 |
| `app/services/ai_service.py` | EDIT | -20 + 30 |
| `app/main.py` | EDIT (router init) | +15 |
| `app/prompts/vehix.md` | EDIT (add fallback section) | +20 |
| `scripts/seed_vehix_rag.py` | NEW | +150 |
| `tests/unit/test_provider_router.py` | NEW | +150 |
| `tests/integration/test_vehix_rag_seeded.py` | NEW | +60 |

**Total: ~525 LOC across 7 files (3 NEW, 4 EDIT).**

## Open questions

None — design approved 2026-06-20 by user. Proceed to writing-plans.
