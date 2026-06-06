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
