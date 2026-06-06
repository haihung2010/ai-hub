# RAG Knowledge Scrape + Ingest

**Date:** 2026-06-06
**Status:** Approved (brainstorming 2026-06-06)
**Author:** Brainstorming session with user

---

## 1. Background

ai-hub has a working RAG pipeline (`knowledge_ingestion_service` →
hybrid search with FastEmbed + bge-reranker). The existing knowledge
base is curated manually via Admin UI upload. With MiniMax WebSearch MCP
just shipped, we can now scrape public Vietnamese content at scale to
grow the KB.

**Goal:** Agent determines the most-needed domains (from Agent B's health
report), scrapes high-quality content, ingests via existing pipeline,
and measures retrieval quality.

## 2. Approach

Single sub-agent in worktree `rag-scrape` (branch `feat/rag-scrape`):

1. **Wait for** Agent B's `reports/health-2026-06-06.md` (or read it
   if already written when this agent starts).
2. **Identify top 5 conversation topics** by message frequency in PG
   (cross-check with B's RAG analysis in §3.3).
3. **Scrape** each topic using MiniMax WebSearch MCP — Vietnamese-language
   results, 30-50 sources per topic. Prioritize: official docs > reputable
   news > forum discussions.
4. **Deduplicate** scraped content (URL + content hash).
5. **Ingest** via `knowledge_ingestion_service.create_card()` (existing
   API). Set `domain` to the topic slug, `trust_level` based on source
   (official=high, news=medium, forum=low).
6. **Measure** retrieval quality:
   - Generate 10 test queries from the scraped content
   - Call `POST /v1/knowledge/search` for each
   - Check if any of the new cards appear in top-5 results
   - Compare hit-rate vs pre-scrape baseline (run same queries first)

## 3. Components

### 3.1 Worktree
- Branch: `feat/rag-scrape` based on `main`
- Path: `.worktrees/rag-scrape`
- Isolation: `isolation: "worktree"` on Agent tool

### 3.2 New code (if needed)
- `scripts/scrape_rag.py` — orchestrator: calls MiniMax MCP, dedupes, ingests
- `tests/integration/test_rag_scrape.py` — quality measurement

If existing `knowledge_ingestion_service` covers all needs, no new code;
just the script.

### 3.3 Fallback
- MiniMax MCP fails repeatedly (circuit-breaker triggers after 3 failures
  per `app/services/mcp/minimax_websearch.py`) → wait 5 min for breaker
  reset and retry, then if still failing, skip scrape and use a small
  hand-curated sample (5-10 cards) to prove the ingestion pipeline works.
- Do **not** reintroduce DDGS — it was removed in commit `4f0a51d` and
  has known Vietnamese-quality issues per user feedback.
- **License/copyright**: only scrape public, attributable content. No
  paywalled sources. Note source URL on each card for attribution.

## 4. Data flow

```
worktree (feat/rag-scrape)
  │
  ├── read reports/health-2026-06-06.md → extract top 5 topics
  ├── for each topic:
  │     MiniMax MCP search → 30-50 URLs
  │     fetch + extract text (httpx + readability)
  │     dedupe (sha256 of normalized text)
  │     chunk (2000 chars) via knowledge_ingestion
  │     embed via FastEmbed (CPU)
  │     store in PG knowledge_chunks
  ├── measure: 10 test queries → top-5 retrieval hit-rate
  └── write reports/rag-scrape-2026-06-06.md
```

## 5. Error handling

- MiniMax MCP circuit-breaker (3 failures): switch to DDGS, note in report
- HTTP fetch fail: skip URL, continue
- Embedding fail: skip chunk, log
- Quality regression: do NOT commit, document in report, suggest manual
  review of new cards

## 6. Testing

- Pre: measure baseline hit-rate on 10 test queries (using existing KB only)
- Post: re-run same 10 queries, compare hit-rate
- Threshold: do not commit if hit-rate drops by >5 percentage points
- `pytest tests/integration/test_rag_scrape.py` (new) should pass

## 7. Deliverables

- `scripts/scrape_rag.py` (new, committed)
- `tests/integration/test_rag_scrape.py` (new, committed)
- New knowledge cards in PG (rows in `knowledge_cards` + `knowledge_chunks`)
- `reports/rag-scrape-2026-06-06.md` — topics, sources, dedup stats,
  hit-rate before/after, sample queries
- Commit on `feat/rag-scrape`

## 8. Token budget

~4-6M tokens. Scraped content is the bulk — keep raw content in
working memory only briefly, flush to PG immediately.

## 9. Dependencies

- Agent B's report (specifically the top 5 topics)
- MiniMax API key (`MINIMAX_API_KEY` in env)
- Existing `knowledge_ingestion_service` (no changes needed if API covers it)
