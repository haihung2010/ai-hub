"""Unit tests for MiniMax WebSearch MCP client.

The MCP protocol uses newline-delimited JSON over stdio:
- Client → Server: one JSON object per line on stdin
- Server → Client: one JSON object per line on stdout
"""

import os
import shutil
from unittest.mock import MagicMock

import pytest

from app.services.mcp.minimax_websearch import (
    JsonRpcFramer,
    MCPError,
    ensure_uvx_installed,
)

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


# ---------------------------------------------------------------------------
# MiniMaxMCPClient tests (Task 3)
# ---------------------------------------------------------------------------

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.mcp.minimax_websearch import MiniMaxMCPClient, MCPCircuitOpen


def _make_mock_process(returncode=None):
    """Build a mock asyncio.subprocess.Process with paired streams.

    Default returncode=None represents a still-running process, matching
    the real `asyncio.subprocess.Process` behavior.
    """
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
    init_response = (
        '{"jsonrpc": "2.0", "id": 1, "result": '
        '{"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, '
        '"serverInfo": {"name": "minimax-coding-plan-mcp", "version": "0.1.0"}}}\n'
    )
    tools_response = (
        '{"jsonrpc": "2.0", "id": 2, "result": '
        '{"tools": [{"name": "web_search", "description": "Search the web", '
        '"inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}, '
        '"required": ["query"]}}]}}\n'
    )

    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)) as mock_exec:
        proc.stdout.readline = AsyncMock(side_effect=[init_response, tools_response, b""])
        client = MiniMaxMCPClient(
            api_key="test", base_url="https://api.minimax.io",
            command="uvx", args=["minimax-coding-plan-mcp", "-y"], timeout=8.0,
        )
        await client.start()
        mock_exec.assert_called_once()
        args, kwargs = mock_exec.call_args
        assert args[0] == "uvx"
        assert "minimax-coding-plan-mcp" in args
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
    inner_results = [
        {"url": "https://example.com/1", "title": "Page 1", "snippet": "snippet 1"},
        {"url": "https://example.com/2", "title": "Page 2", "snippet": "snippet 2"},
    ]
    inner_text = json.dumps(inner_results)  # a JSON-encoded string
    search_response = json.dumps({
        "jsonrpc": "2.0",
        "id": 3,
        "result": {
            "content": [{"type": "text", "text": inner_text}],
            "isError": False,
        },
    }) + "\n"

    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
        proc.stdout.readline = AsyncMock(side_effect=[init_response, tools_response, search_response, b""])
        client = MiniMaxMCPClient(
            api_key="test", base_url="https://api.minimax.io",
            command="uvx", args=["minimax-coding-plan-mcp", "-y"], timeout=8.0,
        )
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
        client = MiniMaxMCPClient(
            api_key="bad", base_url="https://api.minimax.io",
            command="uvx", args=["minimax-coding-plan-mcp", "-y"], timeout=8.0,
        )
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
        client = MiniMaxMCPClient(
            api_key="test", base_url="https://api.minimax.io",
            command="uvx", args=["minimax-coding-plan-mcp", "-y"], timeout=8.0,
        )
        await client.start()
        with pytest.raises(MCPError, match="Internal error"):
            await client.search("Hanoi weather", max_results=5)


@pytest.mark.asyncio
async def test_mcp_client_search_handles_timeout():
    """A subprocess that never responds should raise asyncio.TimeoutError."""
    proc = _make_mock_process()
    init_response = '{"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}}}\n'
    tools_response = '{"jsonrpc": "2.0", "id": 2, "result": {"tools": [{"name": "web_search", "description": "x", "inputSchema": {}}]}}\n'

    call_count = {"n": 0}

    async def readline_handler():
        call_count["n"] += 1
        if call_count["n"] == 1:
            return init_response
        if call_count["n"] == 2:
            return tools_response
        await asyncio.sleep(100)  # hang forever

    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
        proc.stdout.readline = AsyncMock(side_effect=readline_handler)
        client = MiniMaxMCPClient(
            api_key="test", base_url="https://api.minimax.io",
            command="uvx", args=["minimax-coding-plan-mcp", "-y"], timeout=0.5,
        )
        await client.start()
        with pytest.raises(asyncio.TimeoutError):
            await client.search("Hanoi weather", max_results=5)


@pytest.mark.asyncio
async def test_mcp_client_circuit_breaker_opens_after_3_failures():
    """After 3 consecutive failures, circuit opens."""
    proc = _make_mock_process()
    init_response = '{"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}}}\n'
    tools_response = '{"jsonrpc": "2.0", "id": 2, "result": {"tools": [{"name": "web_search", "description": "x", "inputSchema": {}}]}}\n'

    call_count = {"n": 0}

    async def readline_handler():
        """Return an error response that matches whatever id the client just sent.

        The client's `_next_id` is incremented on every request, so we need to
        return a frame whose `id` matches the current request id (3, 4, 5, ...).
        Calls 1 and 2 are the initialize/tools/list handshake (ids 1 and 2).
        """
        call_count["n"] += 1
        if call_count["n"] == 1:
            return init_response
        if call_count["n"] == 2:
            return tools_response
        # After handshake, _next_id is 3 and increments per search() call
        req_id = call_count["n"]  # 3, 4, 5, ... matches the search request id
        return f'{{"jsonrpc": "2.0", "id": {req_id}, "error": {{"code": -32603, "message": "fail"}}}}\n'

    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
        proc.stdout.readline = AsyncMock(side_effect=readline_handler)
        client = MiniMaxMCPClient(
            api_key="test", base_url="https://api.minimax.io",
            command="uvx", args=["minimax-coding-plan-mcp", "-y"], timeout=8.0,
        )
        await client.start()
        # First 3 calls fail (count is reset per call, so 1+2+3+... reads advance)
        for _ in range(3):
            with pytest.raises(MCPError):
                await client.search("x", max_results=1)
        # 4th call should short-circuit on circuit breaker
        with pytest.raises(MCPCircuitOpen):
            await client.search("x", max_results=1)


# ---------------------------------------------------------------------------
# ensure_uvx_installed tests (Task 4)
# ---------------------------------------------------------------------------


def test_ensure_uvx_installed_skips_when_present(monkeypatch, tmp_path):
    """If uvx is already in PATH, no install is performed."""
    # Create a fake uvx in tmp_path
    fake_uvx = tmp_path / "uvx"
    fake_uvx.write_text("#!/bin/sh\n")
    fake_uvx.chmod(0o755)
    monkeypatch.setattr("app.services.mcp.minimax_websearch.shutil.which", lambda x: str(fake_uvx))
    called = {"n": 0}
    monkeypatch.setattr("app.services.mcp.minimax_websearch.subprocess.run", lambda *a, **k: called.update(n=called["n"] + 1))
    path = ensure_uvx_installed()
    assert path == str(fake_uvx)
    assert called["n"] == 0  # no install attempted


def test_ensure_uvx_installed_runs_install_script_when_missing(monkeypatch, tmp_path):
    """If uvx is missing, run the install script and re-check."""
    which_calls = {"n": 0}
    install_calls = {"n": 0}

    def fake_which(x):
        which_calls["n"] += 1
        if which_calls["n"] == 1:
            return None  # first call: not found
        # second call (after install): found in tmp_path
        return str(tmp_path / "uvx")

    monkeypatch.setattr("app.services.mcp.minimax_websearch.shutil.which", fake_which)

    def fake_run(cmd, **kwargs):
        install_calls["n"] += 1
        # Make a fake binary so the second shutil.which returns a path
        fake_uvx = tmp_path / "uvx"
        fake_uvx.write_text("#!/bin/sh\n")
        fake_uvx.chmod(0o755)
        m = MagicMock()
        m.returncode = 0
        return m

    monkeypatch.setattr("app.services.mcp.minimax_websearch.subprocess.run", fake_run)
    path = ensure_uvx_installed()
    assert path == str(tmp_path / "uvx")
    assert install_calls["n"] == 1
