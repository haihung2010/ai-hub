# PostgreSQL Health Report (30-day window)

**Date:** 2026-06-06
**Status:** Approved (brainstorming 2026-06-06)
**Author:** Brainstorming session with user

---

## 1. Background

ai-hub uses PostgreSQL (`ai_hub` DB on port 5432) for all persistent state:
messages, sessions, users, knowledge cards, memory, usage, rate-limiter
fallback, etc. Recent optimization work (12B Q4 full, MiniMax MCP, IHI RAG)
has shipped without a baseline measurement of actual production traffic.

**Goal:** Produce a comprehensive health report over the last 30 days that
identifies concrete optimization targets.

## 2. Approach

Single sub-agent, **read-only** (no worktree, no code changes). Scope is
SQL queries + analysis + markdown report.

## 3. Dimensions to measure

### 3.1 Latency
- p50, p95, p99 request latency, cross-tabbed by `tenant_id` × `model`
- Top 10 slowest (tenant, model, endpoint) combinations
- Latency trend over 30 days (any regression after recent optimization?)
- Identify tail-latency outliers

### 3.2 Cost & token usage
- Total tokens in/out, total cost USD, by model × tenant
- Cloud-fallback ratio (MiniMax / OpenRouter calls vs local llama.cpp)
- Cost per tenant (top 10 spenders)
- Identify tenants using expensive models (e.g. E2B for short msgs where
  background model would suffice)

### 3.3 RAG & memory quality
- `knowledge_chunks`: count, avg size, total embeddings size
- RAG hit-rate: how often does `search_knowledge()` return >0 results?
- `summaries`: count created, trigger threshold frequency
- `structmem`: SPO triples count by type
- `pinned_memory`: per-user count
- Hallucination signal: count of responses <20 chars (likely error/empty)
  or with token pattern suggesting fallback

### 3.4 Rate-limit & auth
- `rate_limit_hits` (Redis fallback table or `usage_logs`): count per API key
- `auth_failures`: per IP, per API key
- IP blocks triggered (last 30 days)

### 3.5 Provider error rates
- Error count by provider (llama.cpp / MiniMax / OpenRouter)
- Common error types (timeout / 4xx / 5xx)
- llama.cpp slot-saturation events (`queue_depth` spikes)

## 4. Data flow

```
sub-agent (read-only)
  │
  ├── connect: psql $DATABASE_URL
  ├── query 1: latency by tenant × model
  ├── query 2: cost breakdown
  ├── query 3: RAG/memory quality
  ├── query 4: rate-limit + auth
  ├── query 5: provider errors
  ├── cross-validate: each result's row count vs SELECT COUNT(*) FROM <table>
  └── write reports/health-2026-06-06.md
```

## 5. Output format

`reports/health-2026-06-06.md` with sections:
1. Executive summary (5-10 bullet points)
2. Latency analysis (table + chart text)
3. Cost analysis
4. RAG/memory quality
5. Rate-limit & auth
6. Provider errors
7. **Concrete optimization recommendations** (this is the deliverable —
   ranked by impact:effort ratio)
8. Appendix: raw SQL queries used

## 6. Error handling

- SQL error on a dimension → log, skip dimension, note in report
- Empty result (e.g. no provider errors) → note as "healthy"
- Cross-validation mismatch → flag, may indicate incomplete data

## 7. Token budget

~3-4M tokens. Read-only queries, so context stays small. All findings
go to single report file.

## 8. Dependencies

- Output feeds into sub-project C (RAG scrape will use top topics from §3.3)
- Agent B should be dispatched **before** Agent C starts scraping.
