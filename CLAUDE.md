# AI Hub Project Context

## Project Overview
Central router for per-project AI chat, optimized for Ollama (local) with multimodal support, user-scoped session resume, and local-first security controls.

## Current Access Points
- **Local UI/API**: `http://localhost:8000`
- **Public Domain**: `https://api-aiserver.htechlabsvn.com/`
- **Primary Site Origin**: `https://htechlabsvn.com`

## Recently Implemented Features (April 2026)
- **Local-Only Architecture**: Removed 9Router (cloud) dependencies for a cleaner, privacy-focused setup.
- **Lite Model Optimization**: Pre-configured `gemma4:e4b` with 32k context window.
- **Multimodal Support**: Frontend and backend support image input (Base64) for vision-capable models in Lite mode only.
- **User-Scoped Chat Resume**: Chat requests can include `user_name`, map to a persistent `user_id`, and resume prior sessions via `GET /v1/users/{user_name}/sessions`.
- **Visual Chatbot UI**: Root UI now supports user-name resume flow, Lite-only image upload, and browser-side API key prompt/storage for authenticated chatting.
- **Rolling Summary Memory Foundation**: Database and message pipeline now include `users`, `summaries`, and `is_summarized` support as the base for recursive rolling summaries.
- **Security Hardening**: Added API-key protection, per-key rate limiting, restricted CORS, and security denial logging to protect the local Ollama-backed server.
- **Dockerization**: Full containerization using Docker Compose (App + Ollama).
- **Database Persistence**: SQLite (`ai_hub.db`) stores chat sessions, users, and message history.

## Technical Details
- **Database Path**: `/app/data/ai_hub.db` (in container) or `ai_hub.db` (local).
- **Core Services**:
    - `app/services/ai_service.py`: Orchestrates Ollama calls with dynamic `num_ctx` based on mode (Normal: 8k, Lite: 32k), user-scoped sessions, and Lite-only image forwarding.
    - `app/services/history_service.py`: Persists sessions/messages with `user_id` and `is_summarized` support.
    - `app/services/user_service.py`: Resolves `user_name`, creates users, and lists resumable sessions.
    - `app/services/providers/ollama.py`: Handles OpenAI-compatible communication with local Ollama.
- **Security Layer**:
    - `app/middleware/security.py`: Enforces `X-API-KEY`, per-key rate limiting (`429`), and denial logging.
    - `app/core/logging.py`: Writes security denials to `security.log` through the `app.security` logger.
    - `app/core/config.py`: Holds `API_KEY`, `RATE_LIMIT_PER_MINUTE`, `SECURITY_LOG_FILE`, and `ALLOWED_ORIGINS` settings.
- **Frontend Contract**:
    - `static/index.html`: Prompts for API key and user name, stores them in `localStorage`, and attaches `X-API-KEY` to chat/session-resume requests.

## Security Defaults
- **API Key Header**: `X-API-KEY`
- **Default Rate Limit**: `5` requests per minute per API key
- **Security Log File**: `security.log`
- **Allowed Origins**:
    - `http://localhost`
    - `http://localhost:3000`
    - `http://localhost:5173`
    - `http://127.0.0.1:8000`
    - `https://htechlabsvn.com`
    - `https://api-aiserver.htechlabsvn.com`

## Build & Run Commands (Docker - Recommended)
- **Start Everything**: `docker compose up --build -d`
- **Stop Everything**: `docker compose down`
- **View Logs**: `docker compose logs -f app`
- **Health Check**: `curl http://localhost:8000/health`

## Build & Run Commands (Local Dev)
- **Start Server**: `./venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload`
- **Run Tests**: `./venv/bin/pytest tests/`
- **Security Test Slice**: `./venv/bin/pytest --no-cov tests/integration/test_security_middleware.py tests/unit/test_security_middleware.py tests/unit/test_config.py`

## File Structure Changes
- `docker-compose.yml` & `Dockerfile`: Infrastructure as code.
- `static/index.html`: UI with user-name resume flow, Lite-only image handling, and API key prompt/header injection.
- `app/core/config.py`: Local app settings, model settings, CORS origins, and security configuration.
- `app/core/logging.py`: Structured console logging plus `security.log` file output.
- `app/middleware/security.py`: API key auth and rate limiting middleware.
- `app/models/chat.py`: Added `images`, `user_name`, and `user_id` support in the chat contract.
- `app/models/user.py`: Typed records for users and resumable sessions.
- `app/routes/users.py`: Session resume endpoint by user name.
- `app/services/history_service.py`: Session/message persistence with summary-related flags.
- `app/services/user_service.py`: User lookup/creation and session listing.
- `tests/integration/test_security_middleware.py`: Coverage for auth, rate limit, CORS, and security logging.
- `tests/integration/test_user_sessions_endpoint.py`: Coverage for user-scoped resume flow.
- `tests/unit/test_security_middleware.py`: Unit coverage for in-memory rate limiting.
- `tests/unit/test_history_service.py`: Unit coverage for user/session persistence and unsummarized filtering.

## Rolling Summary Memory Status
- **Implemented now**:
    - `users` table
    - `summaries` table
    - `messages.is_summarized`
    - user/session linkage required for long-term memory
- **Planned next**:
    - recursive summary generation
    - summarize oldest unsummarized messages when threshold is reached
    - inject rolling summary into the Ollama context window
    - run summary generation asynchronously so chat requests stay responsive

## Next TODOs
- Implement `SummaryService` to generate recursive rolling summaries per `user_id` and `project_id`.
- Trigger summarization when unsummarized message count reaches the chosen threshold, then mark summarized rows with `is_summarized = 1`.
- Build a context-window assembler that injects the latest stored summary before recent live messages.
- Run summary generation asynchronously so the main chat request stays fast.
- Add integration and unit coverage for summary thresholds, recursive summary replacement, and context assembly.
- Add a small UI control to replace or clear the saved browser API key without manually clearing `localStorage`.

## Notes
- The browser UI now requires an API key before chat requests will succeed.
- If API key or user name changes are needed in the browser, they are stored in `localStorage`.
- Normal mode does not expose image upload; image requests are restricted to Lite mode.
