# AI Hub Project Context

## Project Overview
Central router for per-project AI chat, optimized for local llama.cpp (Q8) with multimodal support, user-scoped session resume, rolling memory, RAG, and local-first security controls.

## Current Access Points
- **Local UI/API**: `http://localhost:8000`
- **Public Domain**: `https://api-aiserver.htechlabsvn.com/`
- **Primary Site Origin**: `https://htechlabsvn.com`

## Implemented Features (current state)

### Core Chat
- **Local inference**: llama.cpp Q8 backend (port 8080), OpenAI-compatible API
- **Cloud fallback**: OpenRouter (`openai/gpt-oss-20b:free`), project allow/deny policy
- **Streaming**: SSE streaming with `[DONE]` sentinel
- **Multimodal**: Image input (Base64 → OpenAI `image_url` content-parts format) — Lite mode only
- **Model modes**: `lite` (Gemma4 E4B Q8, 32k ctx), `thinking` (Qwen 27B), `external` (cloud only)

### Memory System
- **Rolling summaries** (`SummaryService`): async, threshold-triggered (default 20 msgs), marks `is_summarized=1`
- **Structured memory** (`StructMem`): SPO triple extraction, 4 types (episodic, semantic, relational, procedural), consolidation every N extractions
- **Pinned memory**: user-scoped key-value facts with confidence scores
- SummaryService and StructMem are mutually exclusive per conversation

### RAG / Knowledge Base
- Knowledge cards chunking (2000 chars/chunk default)
- **Hybrid Search**: 70% Semantic (FastEmbed `paraphrase-multilingual-MiniLM-L12-v2` with `onnxruntime-gpu`) + 30% Token overlap
- **Reranking**: `bge-reranker-v2-m3` via local llama.cpp (port 8082)
- Domain-based organization, trust levels, versioning, tags
- Endpoints: `POST /v1/knowledge/cards`, `GET /v1/knowledge/cards`, `POST /v1/knowledge/search`
- **Admin Reindex**: `POST /v1/admin/knowledge/reindex` to backfill embeddings

### Web Search
- Multi-backend: Google Custom Search → DDGS → DuckDuckGo HTML → Bing HTML
- Vietnamese domain quality scoring, tracking param removal
- Triggered by `/search:` prefix or `enable_search=true` with `?` in message

### User & Session Management
- User lookup/creation by name, tenant-scoped
- Session resume: `GET /v1/users/{user_name}/sessions`
- `/clear` command in UI: wipes all messages, sessions, summaries for user+project via `DELETE /v1/users/{user_name}/history`

### Security
- `X-API-KEY` header auth
- Per-key rate limiting — **Redis sliding window** (auto-fallback to in-memory nếu Redis down)
- IP-based auth failure tracking and blocking (Redis-backed, fallback in-memory)
- CORS restricted to allowed origins
- Security denial logging to `security.log`

### Infrastructure (Phase 1 — done 2026-05-03)
- **PostgreSQL** thay SQLite: `psycopg3` + `psycopg-pool` connection pool (min=2, max=10)
- **Redis rate limiter**: sliding window ZSET, auto-fallback về InMemory
- **Queue visibility**: `GET /v1/admin/queue` + badge trong UI (poll 3s khi đang request)
- **Per-project context size**: `PROJECT_CONTEXT_SIZES={"proj": num_ctx}` trong `.env`

### Other
- **Failure risk assessment**: risk scoring (0–1.0), log-only or action mode
- **CrewAI agents**: Researcher + Analyst crew (`POST /v1/crew/research`)
- **Whisper Audio Input**: `faster-whisper` (`large-v3-turbo` float16 on CUDA) via `POST /v1/audio/transcriptions`
- **Usage tracking**: token counting, cost calculation, latency, provider/route logging
- **Prediction audit trail**: stock prediction records with outcome evaluation
- **Admin metrics**: `GET /v1/admin/usage`, `GET /v1/admin/stats`, `GET /v1/admin/queue`, `GET /v1/admin/gpu/stats`, `GET /v1/admin/health/providers`
- **Admin tenants/users**: `GET /v1/admin/tenants`, `GET /v1/admin/tenants/{project_id}/users`, `GET /v1/admin/users/{user_id}/detail`, `GET /v1/admin/users/{user_id}/messages`
- **Admin keys**: `POST /v1/admin/keys` (mint), `DELETE /v1/admin/keys/{id}` (disable), `PATCH /v1/admin/keys/{id}` (re-enable / change rpm / budget / admin / name), `GET /v1/admin/management/keys`, `GET /v1/admin/management/sessions`
- **Admin knowledge ops**: `POST /v1/admin/knowledge/upload`, `GET /v1/admin/knowledge/cards`, `DELETE /v1/admin/knowledge/cards/{id}` (cascade chunks via FK), `POST /v1/admin/knowledge/reindex`
- **Admin OS UI** (`static/admin.html` + `admin.css` + `admin.js`): full redesign — Cyber-Slate Refined theme, reusable `DataTable` component (search / filter chips / sortable columns / pagination / per-row icon actions), `Toast` + `Modal.confirm/preview` system, per-tab redesign (Dashboard / GPU / Access Keys / RAG Knowledge / Tenants), live stat cards, GPU progress rings, breadcrumb tenant drilldown, chat history search

## Technical Details

### Database
- **PostgreSQL** `ai_hub` (user: `aihub`, pass: `aihub_pass`, port 5432)
- Connection URL: `DATABASE_URL=postgresql://aihub:aihub_pass@localhost:5432/ai_hub`
- Schema tạo tự động qua `init_db()` khi server khởi động
- Migration script: `scripts/migrate_sqlite_to_pg.py` (idempotent, dùng ON CONFLICT DO NOTHING)

### Redis
- URL: `REDIS_URL=redis://localhost:6379/0`
- Dùng cho: rate limiting (ZSET sliding window) + auth failure tracker
- Auto-fallback về InMemory nếu Redis không available

### Core Services
| Service | Purpose |
|---|---|
| `app/services/ai_service.py` | Orchestrates routing, memory injection, search, provider selection |
| `app/services/history_service.py` | Session/message persistence, `clear_user_history()` |
| `app/services/user_service.py` | User lookup/creation, session listing |
| `app/services/summary_service.py` | Async rolling summaries |
| `app/services/structmem_service.py` | Structured memory pipeline |
| `app/services/knowledge_ingestion_service.py` | RAG card chunking + embeddings extraction |
| `app/services/knowledge_retrieval_service.py` | Hybrid search (vector + token) |
| `app/services/rerank_service.py` | Re-scores search results using cross-encoder |
| `app/services/whisper_service.py` | GPU-accelerated voice transcription |
| `app/services/tools/web_search_service.py` | Multi-backend web search |
| `app/services/failure_risk_service.py` | Failure risk scoring and actions |
| `app/services/providers/llama_cpp.py` | llama.cpp API integration (handles prompt formatting) |
| `app/services/providers/openrouter.py` | Cloud fallback via OpenRouter |

### Security Layer
- `app/middleware/security.py`: API key auth, Redis rate limiting, denial logging
- `app/core/config.py`: All settings — `API_KEY`, `RATE_LIMIT_PER_MINUTE`, `ALLOWED_ORIGINS`, model/memory/search config

### Frontend
- `static/index.html`: API key prompt (localStorage), user-name resume, Lite-only image upload, `/clear` command support, streaming toggle, search toggle, queue badge (⏳ Queue: active/capacity)

## Security Defaults
- **API Key Header**: `X-API-KEY`
- **Default Rate Limit**: `60` requests per minute per API key
- **Security Log File**: `security.log`
- **Allowed Origins**: localhost variants, `https://htechlabsvn.com`, `https://api-aiserver.htechlabsvn.com`

## Build & Run Commands (Local Dev)
```bash
# Start server (PostgreSQL + Redis phải đang chạy)
./venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# Full stack (llama.cpp Q8 + Q4 background + reranker + API server)
./start.sh

# Run tests
./venv/bin/pytest tests/

# Migrate dữ liệu cũ từ SQLite sang PG (chạy một lần)
SQLITE_PATH=ai_hub.db DATABASE_URL=postgresql://aihub:aihub_pass@localhost:5432/ai_hub \
  ./venv/bin/python scripts/migrate_sqlite_to_pg.py

# Kiểm tra health
curl -H "X-API-KEY: ..." http://localhost:8000/health
curl -H "X-API-KEY: ..." http://localhost:8000/v1/admin/queue
```

## Build & Run Commands (Docker)
```bash
docker compose up --build -d
docker compose down
docker compose logs -f app
curl http://localhost:8000/health
```

## Multi-Model GPU Architecture (16GB VRAM)

*   **Primary Chat (Port 8080)**: `E4B Q8` (Context: 32K, Slots: 8). Handles 100% of user chat queries.
*   **Background Tasks (Port 8081)**: `E4B Q4` (Context: 16K, Slots: 2). Dedicated to generating async Summaries and Structured Memory.
*   **Search Reranker (Port 8082)**: `bge-reranker-v2-m3` (Context: 4K). Re-scores knowledge RAG.
*   **FastEmbed**: Runs directly inside API server using `CUDAExecutionProvider`.
*   **Whisper**: Lazy-loaded `large-v3-turbo` model in float16.
*   **Benchmark**: 30 users × 40 questions (1200 requests) processed with 0 errors in 646s (p50: 13.6s) ~111 RPM without OOM.

## Phase Roadmap

### Phase 1 — Software (DONE 2026-05-03)
- [x] PostgreSQL migration (psycopg3 + connection pool)
- [x] Redis rate limiter + auth failure tracker
- [x] Queue depth endpoint + UI badge
- [x] Per-project context size config
- [x] Migration script SQLite → PostgreSQL

### Phase 2 — Hardware Upgrade (Khi cần >200 RPM)
Nâng cấp GPU để tăng throughput thực sự:
- **RTX 5080 (16GB)**: ~220 RPM (+100% vs hiện tại), ~$900. Tốt nếu muốn tăng slot từ 8→16
- **RTX 5090 (32GB)**: ~400 RPM (+260%), ~$2000. Load 2 model đồng thời (chat + thinking)
- **2× RTX 5060 Ti (32GB tổng)**: ~250 RPM, ~$800 tổng. Phù hợp chạy song song primary + background trên GPU riêng
- Cân nhắc: PSU phải đủ (850W+ cho 5090), PCIe slot phải có

### Phase 3 — Horizontal Scale (Khi cần >500 RPM)
- Load balancer (nginx/traefik) phân request qua nhiều instance
- `LlamaCppLoadBalancer` đã có trong `app/services/providers/load_balancer.py` — cần wire vào `main.py`
- Mỗi node: 1 GPU + 1 API server process
- Shared state: PostgreSQL (đã dùng) + Redis (đã dùng) → sẵn sàng multi-node
- Session affinity không cần thiết (history lưu trong PG, không in-memory)

### Phase 4 — Features
- **Billing/quota per API key**: monthly_budget_usd đã có trong schema, cần tracking thực
- [x] **Admin dashboard**: `static/admin.html` đã redesign với theme Cyber-Slate Refined, real-time charts (Chart.js), DataTable search/filter/sort/actions, toast + modal, GPU progress rings, tenant drilldown, chat history search (DONE 2026-05-06)
- [x] **Admin key management**: PATCH endpoint cho re-enable / RPM / budget; UI có nút edit + toggle enable + delete (DONE 2026-05-06)
- [x] **Admin knowledge management**: DELETE endpoint với FK cascade; UI có nút preview + delete per card (DONE 2026-05-06)
- **Model hot-swap API**: `POST /v1/admin/model/switch` đã có, UI có button (cần test thực tế trên prod)
- **Streaming memory extraction**: hiện tại chạy sau khi reply xong, có thể chạy parallel
- **Multi-tenant isolation**: tenant_id đã có trong mọi bảng, cần enforce ở route layer

## Known Limitations (còn lại sau Phase 1)
- Single llama.cpp instance — no horizontal scaling (Phase 3)
- Two large models (e4b + qwen3.5:27b) cannot stay resident simultaneously on 16GB GPU
- No billing enforcement (schema có nhưng chưa implement logic)
- Bulk operations (batch disable keys, batch delete cards) chưa có — chỉ per-row actions trong UI

## File Structure (key files)
```
app/
├── core/config.py              # All settings (DATABASE_URL, REDIS_URL, PROJECT_CONTEXT_SIZES)
├── core/database.py            # PostgreSQL schema + psycopg3 pool
├── models/chat.py              # ChatRequest, ChatResponse, Message
├── routes/chat.py              # POST /v1/chat
├── routes/users.py             # GET/DELETE /v1/users/{user_name}/...
├── routes/knowledge.py         # /v1/knowledge/*
├── routes/memory.py            # GET /v1/memory
├── routes/admin.py             # /v1/admin/* — usage, stats, queue, gpu/stats, tenants, users/{id}/detail, keys (POST/DELETE/PATCH), knowledge/cards (GET/DELETE), reindex, model/switch
├── middleware/security.py      # Auth + Redis rate limiting
├── services/ai_service.py      # Core orchestrator (per-project ctx, num_ctx injection)
├── services/providers/
│   ├── llama_cpp.py            # Local provider (num_ctx in _ALLOWED_OPTIONS)
│   ├── openrouter.py           # Cloud provider
│   └── load_balancer.py        # Multi-node load balancer (ready, not wired yet)
├── services/tools/
│   └── web_search_service.py   # Multi-backend search
scripts/
├── start_lite_q8.sh            # Launch llama.cpp Q8 (port 8080)
├── start_background_q4.sh      # Launch llama.cpp Q4 background (port 8081)
├── start_reranker.sh           # Launch reranker (port 8082)
├── migrate_sqlite_to_pg.py     # One-shot data migration SQLite → PostgreSQL
├── perf_hybrid_test.py         # Hybrid local+cloud benchmark
├── autotune_q8_multiuser.py    # Multi-config autotune sweep
├── seed_multi_user.py          # Seed multi-tenant/project/user demo data for admin UI
├── loadtest.py                 # Continuous load test (multi-tenant)
static/
├── index.html                  # Main chat UI (queue badge, streaming, search)
├── admin.html                  # Admin OS — markup only (~280 lines)
├── admin.css                   # Cyber-Slate Refined theme tokens, glass surfaces, DataTable, toast, modal
└── admin.js                    # Admin OS logic — DataTable component, API client, toast/modal, charts, per-tab handlers
reports/                        # Benchmark results and notes
```
