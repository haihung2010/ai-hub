# IHI RAG In-Flight Audit + Ship

**Date:** 2026-06-06
**Status:** Approved (brainstorming 2026-06-06)
**Author:** Brainstorming session with user
**Related:**
- `2026-06-03-ihi-rag-optimization-design.md` (predecessor design, still Draft)
- `2026-06-03-ihi-rag-optimization.md` (implementation plan)

---

## 1. Background

Spec `2026-06-03-ihi-rag-optimization-design.md` describes a 3-layer pipeline
(NEMA/ISO standards + per-device overrides + RAG context) for IHI verdicts.
Implementation is **in-flight** with these uncommitted modifications:

| Status | File | Approx lines | Purpose |
|---|---|---|---|
| M | `app/middleware/security.py` | +1 | Tweak rate-limit |
| M | `app/routes/admin.py` | +9/-2 | Admin IHI endpoints |
| M | `app/routes/chat.py` | +63 | Chat route updates |
| M | `app/routes/ihi.py` | +1/-1 | IHI route fix |
| M | `app/services/fact_extraction_service.py` | +264 | SPO extraction refactor |
| M | `app/services/knowledge_ingestion_service.py` | +133 | Ingestion tweaks |
| M | `scripts/start_background_q4.sh` | +1/-1 | Background model launcher |
| M | `static/admin.css`, `admin.v2.js`, `admin3.css` | many | UI changes |
| M | `tests/unit/test_minimax_provider.py` | +26 | New tests |
| ?? | `app/services/tracing_service.py` | new | Langfuse wrapper |
| ?? | `tests/unit/test_contextual_retrieval.py` | new | Contextual RAG tests |
| ?? | `tests/unit/test_fact_extraction_service.py` | new | Fact extraction tests |
| ?? | `tests/integration/test_fact_extraction_live.py` | new | Live integration test |

**Total diff** (per `git diff --stat`): 11 files modified, ~1130 insertions,
~1363 deletions. 4 untracked new files.

## 2. Goal

Audit + ship the in-flight IHI RAG work, OR document a rollback path
if the changes regress the false-negative rate that motivated the original spec.

## 3. Approach

Single sub-agent in worktree `ihi-rag-audit` (branch `feat/ihi-rag-audit`):

1. **Read** predecessor design `2026-06-03-ihi-rag-optimization-design.md`
   + implementation plan `2026-06-03-ihi-rag-optimization.md`.
2. **Diff current working tree** against the spec — flag any deltas where
   implementation diverges from design.
3. **Run live IHI test** on the most recent `alert.db` historical data
   (or seeded sample if not enough data). Measure:
   - False-negative rate (verdict NORMAL when readings abnormal)
   - Hallucination rate (verdict = "ArrayList" / "CLASS-NORMAL" / etc.)
   - Verdict consistency (same input → same output across 4 cycles)
4. **Compare** against baseline numbers documented in spec
   (18% FN, 14% hallucination, 4 inconsistent cycles).
5. **Decide** ship or rollback:
   - If new numbers ≤ baseline on all 3 axes → fix any HIGH/CRITICAL
     code-review issues found, run unit tests, commit.
   - If regression on any axis → document rollback steps in
     `reports/ihi-rag-audit-2026-06-06.md`, **do not commit**.

## 4. Components

### 4.1 Worktree
- Branch: `feat/ihi-rag-audit` based on `main`
- Path: `.worktrees/ihi-rag-audit`
- Isolation: `isolation: "worktree"` on Agent tool

### 4.2 Files in scope
- All 13 files listed in §1 (11 modified + 4 untracked → 4 of them untracked new)
- DO NOT touch: 12B Q4 full optimization (already shipped), MiniMax
  WebSearch MCP (already shipped)

### 4.3 Tests required to pass before commit
- `tests/unit/test_contextual_retrieval.py` (new)
- `tests/unit/test_fact_extraction_service.py` (new)
- `tests/unit/test_minimax_provider.py` (modified, +26 lines)
- `tests/integration/test_fact_extraction_live.py` (new, may require live API key)
- Any pre-existing IHI tests in `tests/`

## 5. Data flow

```
worktree (feat/ihi-rag-audit)
  │
  ├── git status → confirm files match §1
  ├── diff against spec 2026-06-03 → flag deltas
  ├── live test: python -m scripts.test_ihi_real_sensor
  │     measure: FN-rate, hallucination, consistency
  ├── compare vs baseline (18% FN, 14% halluc)
  ├── if regression → write reports/ihi-rag-audit-2026-06-06.md (no commit)
  ├── if no regression → code-review → fix HIGH/CRITICAL → run tests
  └── commit (if all green) → report deliverables
```

## 6. Error handling

- **Live test crashes**: log full traceback to report, mark as inconclusive,
  recommend manual review
- **Tests fail**: do NOT commit. Document in report with full test output.
- **Worktree conflicts** with other agents (B is read-only, C is separate
  worktree): no expected conflict because A and C touch disjoint code
  (A: ihi/chat/fact_extraction; C: knowledge_ingestion scraper)
  — but verify no overlap on `knowledge_ingestion_service.py` before merge.

## 7. Testing

Pre-commit gates:
- [ ] `pytest tests/unit/test_contextual_retrieval.py` passes
- [ ] `pytest tests/unit/test_fact_extraction_service.py` passes
- [ ] `pytest tests/unit/test_minimax_provider.py` passes
- [ ] Live IHI test: FN-rate ≤ 18%, hallucination ≤ 14%
- [ ] No new HIGH/CRITICAL from `code-reviewer` agent

## 8. Deliverables

- `reports/ihi-rag-audit-2026-06-06.md` — audit findings, baseline comparison,
  decision (ship/rollback), test output excerpts
- Commit(s) on `feat/ihi-rag-audit` (if ship)
- OR: explicit rollback instructions in report (if rollback)

## 9. Token budget

~6-8M tokens for Agent A. Stay within budget; if context fills, dump
intermediate findings to report and resume.
