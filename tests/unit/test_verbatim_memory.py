"""Unit tests for VerbatimMemory."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.services.verbatim_memory import VerbatimMemory

pytestmark = pytest.mark.no_isolated_db  # VerbatimMemory tests use a mock pool


def _make_pool(rows):
    """Create mock pool returning given rows from a single query.

    Matches the sync cursor protocol used by VerbatimMemory.get_recent
    (which is sync, not async — ai-hub's pool is psycopg_pool.ConnectionPool).
    """
    cur = MagicMock()
    cur.fetchall = MagicMock(return_value=rows)
    cur.execute = MagicMock()
    conn = MagicMock()
    conn.cursor = MagicMock()
    conn.cursor.return_value.__enter__ = MagicMock(return_value=cur)
    conn.cursor.return_value.__exit__ = MagicMock(return_value=None)
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__ = MagicMock(return_value=None)
    pool = MagicMock()
    pool.connection = MagicMock(return_value=conn)
    return pool, cur, conn


def test_format_for_context_empty():
    assert VerbatimMemory.format_for_context([]) == ""


def test_format_for_context_single():
    msgs = [{"role": "user", "content": "Hello", "ts": "2026-06-13T10:00:00"}]
    out = VerbatimMemory.format_for_context(msgs)
    assert "<verbatim_history>" in out
    assert "</verbatim_history>" in out
    assert "user: Hello" in out
    assert "2026-06-13T10:00:00" in out


def test_format_for_context_chronological():
    msgs = [
        {"role": "assistant", "content": "Hi", "ts": "2026-06-13T10:00:01"},
        {"role": "user", "content": "Hello", "ts": "2026-06-13T10:00:00"},
    ]
    out = VerbatimMemory.format_for_context(msgs)
    # Should reverse so user:Hello comes first, then assistant:Hi
    user_idx = out.find("user: Hello")
    asst_idx = out.find("assistant: Hi")
    assert user_idx < asst_idx, f"expected user before assistant, got:\n{out}"


def test_format_for_context_truncates_long_content():
    long = "x" * 1000
    msgs = [{"role": "user", "content": long, "ts": "2026-06-13T10:00:00"}]
    out = VerbatimMemory.format_for_context(msgs, max_chars_per_msg=100)
    # Should only contain first 100 chars + "..." wait, no, we just slice
    assert "x" * 100 in out
    assert "x" * 200 not in out


def test_get_recent_queries_db():
    pool, cur, _conn = _make_pool([
        ("user", "msg1", "2026-06-13T10:00:00"),
        ("assistant", "reply1", "2026-06-13T10:00:01"),
    ])
    vm = VerbatimMemory(pool, max_messages=20)
    msgs = vm.get_recent("user123", session_id="s1", limit=5)
    assert len(msgs) == 2
    assert msgs[0]["role"] == "user"
    assert msgs[0]["content"] == "msg1"
    # Verify query params
    args, _ = cur.execute.call_args
    assert "user_id = %s AND session_id = %s" in args[0]
    assert args[1] == ("user123", "s1", 5)


def test_get_recent_no_session():
    pool, cur, _conn = _make_pool([])
    vm = VerbatimMemory(pool)
    msgs = vm.get_recent("user123")
    args, _ = cur.execute.call_args
    assert "WHERE user_id = %s" in args[0]
    assert args[1] == ("user123", 20)  # default max_messages
