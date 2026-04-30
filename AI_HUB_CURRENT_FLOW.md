# AI Hub current flow note

Last updated: 2026-04-30

## Finalized local Lite profile

AI Hub is currently optimized around a local-first Lite profile using llama.cpp:

- Local provider: `llama_cpp`
- Local API: `http://localhost:8080/v1`
- AI Hub API/UI: `http://localhost:8000`
- Lite model alias: `local-gemma4-e4b-q8`
- Thinking/default model alias: `local-qwen3.6-27b`
- Lite local capacity target: 8 concurrent users

Current key runtime values in `.env`:

```env
LOCAL_PROVIDER=llama_cpp
LLAMA_CPP_BASE_URL=http://localhost:8080
LLAMA_CPP_OPENAI_URL=http://localhost:8080/v1

DEFAULT_MODEL=local-qwen3.6-27b
LITE_MODEL=local-gemma4-e4b-q8
SUMMARY_MODEL=local-gemma4-e4b-q8
CREW_MODEL=local-gemma4-e4b-q8

LITE_NUM_CTX=8192
DEFAULT_NUM_CTX=8192
GPU_CONCURRENCY=8
HYBRID_LOCAL_QUEUE_TIMEOUT_SECONDS=30

MAX_HISTORY_MESSAGES=20
LITE_MAX_HISTORY_MESSAGES=20
SUMMARY_THRESHOLD=20
SUMMARY_CONTEXT_TOKEN_THRESHOLD=4000
SUMMARY_CONCURRENCY=1
```

Recommended llama-server Lite startup profile:

```bash
PARALLEL=8 CTX_SIZE=65536 scripts/start_lite_q8.sh
```

This gives 8 llama.cpp slots with 8k context per slot.

## Main request flow

Primary endpoint:

```text
POST /v1/chat
```

Request model fields are defined in:

```text
app/models/chat.py
```

Important request fields:

- `project_id`
- `tenant_id`
- `user_name`
- `user_message`
- `session_id`
- `model_mode`
- `provider`
- `allow_external`
- `stream`
- `enable_search`

Main orchestration is in:

```text
app/services/ai_service.py
```

High-level flow:

1. Resolve/create user from `user_name`.
2. Resolve/create chat session.
3. Load latest rolling summary for the user/project/tenant.
4. Load recent raw session history.
5. Inject pinned memory / structured memory if enabled.
6. Assemble provider messages.
7. Route to local llama.cpp by default.
8. Save user and assistant messages after successful response.
9. Schedule memory/summary background jobs.
10. Record usage event.

## Model mode behavior

Current intent:

- `model_mode=lite`
  - Uses `LITE_MODEL=local-gemma4-e4b-q8`.
  - Main production/default chatbot path.
- `model_mode=thinking` or legacy `normal`
  - Uses `DEFAULT_MODEL=local-qwen3.6-27b`.
  - Requires OS-level llama-server switch to Qwen; UI mode alone does not load/unload GPU models.
- `model_mode=external` or `provider=cloud`
  - Uses OpenRouter if allowed.

Operational scripts:

```text
scripts/start_lite_q8.sh
scripts/start_thinking_qwen.sh
scripts/stop_local_model.sh
```

Important: the UI/API model mode only chooses model aliases. Actual GPU model switching is done by the start/stop scripts.

## History and summary logic

The stability fix was to stop sending long raw history into llama.cpp.

Current policy:

- Keep only the latest 20 raw history messages in model context.
- Also keep a rolling summary for older conversation state.
- Trigger summary generation when either:
  - unsummarized message count reaches `SUMMARY_THRESHOLD=20`, or
  - estimated unsummarized context reaches `SUMMARY_CONTEXT_TOKEN_THRESHOLD=4000` tokens.

Relevant files:

```text
app/services/ai_service.py
app/services/summary_service.py
app/services/history_service.py
app/core/config.py
```

Why this matters:

- Before this change, long sessions caused prompts to grow to thousands of tokens per request.
- Under 8 concurrent users, llama-server accumulated large context/cache pressure and could die.
- After limiting raw history to 20 and summarizing older context, long-session tests returned to 100% OK.

Current assembly behavior:

- System prompt and memory blocks are added first.
- Latest summary is injected when available and StructMem is not enabled.
- Last 20 raw history messages are included.
- Current user message is appended.

StructMem note:

- `ENABLE_STRUCTMEM` is currently not set in `.env`, so rolling summary is the active long-term compression path.
- If StructMem is enabled later, review summary coexistence because the code treats StructMem and SummaryService as mutually exclusive background pipelines.

## Local concurrency and queue behavior

Current local concurrency:

```env
GPU_CONCURRENCY=8
HYBRID_LOCAL_QUEUE_TIMEOUT_SECONDS=30
```

Meaning:

- AI Hub allows up to 8 local in-flight model calls.
- Extra requests wait for the local GPU semaphore.
- If queue wait exceeds 30s and cloud is allowed, the request can fall back to OpenRouter.

For local-only benchmarks, cloud should be disabled:

```bash
AIHUB_LOADTEST_ALLOW_EXTERNAL=false
```

This keeps benchmark numbers honest and measures only local hardware/model capacity.

## Cloud fallback status

OpenRouter configuration currently exists:

```env
OPENROUTER_ENABLED=true
OPENROUTER_MODEL=openai/gpt-oss-20b:free
OPENROUTER_FALLBACK_MODELS=["openrouter/auto"]
OPENROUTER_ALLOWED_PROJECTS=["test","doden"]
OPENROUTER_DENIED_PROJECTS=["vehix"]
EXTERNAL_LLM_DEFAULT_ALLOWED=false
```

OpenRouter provider now sends both a primary model and a fallback model list:

```json
{
  "model": "openai/gpt-oss-20b:free",
  "models": ["openai/gpt-oss-20b:free", "openrouter/auto"]
}
```

Fallback can happen for non-streaming local requests when:

- local queue is saturated,
- local queue wait times out,
- local provider becomes unavailable,
- VRAM/local provider error occurs,
- request/project/key policy allows external.

Current caveat:

- Manual verification showed the fallback route reaches OpenRouter, but OpenRouter returned `402 Insufficient credits` for the current account/key.
- Therefore cloud fallback logic exists, but production cloud fallback is not ready until the OpenRouter account/key has usable quota/credits.
- For now, capacity claims should be based on local-only results.

Security/privacy policy:

- `vehix` is denied from OpenRouter fallback.
- `test` and `doden` are allowed.
- External fallback is not default unless request explicitly sets `allow_external=true` or defaults are changed.

## Timeout/retry behavior

Timeout protection added:

- Provider timeout retries once.
- If retry still fails, API returns a `504` message telling the client to try again and that the prompt was not saved as answered.

Relevant files:

```text
app/services/ai_service.py
app/main.py
```

This prevents silent prompt loss: a timeout should be visible to frontend/user, and the user can retry.

## Current best benchmark evidence

### Stable 8-user, 20-question local test

Report:

```text
reports/q8_8slot_ctx8_gpu8_8u20_localonly_history20_summary4k
```

Config:

- Q8 `8 slot × 8k`
- `GPU_CONCURRENCY=8`
- `MAX_HISTORY_MESSAGES=20`
- `LITE_MAX_HISTORY_MESSAGES=20`
- `SUMMARY_THRESHOLD=20`
- `SUMMARY_CONTEXT_TOKEN_THRESHOLD=4000`
- `SUMMARY_CONCURRENCY=1`
- 8 users
- 20 questions per user
- max concurrency 8
- cloud off

Result:

```text
160/160 OK
0 errors
wall time: 196.3s
p50: 8.663s
p95: 15.128s
p99: 16.168s
max: 16.841s
memory checks: 8/8
local route: 160
fallback: 0
Q8 server survived after test
```

### Mixed long-session local test

Report:

```text
reports/q8_8slot_ctx8_gpu8_mixed_4u40_4u20_history20_summary4k
```

Config:

- Q8 `8 slot × 8k`
- `GPU_CONCURRENCY=8`
- 8 concurrent users
- first 4 users: 40 questions each
- remaining 4 users: 20 questions each
- total 240 questions
- cloud off

Result:

```text
240/240 OK
0 errors
wall time: 333.7s
p50: 8.098s
p95: 14.031s
p99: 15.465s
max: 19.219s
memory checks: 8/8
local route: 240
fallback: 0
Q8 server survived after test
```

This is the strongest current capacity proof.

## Capacity statement for team

Recommended wording:

> AI Hub Lite local Q8 is currently validated for 8 concurrent users on local hardware. In local-only tests, 8 simultaneous users with long conversations completed successfully: 4 users continued to 40 questions and 4 users stopped at 20 questions, for 240 total requests, with 100% OK, memory checks 8/8, and the local model remaining alive. This stability depends on keeping raw history capped at 20 messages and using rolling summaries once conversation history grows.

Avoid saying:

> Unlimited users or unlimited long conversations.

Better nuance:

> More than 8 users can use the system if their requests are spread over time. The hard local concurrent generation capacity should be treated as 8. Production should still keep queueing, retry, monitoring, and eventually cloud fallback once OpenRouter quota is fixed.

## Known operational caveats

1. Do not benchmark tuning with cloud enabled unless specifically testing production fallback.
2. Avoid `source .env` for running Uvicorn because JSON-array env values can be mangled by shell parsing.
3. Start app in a way that lets Pydantic read `.env` directly, or export JSON array envs carefully.
4. There is a recurring port race when restarting Uvicorn; verify active process with:

```bash
ss -ltnp 'sport = :8000'
```

5. Verify Q8 with:

```bash
ss -ltnp 'sport = :8080'
curl http://localhost:8080/v1/models
```

6. `scripts/start_lite_q8.sh` default values are conservative (`PARALLEL=2`, `CTX_SIZE=16384`), so production/local benchmark startup should explicitly pass:

```bash
PARALLEL=8 CTX_SIZE=65536 scripts/start_lite_q8.sh
```

## Useful commands

Start Lite Q8 finalized profile:

```bash
PARALLEL=8 CTX_SIZE=65536 scripts/start_lite_q8.sh
```

Stop local model:

```bash
scripts/stop_local_model.sh
```

Run local-only 8 users × 20 questions:

```bash
AIHUB_API_KEY="$(./venv/bin/python - <<'PY'
from dotenv import dotenv_values
print(dotenv_values('.env')['API_KEY'])
PY
)" \
AIHUB_LOADTEST_URL=http://localhost:8000 \
AIHUB_LOADTEST_USERS=8 \
AIHUB_LOADTEST_QUESTIONS=20 \
AIHUB_LOADTEST_MAX_CONCURRENCY=8 \
AIHUB_LOADTEST_TIMEOUT=240 \
AIHUB_LOADTEST_ALLOW_EXTERNAL=false \
AIHUB_LOADTEST_MODEL_MODE=lite \
AIHUB_LOADTEST_REPORT=q8_8slot_ctx8_gpu8_8u20_localonly_history20_summary4k \
./venv/bin/python scripts/repeated_topic_loadtest.py
```

Run mixed 4 users × 40 + 4 users × 20:

```bash
AIHUB_API_KEY="$(./venv/bin/python - <<'PY'
from dotenv import dotenv_values
print(dotenv_values('.env')['API_KEY'])
PY
)" \
AIHUB_LOADTEST_URL=http://localhost:8000 \
AIHUB_LOADTEST_USERS=8 \
AIHUB_LOADTEST_QUESTIONS=20 \
AIHUB_LOADTEST_EXTRA_QUESTIONS_USERS=4 \
AIHUB_LOADTEST_EXTRA_QUESTIONS=20 \
AIHUB_LOADTEST_MAX_CONCURRENCY=8 \
AIHUB_LOADTEST_TIMEOUT=300 \
AIHUB_LOADTEST_ALLOW_EXTERNAL=false \
AIHUB_LOADTEST_MODEL_MODE=lite \
AIHUB_LOADTEST_REPORT=q8_8slot_ctx8_gpu8_mixed_4u40_4u20_history20_summary4k \
./venv/bin/python scripts/repeated_topic_loadtest.py
```

## Files changed in this tuning phase

Important changed files:

```text
.env
app/core/config.py
app/main.py
app/services/ai_service.py
app/services/providers/openrouter.py
app/services/summary_service.py
scripts/repeated_topic_loadtest.py
tests/unit/test_config.py
tests/unit/test_openrouter_provider.py
tests/unit/test_ai_service_openrouter.py
tests/unit/test_summary_service.py
```

Important generated/updated reports:

```text
reports/q8_8slot_ctx8_gpu8_8u20_localonly_history20_summary4k
reports/q8_8slot_ctx8_gpu8_mixed_4u40_4u20_history20_summary4k
```
