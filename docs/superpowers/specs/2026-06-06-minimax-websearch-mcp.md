# MiniMax WebSearch MCP Integration — Design

**Date:** 2026-06-06
**Status:** Approved
**Author:** Brainstorming session with user
**Related:** `docs/superpowers/specs/2026-06-05-12b-quantization-optimization-design.md`

---

## 1. Background & Motivation

Hiện tại ai-hub có 2 cơ chế search:
- `/search: <query>` prefix → route đến cloud provider (OpenRouter/MiniMax) với `:online` plugin
- `enable_search=true` hoặc message có `?` → gọi `WebSearchService` local (Google → DDGS → DuckDuckGo → Bing)

**Vấn đề với WebSearchService local:**
- DDGS (DuckDuckGo Search) thường xuyên bị rate limit / trả 0 results
- Google Custom Search yêu cầu API key + CX config
- Bing HTML parse dễ vỡ khi Microsoft đổi layout
- Vietnamese domain quality scoring hoạt động không nhất quán

**Giải pháp: MiniMax WebSearch MCP** — package chính thức từ MiniMax, dùng Token Plan credits để gọi web search chất lượng cao, trả về citations chuẩn. Đã verify qua docs tại https://platform.minimax.io/docs/token-plan/mcp-guide.

**Quyết định:** Thay thế hoàn toàn WebSearchService local. `/search:` prefix + `?` auto-detect đều dùng MiniMax MCP.

---

## 2. Architecture

```
ai-hub (FastAPI on port 8000)
├── Startup phase
│   ├── Install uvx (if missing) via curl https://astral.sh/uv/install.sh
│   └── spawn `uvx minimax-coding-plan-mcp` subprocess
│       ├── stdin/stdout pipes for JSON-RPC
│       ├── env: MINIMAX_API_KEY=<key>, MINIMAX_API_HOST=https://api.minimax.io
│       └── MCP server runs in background process
│
├── Request lifecycle
│   ├── User sends "Thời tiết Hà Nội hôm nay?" (has ?)
│   │   OR sends "/search: thời tiết Hà Nội"
│   ├── ai_service._build_search_context(query)
│   │   └── calls MiniMaxWebSearchClient.search(query)
│   │       └── JSON-RPC tools/call {"name": "web_search", "arguments": {"query": ...}}
│   │       └── parses results array from MCP response
│   ├── Inject results as system context block
│   └── LLM generates response with citations
│
└── Error handling
    ├── subprocess crash → auto-restart once
    ├── MCP timeout (8s) → log + return "Web search unavailable" to LLM
    └── persistent failure → disable MCP for 5 min (circuit breaker)
```

---

## 3. Components

### 3.1 New: `app/services/mcp/minimax_websearch.py`

Single file containing:
- `MiniMaxMCPClient` class:
  - `__init__(api_key, base_url, command="uvx", args=["minimax-coding-plan-mcp", "-y"], timeout=8.0)`
  - `start()` — spawn subprocess, handshake (initialize request/response)
  - `search(query: str) -> list[dict]` — single tools/call invocation
  - `stop()` — graceful subprocess termination
  - `is_healthy() -> bool` — for health checks
- `JsonRpcFramer` helper (with newline-delimited JSON protocol per MCP spec)
- `MCPError` exception class

### 3.2 New: `app/services/mcp/__init__.py`

Empty package init.

### 3.3 Modified: `app/services/ai_service.py`

- Remove `from app.services.tools.web_search_service import WebSearchService`
- Constructor: take `MiniMaxMCPClient` instead of `WebSearchService`
- `_build_search_context()`: call `client.search(query)` instead of `web_search.search(query)`
- Result format: same (list of `{url, title, snippet, score}` dicts) for backwards compat with prompt template

### 3.4 Modified: `app/main.py`

- Remove `from app.services.tools.web_search_service import WebSearchService`
- Add `from app.services.mcp.minimax_websearch import MiniMaxMCPClient`
- Startup: install uvx if missing + start MCP client
- Shutdown: stop MCP client (terminate subprocess)
- Pass MCP client to ai_service

### 3.5 Modified: `app/core/config.py`

- Remove `enable_web_search_tool`, `web_search_max_results`, `web_search_timeout_seconds`, `google_search_cx`, `web_search_*` settings
- Add:
  - `minimax_mcp_enabled: bool = Field(default=True, alias="MINIMAX_MCP_ENABLED")`
  - `minimax_mcp_command: str = Field(default="uvx", alias="MINIMAX_MCP_COMMAND")`
  - `minimax_mcp_args: list[str] = Field(default_factory=lambda: ["minimax-coding-plan-mcp", "-y"], alias="MINIMAX_MCP_ARGS")`
  - `minimax_mcp_timeout_seconds: float = Field(default=8.0, gt=0, alias="MINIMAX_MCP_TIMEOUT_SECONDS")`
  - `minimax_mcp_max_results: int = Field(default=5, alias="MINIMAX_MCP_MAX_RESULTS")`

### 3.6 Modified: `.env`

Add:
```
MINIMAX_MCP_ENABLED=true
MINIMAX_MCP_COMMAND=uvx
MINIMAX_MCP_ARGS=["minimax-coding-plan-mcp","-y"]
MINIMAX_MCP_TIMEOUT_SECONDS=8.0
MINIMAX_MCP_MAX_RESULTS=5
```

User-provided API key already in `MINIMAX_API_KEY`.

### 3.7 Deleted: `app/services/tools/web_search_service.py`

Plus `app/services/tools/__init__.py` (empty now, may stay).

### 3.8 Modified: `requirements.txt` (or `pyproject.toml`)

Remove:
- `ddgs` (was for DDGS)
- `lxml` (was for HTML parsing)
- `requests` (was for direct Bing/Google calls — verify if still used elsewhere first)

---

## 4. Data Flow

### 4.1 Subprocess spawn (startup)

```python
# In main.py startup
if settings.minimax_mcp_enabled:
    if not shutil.which("uvx"):
        logger.warning("uvx not found, installing...")
        install_uvx()  # runs curl https://astral.sh/uv/install.sh
    client = MiniMaxMCPClient(
        api_key=settings.minimax_api_key,
        base_url=settings.minimax_base_url,  # https://api.minimax.io
        command=settings.minimax_mcp_command,
        args=settings.minimax_mcp_args,
        timeout=settings.minimax_mcp_timeout_seconds,
    )
    await client.start()
    app.state.minimax_mcp = client
else:
    app.state.minimax_mcp = None
```

### 4.2 JSON-RPC framing

Per MCP spec (newline-delimited JSON over stdio):

```
→ Server: {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "ai-hub", "version": "1.0"}}}
← Client: {"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, "serverInfo": {"name": "minimax-coding-plan-mcp", "version": "x.y.z"}}}

→ Server: {"jsonrpc": "2.0", "method": "notifications/initialized"}
→ Server: {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
← Client: {"jsonrpc": "2.0", "id": 2, "result": {"tools": [{"name": "web_search", "description": "...", "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}]}}

→ Server: {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "web_search", "arguments": {"query": "thời tiết Hà Nội"}}}
← Client: {"jsonrpc": "2.0", "id": 3, "result": {"content": [{"type": "text", "text": "[{...search results JSON...}]"}], "isError": false}}
```

### 4.3 Search context injection (ai_service)

Same prompt template as before (no change to LLM-facing format):

```python
def _build_search_context(self, query: str) -> tuple[str | None, list[str]]:
    if not self._mcp or not self._settings.minimax_mcp_enabled:
        return None, []
    try:
        results = await self._mcp.search(query, max_results=self._settings.minimax_mcp_max_results)
    except Exception:
        logger.exception("MiniMax MCP search failed query=%s", query)
        return None, []
    if not results:
        return None, []
    payload = json.dumps(results, ensure_ascii=False)
    return (
        "### SYSTEM: WEB SEARCH CONTEXT ###\n"
        "The user explicitly requested web search. ...\n\n"
        f"{payload}",
        [r["url"] for r in results if r.get("url")],
    )
```

### 4.4 Error handling matrix

| Failure | Detection | Recovery |
|---------|-----------|----------|
| `uvx` not installed at startup | `shutil.which("uvx") is None` | Auto-install via `curl \| sh`, then retry spawn |
| Subprocess exits within 5s of spawn | subprocess returncode != 0 | Read stderr, log, mark MCP as disabled for 5 min |
| subprocess crash mid-session | `process.returncode` is set | Auto-restart once with backoff; if still crashes, circuit-break 5 min |
| MCP timeout (8s) | `asyncio.wait_for` on send/recv | Log warning, return None to ai_service → no context injected |
| API key invalid | MCP returns error response | Log error, surface "Web search unavailable" once per session |
| JSON-RPC parse error | `json.loads` raises | Skip malformed frame, log, continue |

### 4.5 Circuit breaker

```python
class MiniMaxMCPClient:
    def __init__(self):
        self._failure_count = 0
        self._circuit_open_until = 0  # unix timestamp
    
    async def search(self, query):
        if time.time() < self._circuit_open_until:
            raise MCPCircuitOpen("MCP disabled for 5 min after repeated failures")
        try:
            result = await self._do_search(query)
            self._failure_count = 0
            return result
        except Exception:
            self._failure_count += 1
            if self._failure_count >= 3:
                self._circuit_open_until = time.time() + 300
            raise
```

---

## 5. Testing

### 5.1 Unit tests (TDD)

`tests/unit/test_minimax_mcp.py`:
- `test_jsonrpc_frame_build_request` — verify JSON-RPC envelope shape
- `test_jsonrpc_frame_parse_response` — verify frame parser
- `test_mcp_client_search_returns_results` — mock subprocess with canned JSON-RPC responses
- `test_mcp_client_search_handles_timeout` — mock subprocess that never responds
- `test_mcp_client_search_handles_mcp_error` — mock subprocess returning isError: true
- `test_mcp_client_circuit_breaker_opens_after_3_failures`
- `test_mcp_client_start_uvx_missing_calls_install` (mock `shutil.which` + `subprocess.run`)

### 5.2 Integration smoke test

After implementation, run:
```bash
./start.sh
sleep 30  # let MCP subprocess start

API_KEY="..."
DOMAIN="http://localhost:8000"  # or public domain
# Test 1: explicit /search:
curl -X POST "$DOMAIN/v1/chat" \
  -H "X-API-KEY: $API_KEY" -H "Content-Type: application/json" \
  -d '{"project_id":"test","user_name":"hung","user_message":"/search: thời tiết Hà Nội hôm nay","max_tokens":300}'

# Test 2: auto-detect via ?
curl -X POST "$DOMAIN/v1/chat" \
  -H "X-API-KEY: $API_KEY" -H "Content-Type: application/json" \
  -d '{"project_id":"test","user_name":"hung","user_message":"Ai là tổng thống Mỹ hiện tại?","max_tokens":300}'

# Verify both responses include web search results with citations
```

---

## 6. File Structure

```
app/
├── services/
│   ├── mcp/                          NEW
│   │   ├── __init__.py
│   │   └── minimax_websearch.py      NEW (MCP client + subprocess manager)
│   └── tools/
│       └── web_search_service.py     DELETED
├── ai_service.py                     MODIFIED
├── main.py                           MODIFIED
└── core/
    └── config.py                     MODIFIED

tests/
├── unit/
│   └── test_minimax_mcp.py           NEW

.env                                  MODIFIED
requirements.txt                      MODIFIED
CLAUDE.md                             MODIFIED
docs/superpowers/specs/2026-06-06-minimax-websearch-mcp.md  THIS FILE
```

---

## 7. Time Budget

- Task 1 (install uvx + verify): 5 min
- Task 2 (write TDD tests for JsonRpcFramer + search): 30 min
- Task 3 (implement MiniMaxMCPClient + wire into main): 45 min
- Task 4 (modify ai_service to use MCP instead of WebSearchService): 15 min
- Task 5 (delete WebSearchService + cleanup deps): 15 min
- Task 6 (config.py + .env + CLAUDE.md): 15 min
- Task 7 (integration smoke test): 15 min
- **Total: ~2.5 hours**

---

## 8. Success Criteria

- [ ] uvx installed on server
- [ ] `minimax-coding-plan-mcp` package auto-downloaded by uvx on first spawn
- [ ] MCP subprocess starts on ai-hub startup
- [ ] `/search: <query>` returns response with web search citations
- [ ] `?` auto-detect also uses MCP (not WebSearchService)
- [ ] WebSearchService.py deleted, deps removed
- [ ] Circuit breaker opens after 3 consecutive failures
- [ ] All 8+ unit tests pass
- [ ] Integration smoke test shows 0 errors, real search results
- [ ] CLAUDE.md updated to reflect MiniMax MCP as primary search backend
- [ ] `.env` has MINIMAX_MCP_* settings + MINIMAX_API_KEY

---

## 9. Risk & Mitigation

| Risk | Mitigation |
|------|------------|
| `minimax-coding-plan-mcp` package unavailable on PyPI | uvx will fail loudly; fall back to WebSearchService as a Plan B (keep code commented out for rollback) |
| `uvx` install fails (no internet, no sudo) | Log error, mark MCP as disabled, continue startup without search |
| API key invalid / out of credits | MCP returns error → circuit breaker → user gets "Web search unavailable" message; ai-service continues with model knowledge |
| Subprocess hangs | asyncio timeout 8s + process.kill() on cleanup |
| Vietnamese search quality worse than local DDGS | A/B test with real Vietnamese queries; if poor, add fallback path |
| Python 3.x compatibility of `minimax-coding-plan-mcp` | Pin Python version in install instruction; check PyPI page before integration |

---

## 10. References

- MiniMax MCP docs: https://platform.minimax.io/docs/token-plan/mcp-guide
- MCP spec: https://modelcontextprotocol.io/specification/2024-11-05
- uv install: https://astral.sh/uv/install.sh
- Existing ai-hub WebSearchService: `app/services/tools/web_search_service.py` (to be deleted)
- ai_service search routing: `app/services/ai_service.py:420-460`
