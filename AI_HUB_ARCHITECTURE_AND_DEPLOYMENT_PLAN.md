# AI Hub Architecture and Deployment Plan

## 1) End-to-end flow chart

```text
[Browser UI / Client App / External Project]
        |
        |  HTTP request
        |  POST /v1/chat
        |  GET  /v1/users/{user_name}/sessions
        v
+----------------------+
| FastAPI app          |  app/main.py:71
| create_app()         |
+----------------------+
        |
        +--> CORS middleware                     app/main.py:129
        |
        +--> SecurityMiddleware                  app/main.py:136
               - check X-API-KEY                app/middleware/security.py:68
               - rate limit /minute             app/middleware/security.py:78
               - security denial logging        app/middleware/security.py:88
        |
        +--> Router layer
               - /v1/chat                       app/routes/chat.py:11
               - /v1/users/.../sessions         app/routes/users.py:13
               - /health
               - /v1/crew/*
        |
        v
+----------------------+
| AIService            |  app/services/ai_service.py:27
+----------------------+
        |
        +--> Resolve user by user_name          ai_service.py:248
        |     via UserService                   app/services/user_service.py:17
        |
        +--> Resolve/create session_id          ai_service.py:265
        |     via HistoryService.create_session app/services/history_service.py:11
        |
        +--> Load chat history                  ai_service.py:254
        |     from SQLite                       history_service.py:38
        |
        +--> Load rolling summary (legacy)      ai_service.py:258
        |     via SummaryService                app/services/summary_service.py:25
        |
        +--> Load StructMem retrieval           ai_service.py:70
        |     via MemoryRetrievalService        app/services/memory_retrieval_service.py:19
        |
        +--> Load project prompt                ai_service.py:300
        |     via load_prompt()
        |
        +--> Optional web search decision       ai_service.py:143
        |     and context injection             ai_service.py:157,196
        |
        +--> Assemble final prompt/messages     ai_service.py:218,268
        |
        +--> Select model + num_ctx             ai_service.py:213
        |     - lite -> gemma4:e4b              config.py:36-41
        |     - normal -> default model
        |
        +--> GPU concurrency semaphore          ai_service.py:47,288
        |
        v
+----------------------+
| OllamaProvider       |  app/main.py:80
| local model backend  |
+----------------------+
        |
        v
   [LLM completion]
        |
        v
+----------------------+
| AIService postprocess|
+----------------------+
        |
        +--> append Sources if web search used  ai_service.py:184
        |
        +--> return ChatResponse                ai_service.py:315-317
        |
        +--> save user+assistant messages       ai_service.py:261
        |     to SQLite                         history_service.py:21
        |
        +--> fire async memory job             ai_service.py:103
               |
               +--> StructMem path              ai_service.py:111
               |     StructMemService           structmem_service.py:11
               |     - get unsummarized msgs    structmem_service.py:34
               |     - extract_and_store        structmem_service.py:38
               |     - mark summarized          structmem_service.py:50
               |
               +--> or legacy summary path      ai_service.py:124
                     SummaryService.summarize   summary_service.py:44
                     - summarize chunks
                     - upsert latest summary    summary_service.py:91
                     - mark summarized msgs     summary_service.py:83
```

## 2) Detailed data flow by module

### A. App bootstrap
- App initializes at `app/main.py:71`
- At startup:
  - `init_db()` creates/migrates SQLite schema at `app/core/database.py:17`
  - creates `OllamaProvider`, `HistoryService`, `UserService`, `SummaryService`, `MemoryExtractionService`, `MemoryRetrievalService`, `StructMemService`, `WebSearchService` at `app/main.py:79-103`
  - builds `AIService` at `app/main.py:109`

### B. Security boundary
- Every request except `/`, `/health`, `/docs`, `/openapi.json`, `/redoc` passes through `SecurityMiddleware` at `app/middleware/security.py:64`
- Checks:
  - API key header `X-API-KEY` at `security.py:20,68`
  - in-memory rate limit at `security.py:31`
  - security log via logger `app.security` at `security.py:19,88`

### C. Chat request flow
- `POST /v1/chat` enters `chat_endpoint()` at `app/routes/chat.py:11`
- Real streaming is not supported yet; returns `501` if `payload.stream=true` at `chat.py:13-17`
- Full chat orchestration lives in `AIService.chat()` at `app/services/ai_service.py:294`

### D. Session + user resume flow
- If `user_name` exists, the system calls `UserService.get_or_create_user()` at `ai_service.py:248-252`
- If `session_id` is missing, it creates a new session via `HistoryService.create_session()` at `ai_service.py:265-266`
- Resume list uses:
  - endpoint `GET /v1/users/{user_name}/sessions` at `app/routes/users.py:13`
  - lookup via `UserService.find_by_name()` and `find_sessions_for_user()` at `user_service.py:40,54`

### E. Prompt assembly flow
Final prompt is composed of:
1. project system prompt
2. StructMem blocks if enabled
3. rolling summary if StructMem is not enabled
4. trimmed history
5. current user message
6. optional web-search context

Code:
- `_assemble()` at `ai_service.py:268`
- `_build_structmem_blocks()` at `ai_service.py:91`
- `_inject_search_context()` at `ai_service.py:196`

### F. Persistence layer
SQLite file:
- `ai_hub.db` at `app/core/database.py:8`

Primary tables:
- `sessions` `database.py:22`
- `messages` `database.py:30`
- `users` `database.py:42`
- `summaries` `database.py:52`
- `memory_episodes` `database.py:64`
- `memory_items` `database.py:82`
- `memory_consolidations` `database.py:104`

### G. Current memory architecture
There are **2 memory layers** coexisting:

1. **Legacy rolling summary**
- `SummaryService` at `app/services/summary_service.py:25`
- aggregates unsummarized messages -> calls model -> updates `summaries`

2. **StructMem**
- retrieval at `memory_retrieval_service.py:120`
- extraction pipeline at `structmem_service.py:20`
- AIService prioritizes StructMem when `enable_structmem=true` at `ai_service.py:77,111`

This means the current direction is clearly moving from summary-only toward StructMem-first.

## 3) Deployment flow by environment

```text
[Developer / Local machine]
      |
      +--> .env
      +--> docker compose up --build -d
      |
      v
+-------------------+        +----------------------+
| FastAPI app       | <----> | Ollama local         |
| port 8000         |        | port 11434           |
+-------------------+        +----------------------+
      |
      +--> SQLite ai_hub.db
      |
      +--> static/index.html
      |
      +--> external callers / frontend projects
```

Public access according to `CLAUDE.md`:
- local: `http://localhost:8000`
- public: `https://api-aiserver.htechlabsvn.com/`

## 4) AI Hub deployment plan

I split this into 4 phases so it matches the current codebase and stays practical to roll out.

### Phase 1 — Stabilize the core chat platform
**Goal:** AI Hub runs reliably as a local-first chat router.

#### What to finalize
1. **Lock the API contract**
   - standard request/response for `/v1/chat`
   - standard session resume for `/v1/users/{user_name}/sessions`

2. **Stabilize security**
   - enforce `X-API-KEY`
   - tune `RATE_LIMIT_PER_MINUTE`
   - audit security logs
   - validate CORS whitelist at `app/core/config.py:68`

3. **Stabilize provider behavior**
   - finalize lite / normal model mapping
   - finalize timeout / fallback policy
   - measure real Ollama latency

4. **Stabilize DB behavior**
   - backup/restore strategy for `ai_hub.db`
   - retention/rotation strategy for growing `messages`

#### Deliverables
- production `.env`
- stable healthcheck
- smoke tests for `/health`, `/v1/chat`, `/v1/users/.../sessions`
- basic dashboard/logging

### Phase 2 — Standardize the memory pipeline
**Goal:** make “context memory” a first-class product capability.

#### Current state
- summary pipeline exists
- StructMem foundation exists
- retrieval is already injected into prompts
- extraction runs async

#### Recommended next work
1. **Pick one primary direction**
   - If simplicity matters most: keep rolling summary
   - If long-term scale matters most: move fully to StructMem-first

2. **If choosing StructMem-first**
   - complete `MemoryExtractionService`
   - add consolidation scheduler for `memory_consolidations`
   - define salience rules / TTL / dedupe
   - add retrieval-quality tracing/debugging

3. **If choosing Summary-first**
   - limit version growth
   - define replacement strategy clearly
   - add summary quality evaluation

#### Deliverables
- memory strategy doc
- retrieval quality tests
- observability for:
  - number of memory items retrieved
  - hit rate
  - prompt token cost before/after memory injection

### Phase 3 — Tooling and agent capabilities
**Goal:** AI Hub becomes task-oriented, not just chat-oriented.

#### Already present
- web search tool
- crew service toggle `enable_crew_agents` at `config.py:64`

#### What to implement
1. **Tool execution policy**
   - define which projects can use which tools
   - log tool calls
   - add timeout + denylist rules

2. **Crew/agent orchestration**
   - use for research / analysis / multi-step tasks
   - separate role responsibilities clearly
   - optionally persist crew runs

3. **Project-specific prompt packs**
   - each `project_id` gets its own system prompt, tool policy, and model profile

#### Deliverables
- tool registry
- crew execution flow
- permission policy by project

### Phase 4 — Production hardening
**Goal:** reliable deployment, monitorable behavior, and easier scaling.

#### What to do
1. **Observability**
   - request logs
   - latency p50/p95
   - Ollama errors / timeout
   - rate-limit denials
   - memory extraction failures

2. **Persistence hardening**
   - scheduled SQLite backup
   - or migrate to Postgres if concurrent write volume grows

3. **Concurrency strategy**
   - current `gpu_concurrency` semaphore at `ai_service.py:47`
   - benchmark against actual models
   - for many tenants/users, introduce queueing or worker model pools

4. **Streaming**
   - currently missing, returns `501` at `routes/chat.py:13`
   - should be the next production feature if better UX is needed

5. **Auth model upgrade**
   - currently static API key
   - for public multi-tenant use, move to per-tenant / per-client API keys

#### Deliverables
- production monitoring
- backup strategy
- load test
- deployment checklist

## 5) Recommended target architecture

### Most practical version right now
Recommended direction:

#### Suggested option
**AI Hub v1.5**
- FastAPI router
- Ollama local
- API key security
- user/session resume
- StructMem-first memory
- optional web search
- SQLite for local/small-scale
- Docker deployment

#### Why this fits
- matches the current codebase closely
- avoids large refactors
- faster rollout
- strong enough for multiple internal projects

## 6) Short execution roadmap

### Sprint 1
- finalize request contract
- finalize production env
- verify all endpoints
- smoke tests
- baseline security audit

### Sprint 2
- complete StructMem extraction + consolidation
- add tests for retrieval relevance
- measure memory quality

### Sprint 3
- crew/tool orchestration
- project-specific prompt config
- streaming design

### Sprint 4
- monitoring
- backup
- load testing
- public deployment checklist

## 7) Current bottlenecks visible in code

1. **Streaming is missing** — `app/routes/chat.py:13`
2. **Summary and StructMem run in parallel conceptually** — risk of overlapping logic
3. **Rate limiting is in-memory** — state resets on restart
4. **SQLite is fine for local/small-scale**, but will hit limits under heavier concurrent write load
5. **Agent/Crew path is not yet the mainline runtime path**, still an optional capability

## 8) Short conclusion
AI Hub is currently a **local-first central AI router** with a fairly clean architecture:

**Security -> Routing -> AIService orchestration -> Prompt assembly -> Ollama -> Persistence -> Async memory jobs**

For the next step, this document can be turned into either:
1. a Mermaid diagram for docs/GitHub
2. a detailed weekly implementation plan for team execution
