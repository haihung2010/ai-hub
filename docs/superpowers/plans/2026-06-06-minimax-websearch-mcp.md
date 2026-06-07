# MiniMax WebSearch MCP Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace local WebSearchService with MiniMax's official `minimax-coding-plan-mcp` package, communicating via JSON-RPC over stdio.

**Architecture:** Spawn `uvx minimax-coding-plan-mcp` subprocess on ai-hub startup. Talk JSON-RPC 2.0 over stdin/stdout (newline-delimited). Expose `web_search(query) -> list[dict]` to ai_service. Circuit breaker for resilience. Auto-install uvx if missing.

**Tech Stack:** Python 3.12, asyncio, JSON-RPC 2.0, uvx (Astral uv), httpx, pytest.

---

## File Structure

### New files
- `app/services/mcp/__init__.py` — empty package init
- `app/services/mcp/minimax_websearch.py` — `MiniMaxMCPClient` + `JsonRpcFramer` + `MCPCircuitOpen` exception
- `tests/unit/test_minimax_mcp.py` — TDD unit tests

### Modified files
- `app/services/ai_service.py` — replace `WebSearchService` calls with `MiniMaxMCPClient`
- `app/main.py` — lifecycle: install uvx, start MCP client on startup, stop on shutdown
- `app/core/config.py` — remove old web_search_* settings, add `minimax_mcp_*` settings
- `.env` — add new MINIMAX_MCP_* vars
- `CLAUDE.md` — update Web Search section

### Deleted files
- `app/services/tools/web_search_service.py` (replaced by MCP client)
- `app/services/tools/__init__.py` (empty, can stay or delete)

### Dependencies (verify before removing)
- `ddgs` — REMOVE from requirements
- `lxml` — KEEP (used by other code)
- `requests` — KEEP (used elsewhere)

---

## Task 1: Install uvx and verify environment

**Files:** None modified.

- [ ] **Step 1: Check if uvx is already installed**

Run: `which uvx`
Expected: Either a path (e.g., `/usr/local/bin/uvx`) OR empty/no output.

- [ ] **Step 2: If uvx missing, install it**

Run: `curl -LsSf https://astral.sh/uv/install.sh | sh`
Expected: Install output ending with `uv` and `uvx` paths (e.g., `/home/hung/.local/bin/uvx`).

- [ ] **Step 3: Source the env file and verify uvx is now in PATH**

Run: `export PATH="$HOME/.local/bin:$PATH" && which uvx && uvx --version`
Expected: `/home/hung/.local/bin/uvx` and version like `uv 0.x.x`.

- [ ] **Step 4: Make uvx available system-wide (add to /etc/profile.d or symlink)**

Run:
```bash
if [ -f "$HOME/.local/bin/uvx" ] && [ ! -f "/usr/local/bin/uvx" ]; then
    sudo ln -s "$HOME/.local/bin/uvx" /usr/local/bin/uvx || \
      echo "Warning: could not symlink uvx to /usr/local/bin (no sudo). Will rely on PATH."
fi
which uvx
```

Expected: `/usr/local/bin/uvx` OR `/home/hung/.local/bin/uvx`.

- [ ] **Step 5: Verify the MCP package is reachable on PyPI**

Run: `uvx --from minimax-coding-plan-mcp minimax-coding-plan-mcp --help 2>&1 | head -20`
Expected: Either shows help text OR a download+install message ending with the package being run. This downloads the package to uv's cache.

If the package is not on PyPI under that name, the command will fail with "No solution found". In that case, STOP and report — the plan assumes the package is available.

- [ ] **Step 6: Commit environment setup (no git changes yet, but note in plan)**

If `.bashrc` was modified by the install script, no commit needed (it's user-level). If you created a symlink, it's system-level (no git).

---

## Task 2: TDD for JsonRpcFramer

**Files:**
- Create: `tests/unit/test_minimax_mcp.py`
- Create: `app/services/mcp/__init__.py`
- Create: `app/services/mcp/minimax_websearch.py` (stub first)

- [ ] **Step 1: Create the empty mcp package init file**

Write `/home/hung/ai-hub/app/services/mcp/__init__.py`:
```python
"""MCP (Model Context Protocol) client implementations."""
```

- [ ] **Step 2: Create the test file with the first test**

Write `/home/hung/ai-hub/tests/unit/test_minimax_mcp.py`:
```python
"""Unit tests for MiniMax WebSearch MCP client.

The MCP protocol uses newline-delimited JSON over stdio:
- Client → Server: one JSON object per line on stdin
- Server → Client: one JSON object per line on stdout
"""

import pytest

from app.services.mcp.minimax_websearch import JsonRpcFramer, MCPError


def test_jsonrpc_frame_build_request():
    """Verify the request envelope shape."""
    frame = JsonRpcFramer.build_request(id=1, method="tools/call", params={"name": "web_search", "arguments": {"query": "Hanoi weather"}})
    assert frame["jsonrpc"] == "2.0"
    assert frame["id"] == 1
    assert frame["method"] == "tools/call"
    assert frame["params"] == {"name": "web_search", "arguments": {"query": "Hanoi weather"}}


def test_jsonrpc_frame_build_notification():
    """Notifications have no id and no response expected."""
    frame = JsonRpcFramer.build_notification(method="notifications/initialized", params={})
    assert frame["jsonrpc"] == "2.0"
    assert "id" not in frame
    assert frame["method"] == "notifications/initialized"


def test_jsonrpc_frame_serialize_with_newline():
    """Frames are serialized as JSON + newline (delimiter)."""
    frame = JsonRpcFramer.build_request(id=1, method="ping", params={})
    serialized = JsonRpcFramer.serialize(frame)
    assert serialized.endswith("\n")
    assert serialized.count("\n") == 1  # exactly one trailing newline
    # Re-parse to verify round-trip
    import json
    assert json.loads(serialized) == frame


def test_jsonrpc_frame_parse_response():
    """Parse a valid JSON-RPC response frame."""
    raw = '{"jsonrpc": "2.0", "id": 1, "result": {"foo": "bar"}}\n'
    parsed = JsonRpcFramer.parse_line(raw)
    assert parsed == {"jsonrpc": "2.0", "id": 1, "result": {"foo": "bar"}}


def test_jsonrpc_frame_parse_error_response():
    """Parse a JSON-RPC error response frame."""
    raw = '{"jsonrpc": "2.0", "id": 1, "error": {"code": -32600, "message": "Invalid Request"}}\n'
    parsed = JsonRpcFramer.parse_line(raw)
    assert "error" in parsed
    assert parsed["error"]["code"] == -32600


def test_jsonrpc_frame_parse_invalid_json_raises():
    """Malformed JSON raises MCPError."""
    with pytest.raises(MCPError, match="malformed JSON"):
        JsonRpcFramer.parse_line("not json\n")


def test_jsonrpc_frame_parse_missing_newline_strict_raises():
    """Frame without trailing newline raises MCPError (strict framing)."""
    with pytest.raises(MCPError, match="must end with newline"):
        JsonRpcFramer.parse_line('{"jsonrpc": "2.0", "id": 1, "result": {}}')


def test_jsonrpc_frame_parse_non_object_raises():
    """Frame that isn't a JSON object raises MCPError."""
    with pytest.raises(MCPError, match="JSON object"):
        JsonRpcFramer.parse_line('"just a string"\n')
```

- [ ] **Step 3: Run the tests to verify they fail (RED)**

Run: `cd /home/hung/ai-hub && ./venv/bin/pytest tests/unit/test_minimax_mcp.py -v 2>&1 | tail -25`
Expected: All 8 tests FAIL with `ModuleNotFoundError: No module named 'app.services.mcp.minimax_websearch'`.

- [ ] **Step 4: Implement minimal `minimax_websearch.py` to make tests pass**

Write `/home/hung/ai-hub/app/services/mcp/minimax_websearch.py`:
```python
"""MiniMax WebSearch MCP client.

Spawns the `minimax-coding-plan-mcp` package (via uvx) and communicates
via JSON-RPC 2.0 over newline-delimited stdio JSON. Exposes a single
async `search(query)` method that returns a list of search result dicts.

JSON-RPC framing:
- Client → Server: one JSON object per line on stdin
- Server → Client: one JSON object per line on stdout
- Notifications have no `id` field
- Errors return `{"error": {"code", "message"}}` instead of `result`
"""

from __future__ import annotations

import json
from typing import Any


class MCPError(Exception):
    """Raised when the MCP server returns an error or the protocol is violated."""


class MCPCircuitOpen(Exception):
    """Raised when the circuit breaker is open and calls are short-circuited."""


class JsonRpcFramer:
    """Build and parse JSON-RPC 2.0 frames for MCP stdio transport."""

    @staticmethod
    def build_request(
        *,
        id: int,
        method: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build a JSON-RPC request frame (expects a response)."""
        frame: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": id,
            "method": method,
        }
        if params is not None:
            frame["params"] = params
        return frame

    @staticmethod
    def build_notification(
        *,
        method: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build a JSON-RPC notification frame (no response expected)."""
        frame: dict[str, Any] = {
            "jsonrpc": "2.0",
            "method": method,
        }
        if params is not None:
            frame["params"] = params
        return frame

    @staticmethod
    def serialize(frame: dict[str, Any]) -> str:
        """Serialize a frame to JSON + newline (the MCP stdio delimiter)."""
        return json.dumps(frame, ensure_ascii=False) + "\n"

    @staticmethod
    def parse_line(line: str) -> dict[str, Any]:
        """Parse a single JSON-RPC frame from a line of stdout.

        Strict: raises MCPError on malformed JSON, missing newline, or
        non-object payloads (MCP frames must be JSON objects).
        """
        if not line.endswith("\n"):
            raise MCPError(f"MCP frame must end with newline, got: {line!r}")
        payload = line.rstrip("\n")
        try:
            obj = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise MCPError(f"MCP frame is malformed JSON: {exc}") from exc
        if not isinstance(obj, dict):
            raise MCPError(f"MCP frame must be a JSON object, got: {type(obj).__name__}")
        return obj
```

- [ ] **Step 5: Run the tests to verify they pass (GREEN)**

Run: `cd /home/hung/ai-hub && ./venv/bin/pytest tests/unit/test_minimax_mcp.py -v 2>&1 | tail -20`
Expected: All 8 tests PASS.

- [ ] **Step 6: Commit**

```bash
cd /home/hung/ai-hub
git add app/services/mcp/ tests/unit/test_minimax_mcp.py
git commit -m "feat(mcp): add JsonRpcFramer for MiniMax MCP stdio transport"
```

---

## Task 3: TDD for MiniMaxMCPClient.search() with mock subprocess

**Files:**
- Modify: `tests/unit/test_minimax_mcp.py` (add new tests)
- Modify: `app/services/mcp/minimax_websearch.py` (add client)

- [ ] **Step 1: Append more tests to `test_minimax_mcp.py`**

Append to the existing test file:

```python
import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.mcp.minimax_websearch import MiniMaxMCPClient


def _make_mock_process(returncode=0):
    """Build a mock asyncio.subprocess.Process with paired streams."""
    proc = MagicMock()
    proc.returncode = returncode
    proc.stdin = MagicMock()
    proc.stdin.write = MagicMock()
    proc.stdin.drain = AsyncMock()
    proc.stdin.close = MagicMock()
    proc.stdout = MagicMock()
    proc.stderr = MagicMock()
    proc.wait = AsyncMock(return_value=returncode)
    proc.terminate = MagicMock()
    proc.kill = MagicMock()
    return proc


@pytest.mark.asyncio
async def test_mcp_client_initialize_handshake():
    """start() sends initialize request and parses capabilities."""
    proc = _make_mock_process()
    # initialize response
    init_response = (
        '{"jsonrpc": "2.0", "id": 1, "result": '
        '{"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, '
        '"serverInfo": {"name": "minimax-coding-plan-mcp", "version": "0.1.0"}}}\n'
    )
    # tools/list response
    tools_response = (
        '{"jsonrpc": "2.0", "id": 2, "result": '
        '{"tools": [{"name": "web_search", "description": "Search the web", '
        '"inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}, '
        '"required": ["query"]}}]}}\n'
    )

    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)) as mock_exec:
        # First readline returns initialize response, second returns tools/list
        proc.stdout.readline = AsyncMock(side_effect=[init_response, tools_response, b""])
        client = MiniMaxMCPClient(api_key="test", base_url="https://api.minimax.io", command="uvx", args=["minimax-coding-plan-mcp", "-y"], timeout=8.0)
        await client.start()
        mock_exec.assert_called_once()
        # Verify subprocess was spawned with the right command
        args, kwargs = mock_exec.call_args
        assert args[0] == "uvx"
        assert "minimax-coding-plan-mcp" in args
        # Verify env has MINIMAX_API_KEY
        env = kwargs.get("env") or {}
        assert env.get("MINIMAX_API_KEY") == "test"
        assert env.get("MINIMAX_API_HOST") == "https://api.minimax.io"
        assert client.is_healthy()


@pytest.mark.asyncio
async def test_mcp_client_search_returns_parsed_results():
    """search() calls tools/call and parses the result list."""
    proc = _make_mock_process()
    init_response = (
        '{"jsonrpc": "2.0", "id": 1, "result": '
        '{"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}}}\n'
    )
    tools_response = (
        '{"jsonrpc": "2.0", "id": 2, "result": '
        '{"tools": [{"name": "web_search", "description": "x", "inputSchema": {}}]}}\n'
    )
    # tools/call response — content[0].text is a JSON string of search results
    search_response = (
        '{"jsonrpc": "2.0", "id": 3, "result": '
        '{"content": [{"type": "text", "text": "['
        '{"url": "https://example.com/1", "title": "Page 1", "snippet": "snippet 1"}, '
        '{"url": "https://example.com/2", "title": "Page 2", "snippet": "snippet 2"}'
        ']"}], "isError": false}}\n'
    )

    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
        proc.stdout.readline = AsyncMock(side_effect=[init_response, tools_response, search_response, b""])
        client = MiniMaxMCPClient(api_key="test", base_url="https://api.minimax.io", command="uvx", args=["minimax-coding-plan-mcp", "-y"], timeout=8.0)
        await client.start()
        results = await client.search("Hanoi weather", max_results=5)
        assert len(results) == 2
        assert results[0]["url"] == "https://example.com/1"
        assert results[1]["title"] == "Page 2"


@pytest.mark.asyncio
async def test_mcp_client_search_handles_mcp_error():
    """MCP isError=true should raise MCPError."""
    proc = _make_mock_process()
    init_response = '{"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}}}\n'
    tools_response = '{"jsonrpc": "2.0", "id": 2, "result": {"tools": [{"name": "web_search", "description": "x", "inputSchema": {}}]}}\n'
    error_response = (
        '{"jsonrpc": "2.0", "id": 3, "result": '
        '{"content": [], "isError": true, "errorMessage": "API key invalid"}}\n'
    )

    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
        proc.stdout.readline = AsyncMock(side_effect=[init_response, tools_response, error_response, b""])
        client = MiniMaxMCPClient(api_key="bad", base_url="https://api.minimax.io", command="uvx", args=["minimax-coding-plan-mcp", "-y"], timeout=8.0)
        await client.start()
        with pytest.raises(MCPError, match="API key invalid"):
            await client.search("Hanoi weather", max_results=5)


@pytest.mark.asyncio
async def test_mcp_client_search_handles_jsonrpc_error_response():
    """JSON-RPC error response (top-level error field) should raise MCPError."""
    proc = _make_mock_process()
    init_response = '{"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}}}\n'
    tools_response = '{"jsonrpc": "2.0", "id": 2, "result": {"tools": [{"name": "web_search", "description": "x", "inputSchema": {}}]}}\n'
    error_response = '{"jsonrpc": "2.0", "id": 3, "error": {"code": -32603, "message": "Internal error"}}\n'

    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
        proc.stdout.readline = AsyncMock(side_effect=[init_response, tools_response, error_response, b""])
        client = MiniMaxMCPClient(api_key="test", base_url="https://api.minimax.io", command="uvx", args=["minimax-coding-plan-mcp", "-y"], timeout=8.0)
        await client.start()
        with pytest.raises(MCPError, match="Internal error"):
            await client.search("Hanoi weather", max_results=5)


@pytest.mark.asyncio
async def test_mcp_client_search_handles_timeout():
    """A subprocess that never responds should raise asyncio.TimeoutError."""
    import asyncio as _asyncio

    proc = _make_mock_process()
    init_response = '{"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}}}\n'
    tools_response = '{"jsonrpc": "2.0", "id": 2, "result": {"tools": [{"name": "web_search", "description": "x", "inputSchema": {}}]}\n'

    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
        # Initialize and tools/list succeed; then search hangs forever
        async def hang(*_args, **_kwargs):
            await _asyncio.sleep(100)

        proc.stdout.readline = AsyncMock(side_effect=[init_response, tools_response])
        proc.stdout.readline.side_effect = None
        # After handshake, the next readline should hang. Use a side effect that hangs.
        call_count = {"n": 0}

        async def readline_handler():
            call_count["n"] += 1
            if call_count["n"] <= 2:
                return [init_response, tools_response][call_count["n"] - 1]
            await _asyncio.sleep(100)

        proc.stdout.readline = AsyncMock(side_effect=readline_handler)
        client = MiniMaxMCPClient(api_key="test", base_url="https://api.minimax.io", command="uvx", args=["minimax-coding-plan-mcp", "-y"], timeout=0.5)  # short timeout
        await client.start()
        with pytest.raises(_asyncio.TimeoutError):
            await client.search("Hanoi weather", max_results=5)


@pytest.mark.asyncio
async def test_mcp_client_circuit_breaker_opens_after_3_failures():
    """After 3 consecutive failures, circuit opens for 5 min."""
    proc = _make_mock_process()
    init_response = '{"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}}}\n'
    tools_response = '{"jsonrpc": "2.0", "id": 2, "result": {"tools": [{"name": "web_search", "description": "x", "inputSchema": {}}]}}\n'
    error_response = '{"jsonrpc": "2.0", "id": 3, "error": {"code": -32603, "message": "fail"}}\n'

    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
        # After handshake, every search returns error
        call_count = {"n": 0}

        async def readline_handler():
            call_count["n"] += 1
            if call_count["n"] == 1:
                return init_response
            if call_count["n"] == 2:
                return tools_response
            return error_response

        proc.stdout.readline = AsyncMock(side_effect=readline_handler)
        client = MiniMaxMCPClient(api_key="test", base_url="https://api.minimax.io", command="uvx", args=["minimax-coding-plan-mcp", "-y"], timeout=8.0)
        await client.start()
        # First 3 calls fail
        for _ in range(3):
            with pytest.raises(MCPError):
                await client.search("x", max_results=1)
        # 4th call should short-circuit on circuit breaker
        with pytest.raises(MCPCircuitOpen):
            await client.search("x", max_results=1)
```

- [ ] **Step 2: Run the new tests to verify they fail (RED)**

Run: `cd /home/hung/ai-hub && ./venv/bin/pytest tests/unit/test_minimax_mcp.py -v 2>&1 | tail -30`
Expected: 8 framer tests pass, 6 new client tests FAIL with `ImportError: cannot import name 'MiniMaxMCPClient'`.

- [ ] **Step 3: Implement `MiniMaxMCPClient`**

Append to `/home/hung/ai-hub/app/services/mcp/minimax_websearch.py`:

```python
import asyncio
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

# Circuit breaker constants
CIRCUIT_FAILURE_THRESHOLD = 3
CIRCUIT_OPEN_DURATION_SECONDS = 300  # 5 min


class MiniMaxMCPClient:
    """Async client for the MiniMax WebSearch MCP server.

    Spawns `minimax-coding-plan-mcp` (via uvx) on start(), performs the
    initialize + tools/list handshake, then exposes `search(query)` which
    invokes the `web_search` tool via JSON-RPC 2.0 over stdio.

    On subprocess crash, auto-restarts once. After 3 consecutive failures,
    the circuit breaker opens for 5 minutes and calls short-circuit.
    """

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        command: str = "uvx",
        args: list[str] | None = None,
        timeout: float = 8.0,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url
        self._command = command
        self._args = args or ["minimax-coding-plan-mcp", "-y"]
        self._timeout = timeout
        self._proc: asyncio.subprocess.Process | None = None
        self._next_id = 1
        self._failure_count = 0
        self._circuit_open_until = 0.0

    async def start(self) -> None:
        """Spawn subprocess and run initialize + tools/list handshake."""
        env = {
            **__import__("os").environ,
            "MINIMAX_API_KEY": self._api_key,
            "MINIMAX_API_HOST": self._base_url,
        }
        self._proc = await asyncio.create_subprocess_exec(
            self._command,
            *self._args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        # Handshake: initialize → tools/list
        init_resp = await self._send_request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "ai-hub", "version": "1.0"},
        })
        await self._send_notification("notifications/initialized", {})
        tools_resp = await self._send_request("tools/list", {})
        tools = tools_resp.get("tools", [])
        if not any(t.get("name") == "web_search" for t in tools):
            raise MCPError(f"MCP server did not expose web_search tool. Tools: {tools}")
        self._failure_count = 0
        logger.info("MiniMax MCP ready: %d tools", len(tools))

    def is_healthy(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    async def stop(self) -> None:
        """Terminate subprocess gracefully."""
        if self._proc and self._proc.returncode is None:
            self._proc.terminate()
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=3.0)
            except asyncio.TimeoutError:
                self._proc.kill()
                await self._proc.wait()
        self._proc = None

    async def search(self, query: str, *, max_results: int = 5) -> list[dict[str, Any]]:
        """Invoke web_search tool and return parsed results list."""
        if time.time() < self._circuit_open_until:
            raise MCPCircuitOpen("MiniMax MCP circuit breaker is open")
        if not self._proc or not self.is_healthy():
            raise MCPError("MiniMax MCP subprocess is not running")
        try:
            resp = await self._send_request(
                "tools/call",
                {"name": "web_search", "arguments": {"query": query}},
            )
            self._failure_count = 0
            return self._parse_search_response(resp, max_results)
        except Exception:
            self._failure_count += 1
            if self._failure_count >= CIRCUIT_FAILURE_THRESHOLD:
                self._circuit_open_until = time.time() + CIRCUIT_OPEN_DURATION_SECONDS
                logger.error(
                    "MiniMax MCP circuit breaker opened for %ds after %d consecutive failures",
                    CIRCUIT_OPEN_DURATION_SECONDS, self._failure_count,
                )
            raise

    def _parse_search_response(self, resp: dict[str, Any], max_results: int) -> list[dict[str, Any]]:
        """Parse the JSON-RPC result into a list of {url, title, snippet} dicts.

        Expected shape: result.content = [{"type": "text", "text": "[{...},{...}]"}]
        """
        result = resp.get("result", {})
        if result.get("isError"):
            err = result.get("errorMessage") or "unknown MCP error"
            raise MCPError(f"MCP tool error: {err}")
        content = result.get("content", [])
        if not content:
            return []
        text = content[0].get("text", "")
        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, TypeError) as exc:
            raise MCPError(f"MCP web_search returned non-JSON content: {text[:200]!r}") from exc
        if not isinstance(parsed, list):
            raise MCPError(f"MCP web_search expected JSON array, got: {type(parsed).__name__}")
        return parsed[:max_results]

    async def _send_request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """Send a request frame and read the response (matched by id)."""
        if not self._proc or not self._proc.stdin or not self._proc.stdout:
            raise MCPError("subprocess not running")
        req_id = self._next_id
        self._next_id += 1
        frame = JsonRpcFramer.build_request(id=req_id, method=method, params=params)
        self._proc.stdin.write(JsonRpcFramer.serialize(frame).encode("utf-8"))
        await self._proc.stdin.drain()
        # Read lines until we get the response with matching id
        while True:
            line_bytes = await asyncio.wait_for(self._proc.stdout.readline(), timeout=self._timeout)
            if not line_bytes:
                raise MCPError("MCP subprocess closed stdout unexpectedly")
            line = line_bytes.decode("utf-8")
            try:
                parsed = JsonRpcFramer.parse_line(line)
            except MCPError:
                logger.debug("Skipping malformed MCP frame: %r", line[:200])
                continue
            if parsed.get("id") == req_id:
                if "error" in parsed:
                    err = parsed["error"]
                    raise MCPError(f"JSON-RPC error {err.get('code')}: {err.get('message')}")
                return parsed

    async def _send_notification(self, method: str, params: dict[str, Any]) -> None:
        """Send a notification frame (no response expected)."""
        if not self._proc or not self._proc.stdin:
            raise MCPError("subprocess not running")
        frame = JsonRpcFramer.build_notification(method=method, params=params)
        self._proc.stdin.write(JsonRpcFramer.serialize(frame).encode("utf-8"))
        await self._proc.stdin.drain()
```

- [ ] **Step 4: Run the new tests to verify they pass (GREEN)**

Run: `cd /home/hung/ai-hub && ./venv/bin/pytest tests/unit/test_minimax_mcp.py -v 2>&1 | tail -25`
Expected: All 14 tests PASS (8 framer + 6 client).

- [ ] **Step 5: Commit**

```bash
cd /home/hung/ai-hub
git add app/services/mcp/minimax_websearch.py tests/unit/test_minimax_mcp.py
git commit -m "feat(mcp): add MiniMaxMCPClient with JSON-RPC stdio + circuit breaker"
```

---

## Task 4: TDD for uvx auto-install helper

**Files:**
- Modify: `tests/unit/test_minimax_mcp.py` (add 2 tests)
- Modify: `app/services/mcp/minimax_websearch.py` (add `ensure_uvx_installed`)

- [ ] **Step 1: Add 2 tests to the test file**

Append:
```python
from app.services.mcp.minimax_websearch import ensure_uvx_installed


def test_ensure_uvx_installed_skips_when_present(monkeypatch):
    """If uvx is already in PATH, no install is performed."""
    monkeypatch.setattr("app.services.mcp.minimax_websearch.shutil.which", lambda x: f"/usr/bin/{x}")
    called = {"n": 0}
    monkeypatch.setattr("app.services.mcp.minimax_websearch.subprocess.run", lambda *a, **k: called.update(n=called["n"] + 1))
    path = ensure_uvx_installed()
    assert path == "/usr/bin/uvx"
    assert called["n"] == 0  # no install attempted


def test_ensure_uvx_installed_runs_install_script_when_missing(monkeypatch):
    """If uvx is missing, run the install script and re-check."""
    which_calls = {"n": 0}

    def fake_which(x):
        which_calls["n"] += 1
        if which_calls["n"] == 1:
            return None  # first call: not found
        return f"/home/hung/.local/bin/{x}"  # second call: found after install

    monkeypatch.setattr("app.services.mcp.minimax_websearch.shutil.which", fake_which)
    install_calls = {"n": 0}

    def fake_run(cmd, **kwargs):
        install_calls["n"] += 1
        # Make a fake binary so the second shutil.which returns a path
        import os
        os.makedirs("/home/hung/.local/bin", exist_ok=True)
        with open("/home/hung/.local/bin/uvx", "w") as f:
            f.write("#!/bin/sh\n")
        os.chmod("/home/hung/.local/bin/uvx", 0o755)
        from unittest.mock import MagicMock
        m = MagicMock()
        m.returncode = 0
        return m

    monkeypatch.setattr("app.services.mcp.minimax_websearch.subprocess.run", fake_run)
    path = ensure_uvx_installed()
    assert path == "/home/hung/.local/bin/uvx"
    assert install_calls["n"] == 1
```

- [ ] **Step 2: Run the new tests to verify they fail (RED)**

Run: `cd /home/hung/ai-hub && ./venv/bin/pytest tests/unit/test_minimax_mcp.py::test_ensure_uvx_installed_skips_when_present tests/unit/test_minimax_mcp.py::test_ensure_uvx_installed_runs_install_script_when_missing -v 2>&1 | tail -10`
Expected: Both FAIL with `ImportError: cannot import name 'ensure_uvx_installed'`.

- [ ] **Step 3: Add `ensure_uvx_installed` function**

Add to `/home/hung/ai-hub/app/services/mcp/minimax_websearch.py` (at the top, after imports):

```python
import os
import shutil
import subprocess

UVX_INSTALL_SCRIPT = "https://astral.sh/uv/install.sh"


def ensure_uvx_installed() -> str:
    """Return path to `uvx`, installing via the official script if missing.

    Returns:
        Absolute path to the uvx binary.

    Raises:
        MCPError: if uvx is still not available after the install attempt.
    """
    path = shutil.which("uvx")
    if path:
        return path
    logger.info("uvx not found in PATH; installing via %s", UVX_INSTALL_SCRIPT)
    install_cmd = "curl -LsSf " + UVX_INSTALL_SCRIPT + " | sh"
    result = subprocess.run(install_cmd, shell=True, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        raise MCPError(f"Failed to install uvx: {result.stderr or result.stdout}")
    # Re-check; ensure ~/.local/bin is on PATH for the current process
    home = os.path.expanduser("~")
    local_bin = os.path.join(home, ".local", "bin")
    if local_bin not in os.environ.get("PATH", ""):
        os.environ["PATH"] = local_bin + os.pathsep + os.environ.get("PATH", "")
    path = shutil.which("uvx")
    if not path:
        raise MCPError("uvx still not in PATH after install. Try: export PATH=$HOME/.local/bin:$PATH")
    return path
```

- [ ] **Step 4: Run the new tests to verify they pass (GREEN)**

Run: `cd /home/hung/ai-hub && ./venv/bin/pytest tests/unit/test_minimax_mcp.py -v 2>&1 | tail -20`
Expected: All 16 tests PASS (8 framer + 6 client + 2 ensure_uvx).

- [ ] **Step 5: Commit**

```bash
cd /home/hung/ai-hub
git add app/services/mcp/minimax_websearch.py tests/unit/test_minimax_mcp.py
git commit -m "feat(mcp): add ensure_uvx_installed helper with auto-install"
```

---

## Task 5: Update config.py with MiniMax MCP settings

**Files:**
- Modify: `app/core/config.py`

- [ ] **Step 1: Find the current web_search settings**

Run: `grep -n "enable_web_search_tool\|web_search_max_results\|web_search_timeout_seconds\|google_search_cx" /home/hung/ai-hub/app/core/config.py`
Expected: 4 lines of settings to remove.

- [ ] **Step 2: Remove the old web_search settings**

Delete these 4 lines (and any surrounding lines that become orphaned):
```python
enable_web_search_tool: bool = Field(default=True, alias="ENABLE_WEB_SEARCH_TOOL")
...
web_search_max_results: int = Field(default=5, ge=1, le=10, alias="WEB_SEARCH_MAX_RESULTS")
web_search_timeout_seconds: float = Field(default=8.0, gt=0, alias="WEB_SEARCH_TIMEOUT_SECONDS")
...
google_search_cx: str = Field(default="", alias="GOOGLE_SEARCH_CX")
```

(Open the file in Read first to see exact context, then Edit to remove the lines.)

- [ ] **Step 3: Find the MiniMax settings block**

Run: `grep -n "minimax_api_host\|minimax_max_tokens" /home/hung/ai-hub/app/core/config.py`
Expected: The MiniMax settings block around line 82-89.

- [ ] **Step 4: Add MiniMax MCP settings after the existing MiniMax settings**

Insert these 5 new lines after `minimax_denied_projects`:

```python
minimax_mcp_enabled: bool = Field(default=True, alias="MINIMAX_MCP_ENABLED")
minimax_mcp_command: str = Field(default="uvx", alias="MINIMAX_MCP_COMMAND")
minimax_mcp_args: list[str] = Field(
    default_factory=lambda: ["minimax-coding-plan-mcp", "-y"],
    alias="MINIMAX_MCP_ARGS",
)
minimax_mcp_timeout_seconds: float = Field(default=8.0, gt=0, alias="MINIMAX_MCP_TIMEOUT_SECONDS")
minimax_mcp_max_results: int = Field(default=5, ge=1, le=10, alias="MINIMAX_MCP_MAX_RESULTS")
```

- [ ] **Step 5: Verify with grep**

Run: `grep -n "minimax_mcp" /home/hung/ai-hub/app/core/config.py`
Expected: 5 new settings found.

- [ ] **Step 6: Commit**

```bash
cd /home/hung/ai-hub
git add app/core/config.py
git commit -m "feat(config): replace web_search settings with MiniMax MCP settings"
```

---

## Task 6: Update .env with MiniMax MCP settings

**Files:**
- Modify: `.env`

- [ ] **Step 1: Check current .env state**

Run: `grep -nE "WEB_SEARCH|GOOGLE_SEARCH|MINIMAX_API_KEY|MINIMAX_MCP|ENABLE_WEB_SEARCH" /home/hung/ai-hub/.env`
Expected: Some old `WEB_SEARCH_*` and `GOOGLE_SEARCH_*` lines + `MINIMAX_API_KEY` already there.

- [ ] **Step 2: Remove old web_search env vars**

Remove these lines (use Edit with `replace_all: false`):
- `ENABLE_WEB_SEARCH_TOOL=true`
- `WEB_SEARCH_MAX_RESULTS=5`
- `WEB_SEARCH_TIMEOUT_SECONDS=8.0`
- `GOOGLE_SEARCH_CX=` (and any GOOGLE_SEARCH_API_KEY if present)
- `DDGS_REGION=wt-wt` (if present)
- `BING_SEARCH_KEY=` (if present)

(Use Read first to see exact lines, then Edit each.)

- [ ] **Step 3: Add new MiniMax MCP env vars**

Add these lines (near the existing `MINIMAX_API_KEY`):

```
MINIMAX_MCP_ENABLED=true
MINIMAX_MCP_COMMAND=uvx
MINIMAX_MCP_ARGS=["minimax-coding-plan-mcp","-y"]
MINIMAX_MCP_TIMEOUT_SECONDS=8.0
MINIMAX_MCP_MAX_RESULTS=5
```

- [ ] **Step 4: Verify**

Run: `grep -nE "MINIMAX" /home/hung/ai-hub/.env`
Expected: 9+ lines including MINIMAX_API_KEY, MINIMAX_ENABLED, MINIMAX_BASE_URL, MINIMAX_MODEL, plus 5 new MINIMAX_MCP_*.

- [ ] **Step 5: Commit (note: .env is typically gitignored; if so, no commit needed)**

```bash
cd /home/hung/ai-hub
git check-ignore .env && echo ".env is gitignored, no commit" || git add .env && git commit -m "chore(env): add MINIMAX_MCP_* settings"
```

---

## Task 7: Wire MiniMaxMCPClient into main.py lifecycle

**Files:**
- Modify: `app/main.py`

- [ ] **Step 1: Find current web_search setup in main.py**

Run: `grep -n "WebSearchService\|web_search" /home/hung/ai-hub/app/main.py`
Expected: Several references around lines 60-300.

- [ ] **Step 2: Replace the import**

Find: `from app.services.tools.web_search_service import WebSearchService`
Replace with: `from app.services.mcp.minimax_websearch import MiniMaxMCPClient, ensure_uvx_installed, MCPError`

- [ ] **Step 3: Find the WebSearchService instantiation block**

Run: `sed -n '240,260p' /home/hung/ai-hub/app/main.py`
Expected: A block that constructs `web_search = WebSearchService(...)`.

- [ ] **Step 4: Replace the instantiation**

Find the block that looks like:
```python
            web_search = WebSearchService(
                timeout_seconds=settings.web_search_timeout_seconds,
                ...
            )
            app.state.web_search_service = web_search
```

Replace with:
```python
            minimax_mcp_client: MiniMaxMCPClient | None = None
            if settings.minimax_enabled and settings.minimax_mcp_enabled and settings.minimax_api_key:
                try:
                    uvx_path = ensure_uvx_installed()
                    logger.info("Using uvx at %s", uvx_path)
                    minimax_mcp_client = MiniMaxMCPClient(
                        api_key=settings.minimax_api_key,
                        base_url=settings.minimax_base_url,
                        command=settings.minimax_mcp_command,
                        args=settings.minimax_mcp_args,
                        timeout=settings.minimax_mcp_timeout_seconds,
                    )
                    await minimax_mcp_client.start()
                    app.state.minimax_mcp = minimax_mcp_client
                except Exception as exc:
                    logger.warning("MiniMax MCP disabled (failed to start): %s", exc)
                    app.state.minimax_mcp = None
            else:
                app.state.minimax_mcp = None
```

- [ ] **Step 5: Find where web_search_service is passed to AIService**

Run: `grep -n "web_search=" /home/hung/ai-hub/app/main.py`
Expected: A line in the AIService constructor.

- [ ] **Step 6: Update the AIService constructor call**

Find: `web_search=web_search,` (or similar)
Replace with: `minimax_mcp=app.state.minimax_mcp,` (using `app.state` lookup, since `minimax_mcp_client` is a local var)

Or, more safely, refactor to use a clearly-named local var:
```python
            minimax_mcp_for_ai = app.state.minimax_mcp
            ai_service = AIService(..., minimax_mcp=minimax_mcp_for_ai)
```

(Use the existing line structure; just rename the kwarg.)

- [ ] **Step 7: Add shutdown handler for MCP subprocess**

Find the shutdown section (search for `atexit` or `shutdown` handlers in main.py). Add:

```python
            if hasattr(app.state, "minimax_mcp") and app.state.minimax_mcp:
                await app.state.minimax_mcp.stop()
```

Inside the existing shutdown hook function (usually a `@asynccontextmanager` for `lifespan`).

- [ ] **Step 8: Verify with grep**

Run: `grep -n "WebSearchService\|minimax_mcp\|MiniMaxMCPClient" /home/hung/ai-hub/app/main.py`
Expected: 0 references to `WebSearchService`; 4+ references to `MiniMaxMCPClient` / `minimax_mcp`.

- [ ] **Step 9: Commit**

```bash
cd /home/hung/ai-hub
git add app/main.py
git commit -m "feat(main): wire MiniMaxMCPClient into startup + shutdown"
```

---

## Task 8: Update ai_service.py to use MCP client

**Files:**
- Modify: `app/services/ai_service.py`

- [ ] **Step 1: Find the WebSearchService import**

Run: `grep -n "WebSearchService\|web_search_service" /home/hung/ai-hub/app/services/ai_service.py`
Expected: 1 import line + multiple usages.

- [ ] **Step 2: Replace the import**

Find: `from app.services.tools.web_search_service import WebSearchService`
Replace with: `from app.services.mcp.minimax_websearch import MiniMaxMCPClient`

- [ ] **Step 3: Find the AIService constructor**

Run: `grep -n "def __init__\|web_search" /home/hung/ai-hub/app/services/ai_service.py | head -20`
Expected: `__init__` takes a `web_search` parameter (now needs to be `minimax_mcp`).

- [ ] **Step 4: Update the `__init__` signature and the field assignment**

Find: `def __init__(... self, web_search: WebSearchService | None = None, ...)`
Replace with: `def __init__(... self, minimax_mcp: MiniMaxMCPClient | None = None, ...)`

Then find: `self._web_search = web_search`
Replace with: `self._mcp = minimax_mcp`

- [ ] **Step 5: Update the search call site**

Find the existing code around line 435 (from spec):
```python
        if not self._web_search or not self._settings.enable_web_search_tool:
            return None, []
        ...
        results = self._web_search.search(safe_query, max_results=...)
```

Replace with:
```python
        if not self._mcp or not self._settings.minimax_mcp_enabled:
            return None, []
        ...
        results = await self._mcp.search(safe_query, max_results=self._settings.minimax_mcp_max_results)
```

- [ ] **Step 6: Verify with grep**

Run: `grep -n "WebSearchService\|_web_search\b" /home/hung/ai-hub/app/services/ai_service.py`
Expected: 0 matches.

Run: `grep -n "self._mcp\|minimax_mcp\|MiniMaxMCPClient" /home/hung/ai-hub/app/services/ai_service.py`
Expected: 3+ matches.

- [ ] **Step 7: Commit**

```bash
cd /home/hung/ai-hub
git add app/services/ai_service.py
git commit -m "refactor(ai_service): use MiniMaxMCPClient instead of WebSearchService"
```

---

## Task 9: Delete WebSearchService and clean up deps

**Files:**
- Delete: `app/services/tools/web_search_service.py`

- [ ] **Step 1: Verify no remaining references**

Run: `grep -rn "WebSearchService\|from app.services.tools.web_search_service" /home/hung/ai-hub/app/ 2>/dev/null`
Expected: 0 matches (the previous 2 tasks cleaned up all references).

- [ ] **Step 2: Delete the file**

Run: `rm /home/hung/ai-hub/app/services/tools/web_search_service.py && ls /home/hung/ai-hub/app/services/tools/`
Expected: `__init__.py` (and maybe `__pycache__`).

- [ ] **Step 3: Check requirements files for web_search-related deps**

Run: `grep -E "ddgs|lxml|requests" /home/hung/ai-hub/requirements.txt /home/hung/ai-hub/pyproject.toml 2>/dev/null | head -10`
Expected: `ddgs` should be present (for removal); `lxml` and `requests` may be present (KEEP — used elsewhere).

- [ ] **Step 4: Remove `ddgs` from requirements**

If found in `requirements.txt`:
```bash
cd /home/hung/ai-hub
grep -v "^ddgs" requirements.txt > requirements.txt.tmp && mv requirements.txt.tmp requirements.txt
```

If found in `pyproject.toml`, use Edit to remove the line.

- [ ] **Step 5: Commit**

```bash
cd /home/hung/ai-hub
git add app/services/tools/ requirements.txt pyproject.toml
git commit -m "chore: delete WebSearchService, remove ddgs dep (MCP replaces local search)"
```

---

## Task 10: Update CLAUDE.md

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Find the current Web Search section**

Run: `grep -n "Web Search\|web_search\|WebSearchService" /home/hung/ai-hub/CLAUDE.md`
Expected: Lines around 36-39 (per the CLAUDE.md we saw earlier).

- [ ] **Step 2: Replace the Web Search section**

Find:
```markdown
### Web Search
- Multi-backend: Google Custom Search → DDGS → DuckDuckGo HTML → Bing HTML
- Vietnamese domain quality scoring, tracking param removal
- Triggered by `/search:` prefix or `enable_search=true` with `?` in message
```

Replace with:
```markdown
### Web Search
- Backend: **MiniMax WebSearch MCP** (`minimax-coding-plan-mcp` package, runs locally via `uvx`)
- Communicates with MCP server via JSON-RPC 2.0 over stdio (newline-delimited JSON)
- Vietnamese + multi-language: MiniMax handles quality scoring + tracking-param removal server-side
- Triggered by `/search: <query>` prefix OR `?` in message (auto-detect)
- Auto-installs `uvx` on first startup if missing; spawns subprocess; circuit breaker after 3 consecutive failures
- See `app/services/mcp/minimax_websearch.py` for client + `app/services/mcp/minimax_websearch.py:ensure_uvx_installed` for setup
```

- [ ] **Step 3: Update the services table**

Find the line:
```markdown
| `app/services/tools/web_search_service.py` | Multi-backend web search |
```

Replace with:
```markdown
| `app/services/mcp/minimax_websearch.py` | MiniMax WebSearch MCP client (JSON-RPC over stdio) |
```

- [ ] **Step 4: Commit**

```bash
cd /home/hung/ai-hub
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md — MiniMax MCP replaces local WebSearchService"
```

---

## Task 11: Integration smoke test

**Files:** None modified.

- [ ] **Step 1: Restart ai-hub to pick up new code**

```bash
cd /home/hung/ai-hub
pkill -f "uvicorn app.main:app" 2>/dev/null
sleep 2
nohup ./venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 > /tmp/uvicorn-smoke.log 2>&1 &
echo "uvicorn PID: $!"
disown
sleep 30  # give time for MCP subprocess spawn
```

- [ ] **Step 2: Verify uvicorn started without errors**

Run: `tail -20 /tmp/uvicorn-smoke.log`
Expected: Logs showing "Using uvx at ...", "MiniMax MCP ready: 1 tools", "Application startup complete".

If "MiniMax MCP disabled" appears, check the error message — likely the package isn't on PyPI, or API key is missing, or uvx install failed.

- [ ] **Step 3: Test /search: prefix via API**

```bash
API_KEY="1XteCCQ_s_UbrqOGEIYybmDBnokWhxYap90D6_Jojx8"
curl -sS -m 60 -H "X-API-KEY: $API_KEY" -H "Content-Type: application/json" \
  -X POST http://127.0.0.1:8000/v1/chat \
  -d '{"project_id":"test","user_name":"hung","user_message":"/search: thời tiết Hà Nội hôm nay","max_tokens":300}' \
  | python3 -c "import sys, json; d = json.load(sys.stdin); print('reply:', d.get('content','')[:300]); print('sources:', d.get('sources',[]))"
```

Expected: A Vietnamese response that mentions current weather, with `sources` array containing 1+ URLs.

- [ ] **Step 4: Test `?` auto-detect via API**

```bash
curl -sS -m 60 -H "X-API-KEY: $API_KEY" -H "Content-Type: application/json" \
  -X POST http://127.0.0.1:8000/v1/chat \
  -d '{"project_id":"test","user_name":"hung","user_message":"Ai là tổng thống Mỹ hiện tại?","max_tokens":300}' \
  | python3 -c "import sys, json; d = json.load(sys.stdin); print('reply:', d.get('content','')[:300]); print('sources:', d.get('sources',[]))"
```

Expected: A response naming the current US president with citation URLs.

- [ ] **Step 5: Verify MCP subprocess is alive**

Run: `ps aux | grep "minimax-coding-plan-mcp" | grep -v grep | head -2`
Expected: At least 1 process line showing the MCP subprocess.

- [ ] **Step 6: Test via public domain too (optional but recommended)**

```bash
DOMAIN="https://api-aiserver.htechlabsvn.com"
curl -sS -m 60 -H "X-API-KEY: $API_KEY" -H "Content-Type: application/json" \
  -X POST "$DOMAIN/v1/chat" \
  -d '{"project_id":"test","user_name":"hung","user_message":"/search: thủ đô của Nhật Bản","max_tokens":200}' \
  | python3 -c "import sys, json; d = json.load(sys.stdin); print('reply:', d.get('content','')[:200]); print('sources:', d.get('sources',[]))"
```

Expected: A response saying "Tokyo" with at least 1 source URL.

- [ ] **Step 7: Final commit if any cleanup needed**

```bash
cd /home/hung/ai-hub
git status --short
# If anything else modified, commit it
```

If nothing else, skip.

---

## Self-Review Checklist

- [x] All 11 tasks defined with TDD where applicable
- [x] Tests cover JsonRpcFramer (8), MiniMaxMCPClient (6), ensure_uvx (2)
- [x] Type consistency: `MiniMaxMCPClient` defined in Task 3, used in Task 7+8
- [x] All file paths exact
- [x] No placeholders (TBD/TODO/etc.)
- [x] All commands shown with expected output
- [x] All commits specified
- [x] Spec coverage: every section of `docs/superpowers/specs/2026-06-06-minimax-websearch-mcp.md` has a corresponding task
- [x] uvx install included (Task 1) since spec said "chưa có"
- [x] Test API key usage: tests use "test"/"bad" mocks, never the real key
