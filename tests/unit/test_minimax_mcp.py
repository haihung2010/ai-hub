"""Unit tests for MiniMax WebSearch MCP client.

The MCP protocol uses newline-delimited JSON over stdio:
- Client → Server: one JSON object per line on stdin
- Server → Client: one JSON object per line on stdout
"""

import pytest

from app.services.mcp.minimax_websearch import JsonRpcFramer, MCPError

# These tests are pure-Python (no DB), so opt out of the autouse
# `isolated_db` fixture which requires a writable test PostgreSQL.
pytestmark = pytest.mark.no_isolated_db


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
