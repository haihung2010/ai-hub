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
- **Cloud fallback**: OpenRouter (`openai/gpt-oss-120b:free`), project allow/deny policy
- **Streaming**: SSE streaming with `[DONE]` sentinel
- **Multimodal**: Image input (Base64 → OpenAI `image_url` content-parts format) — Lite mode only
- **Model modes**: `lite` (Gemma4 E4B Q8, 32k ctx), `thinking` (Qwen 27B), `external` (cloud only)

### Memory System
- **Rolling summaries** (`SummaryService`): async, threshold-triggered (default 20 msgs), marks `is_summarized=1`
- **Structured memory** (`StructMem`): SPO triple extraction, 4 types (episodic, semantic, relational, procedural), consolidation every N extractions
- **Pinned memory**: user-scoped key-value facts with confidence scores
- SummaryService and StructMem are mutually exclusive per conversation

### RAG / Knowledge Base
- Knowledge cards with chunking (2000 chars/chunk default)
- Token-based relevance search (not vector embeddings — known limitation)
- Domain-based organization, trust levels, versioning, tags
- Endpoints: `POST /v1/knowledge/cards`, `GET /v1/knowledge/cards`, `POST /v1/knowledge/search`

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
- Per-key rate limiting (default 60 RPM), SQLite-backed in production
- IP-based auth failure tracking and blocking
- CORS restricted to allowed origins
- Security denial logging to `security.log`

### Other
- **Failure risk assessment**: risk scoring (0–1.0), log-only or action mode
- **CrewAI agents**: Researcher + Analyst crew (`POST /v1/crew/research`)
- **Usage tracking**: token counting, cost calculation, latency, provider/route logging
- **Prediction audit trail**: stock prediction records with outcome evaluation
- **Admin metrics**: `GET /v1/admin/usage`

## Technical Details

### Database Path
- `/app/data/ai_hub.db` (container) or `ai_hub.db` (local)

### Core Services
| Service | Purpose |
|---|---|
| `app/services/ai_service.py` | Orchestrates routing, memory injection, search, provider selection |
| `app/services/history_service.py` | Session/message persistence, `clear_user_history()` |
| `app/services/user_service.py` | User lookup/creation, session listing |
| `app/services/summary_service.py` | Async rolling summaries |
| `app/services/structmem_service.py` | Structured memory pipeline |
| `app/services/knowledge_ingestion_service.py` | RAG card creation + chunking |
| `app/services/knowledge_retrieval_service.py` | Token-based knowledge search |
| `app/services/tools/web_search_service.py` | Multi-backend web search |
| `app/services/failure_risk_service.py` | Failure risk scoring and actions |
| `app/services/providers/llama_cpp.py` | llama.cpp provider — vision messages use OpenAI content-parts format |
| `app/services/providers/openrouter.py` | Cloud fallback via OpenRouter |

### Security Layer
- `app/middleware/security.py`: API key auth, per-key rate limiting, denial logging
- `app/core/config.py`: All settings — `API_KEY`, `RATE_LIMIT_PER_MINUTE`, `ALLOWED_ORIGINS`, model/memory/search config

### Frontend
- `static/index.html`: API key prompt (localStorage), user-name resume, Lite-only image upload, `/clear` command support, streaming toggle, search toggle

## Security Defaults
- **API Key Header**: `X-API-KEY`
- **Default Rate Limit**: `60` requests per minute per API key
- **Security Log File**: `security.log`
- **Allowed Origins**: localhost variants, `https://htechlabsvn.com`, `https://api-aiserver.htechlabsvn.com`

## Build & Run Commands (Local Dev)
```bash
# Start server
./venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# Full stack (llama.cpp Q8 + API server)
./start.sh

# Run tests
./venv/bin/pytest tests/

# Security test slice
./venv/bin/pytest --no-cov tests/integration/test_security_middleware.py tests/unit/test_security_middleware.py tests/unit/test_config.py
```

## Build & Run Commands (Docker)
```bash
docker compose up --build -d
docker compose down
docker compose logs -f app
curl http://localhost:8000/health
```

## Q8 Benchmark Results (2026-04-27)

### Hardware
- Model: `gemma4:e4b` Q8, fully GPU-offloaded (~10 GB VRAM)
- Backend: llama.cpp single instance

### Optimal config (10 users × 10 questions = 100 requests, 0 errors)
```
parallel_slots=12, ctx_per_slot=4096 (total 49152), gpu_concurrency=12
```
| p50 | p95 | p99 | max | wall_time |
|---|---|---|---|---|
| 5.70s | 10.84s | 13.39s | 13.56s | 70.6s |

### Endurance (10 users × 40 questions = 400 requests, baseline p8/ctx65k)
| p50 | p95 | p99 | wall_time | errors |
|---|---|---|---|---|
| 8.44s | 11.27s | 11.90s | 347.6s | 0 |

### Key findings
- `ctx_per_slot=4096` is optimal for general chat — larger context (32k) increases latency with no quality benefit
- `gpu_concurrency = parallel_slots` — setting concurrency higher than slots causes latency spikes
- Cloud (OpenRouter) is 3× faster than local under heavy 5-user sequential load (p50 16s vs 45s)
- Dynamic cloud routing (queue-depth based) would outperform fixed every-N routing

## Known Limitations
- Knowledge search is **token-based** (not semantic/vector) — lower recall accuracy
- SQLite not suitable for high concurrent writes at scale
- Single llama.cpp instance — no horizontal scaling
- No real-time queue depth visibility in UI
- Two large models (e4b + qwen3.5:9b) cannot stay resident simultaneously on 16GB GPU

## Next TODOs
- Replace token-based knowledge search with vector embeddings (pgvector or Chroma)
- Dynamic cloud routing: route to OpenRouter when local queue depth > threshold
- PostgreSQL migration for concurrent-write scalability
- Queue wait time display in UI
- Per-project context size config instead of one global `LITE_NUM_CTX`

## File Structure (key files)
```
app/
├── core/config.py              # All settings
├── core/database.py            # SQLite schema + migrations
├── models/chat.py              # ChatRequest, ChatResponse, Message
├── routes/chat.py              # POST /v1/chat
├── routes/users.py             # GET/DELETE /v1/users/{user_name}/...
├── routes/knowledge.py         # /v1/knowledge/*
├── routes/memory.py            # GET /v1/memory
├── routes/admin.py             # GET /v1/admin/usage
├── middleware/security.py      # Auth + rate limiting
├── services/ai_service.py      # Core orchestrator
├── services/providers/
│   ├── llama_cpp.py            # Local provider (vision: content-parts format)
│   └── openrouter.py           # Cloud provider
├── services/tools/
│   └── web_search_service.py   # Multi-backend search
scripts/
├── start_lite_q8.sh            # Launch llama.cpp Q8
├── perf_hybrid_test.py         # Hybrid local+cloud benchmark
├── autotune_q8_multiuser.py    # Multi-config autotune sweep
static/
├── index.html                  # Main chat UI
└── admin.html                  # Admin dashboard
reports/                        # Benchmark results and notes
```
