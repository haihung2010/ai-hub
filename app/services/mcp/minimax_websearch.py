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

import asyncio
import json
import logging
import os
import shutil
import subprocess
import time
from typing import Any

logger = logging.getLogger(__name__)

# Circuit breaker constants
CIRCUIT_FAILURE_THRESHOLD = 3
CIRCUIT_OPEN_DURATION_SECONDS = 300  # 5 min

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


class MCPError(Exception):
    """Raised when the MCP server returns an error or the protocol is violated."""


class MCPCircuitOpen(Exception):
    """Raised when the circuit breaker is open and the call should short-circuit."""


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


class MiniMaxMCPClient:
    """Async client for the MiniMax WebSearch MCP server.

    Spawns `minimax-coding-plan-mcp` (via uvx) on start(), performs the
    initialize + tools/list handshake, then exposes `search(query)` which
    invokes the `web_search` tool via JSON-RPC 2.0 over stdio.

    On 3 consecutive failures, the circuit breaker opens for 5 minutes
    and calls short-circuit with MCPCircuitOpen.
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
            **os.environ,
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
        await self._send_request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "ai-hub", "version": "1.0"},
        })
        await self._send_notification("notifications/initialized", {})
        tools_resp = await self._send_request("tools/list", {})
        result = tools_resp.get("result", {})
        tools = result.get("tools", [])
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

    async def search(self, query: str, *, max_results: int = 5) -> list[dict]:
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

    def _parse_search_response(self, resp: dict, max_results: int) -> list[dict]:
        """Parse the JSON-RPC result into a list of {url, title, snippet} dicts.

        The MCP server returns content[0].text as a JSON payload. The shape
        may be either a bare JSON array OR a dict like {"organic": [...]}.
        Items inside the list may use varying key names; we normalize to
        the canonical {url, title, snippet} shape used by ai-hub's prompt
        template.
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

        # Normalize to list of dicts
        items: list[dict] = []
        if isinstance(parsed, list):
            items = parsed
        elif isinstance(parsed, dict):
            for key in ("organic", "results", "items", "data", "web_search_results", "sources"):
                if key in parsed and isinstance(parsed[key], list):
                    items = parsed[key]
                    break
            else:
                logger.warning("MCP web_search returned dict without known results key. Keys: %s", list(parsed.keys())[:10])
                return []
        else:
            raise MCPError(f"MCP web_search expected JSON array or dict, got: {type(parsed).__name__}")

        # Normalize each item to canonical {url, title, snippet}
        normalized = []
        for raw in items[:max_results]:
            if not isinstance(raw, dict):
                continue
            # url key variants
            url = raw.get("url") or raw.get("link") or raw.get("href") or raw.get("source") or ""
            # title key variants
            title = raw.get("title") or raw.get("name") or raw.get("heading") or ""
            # snippet key variants
            snippet = (
                raw.get("snippet") or raw.get("description") or raw.get("abstract")
                or raw.get("content") or raw.get("text") or ""
            )
            if url or title or snippet:
                normalized.append({"url": url, "title": title, "snippet": snippet})
        return normalized

    async def _send_request(self, method: str, params: dict) -> dict:
        """Send a request frame and read the response (matched by id)."""
        if not self._proc or not self._proc.stdin or not self._proc.stdout:
            raise MCPError("subprocess not running")
        req_id = self._next_id
        self._next_id += 1
        frame = JsonRpcFramer.build_request(id=req_id, method=method, params=params)
        self._proc.stdin.write(JsonRpcFramer.serialize(frame).encode("utf-8"))
        await self._proc.stdin.drain()
        while True:
            line_bytes = await asyncio.wait_for(self._proc.stdout.readline(), timeout=self._timeout)
            if not line_bytes:
                raise MCPError("MCP subprocess closed stdout unexpectedly")
            if isinstance(line_bytes, bytes):
                line = line_bytes.decode("utf-8")
            else:
                line = line_bytes
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

    async def _send_notification(self, method: str, params: dict) -> None:
        """Send a notification frame (no response expected)."""
        if not self._proc or not self._proc.stdin:
            raise MCPError("subprocess not running")
        frame = JsonRpcFramer.build_notification(method=method, params=params)
        self._proc.stdin.write(JsonRpcFramer.serialize(frame).encode("utf-8"))
        await self._proc.stdin.drain()
