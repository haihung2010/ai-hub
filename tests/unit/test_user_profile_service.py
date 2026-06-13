"""Unit tests for UserProfileService (sync implementation)."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.services.user_profile_service import UserPreferences, UserProfileService


def _make_pool(memory_rows=None, message_rows=None):
    """Build a mock pool that returns memory_rows for memory_items queries
    and message_rows for messages queries. Sync (mirrors OrdersService pattern).
    """
    cur = MagicMock()
    cur.fetchone = MagicMock(return_value=None)
    cur.fetchall = MagicMock(return_value=[])

    def fake_execute(query, params=None):
        if "memory_items" in query:
            cur.fetchall = MagicMock(return_value=memory_rows or [])
        elif "messages" in query:
            cur.fetchall = MagicMock(return_value=message_rows or [])
        else:
            cur.fetchall = MagicMock(return_value=[])

    cur.execute = fake_execute

    # Sync context managers
    cursor_cm = MagicMock()
    cursor_cm.__enter__ = MagicMock(return_value=cur)
    cursor_cm.__exit__ = MagicMock(return_value=None)

    conn = MagicMock()
    conn.cursor = MagicMock(return_value=cursor_cm)

    conn_cm = MagicMock()
    conn_cm.__enter__ = MagicMock(return_value=conn)
    conn_cm.__exit__ = MagicMock(return_value=None)

    pool = MagicMock()
    pool.connection = MagicMock(return_value=conn_cm)
    return pool


@pytest.mark.no_isolated_db
def test_get_preferences_extracts_size():
    pool = _make_pool(
        memory_rows=[{"subject": "áo", "predicate": "size", "object": "M", "content": ""}],
    )
    svc = UserProfileService(pool)
    prefs = svc.get_preferences("t1", "u1")
    assert "m" in prefs.sizes


@pytest.mark.no_isolated_db
def test_get_preferences_extracts_color():
    pool = _make_pool(
        memory_rows=[{"subject": "áo", "predicate": "color", "object": "trắng", "content": ""}],
    )
    svc = UserProfileService(pool)
    prefs = svc.get_preferences("t1", "u1")
    assert "trắng" in prefs.colors


@pytest.mark.no_isolated_db
def test_get_preferences_extracts_price():
    pool = _make_pool(
        message_rows=[{"content": "Áo này giá 250000 không?"}],
    )
    svc = UserProfileService(pool)
    prefs = svc.get_preferences("t1", "u1")
    assert prefs.price_max is not None and prefs.price_max >= 250000


@pytest.mark.no_isolated_db
def test_format_for_context_with_data():
    prefs = UserPreferences(
        sizes=["m", "l"],
        colors=["trắng", "xanh"],
        price_max=500000,
        categories=[],
    )
    out = UserProfileService.format_for_context(prefs)
    assert "<user_profile>" in out
    assert "Sizes mentioned: m, l" in out
    assert "Colors mentioned: trắng, xanh" in out
    assert "500,000" in out


@pytest.mark.no_isolated_db
def test_format_for_context_empty():
    prefs = UserPreferences(sizes=[], colors=[], price_max=None, categories=[])
    out = UserProfileService.format_for_context(prefs)
    assert out == ""
