"""Unit tests for UserProfileService (sync implementation)."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.services.user_profile_service import (
    PRICE_RE,
    UserPreferences,
    UserProfileService,
    _looks_like_price,
)


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


# ---------------------------------------------------------------------------
# PRICE_RE / _looks_like_price regression tests
# ---------------------------------------------------------------------------


@pytest.mark.no_isolated_db
@pytest.mark.unit
def test_price_re_matches_k_suffix():
    """Vietnamese 'k' style (e.g. '250k' = 250,000 VND) must be captured."""
    assert "250k" in PRICE_RE.findall("áo này 250k")
    assert "1200k" in PRICE_RE.findall("mua 1200k")
    assert "99k" in PRICE_RE.findall("chỉ 99k thôi")


@pytest.mark.no_isolated_db
@pytest.mark.unit
def test_price_re_matches_thousands_separator():
    """Thousands separators (250.000 or 1,200,000) must be captured."""
    assert "250.000" in PRICE_RE.findall("giá 250.000")
    assert "1.200.000" in PRICE_RE.findall("khoảng 1.200.000 đ")


@pytest.mark.no_isolated_db
@pytest.mark.unit
def test_price_re_matches_bare_round_amounts():
    """Bare 6-digit round numbers (250000) should be captured."""
    assert "250000" in PRICE_RE.findall("giá 250000")


@pytest.mark.no_isolated_db
@pytest.mark.unit
def test_price_re_excludes_order_code_fragment_at_word_boundary():
    """The '34880' fragment inside 'ORD-_000-34880' is captured at a word
    boundary, but _looks_like_price must reject it as an order-code-like
    bare 5-digit number.
    """
    matches = PRICE_RE.findall("Mã đơn: ORD-_000-34880")
    assert "34880" in matches
    assert _looks_like_price("34880") is False


@pytest.mark.no_isolated_db
@pytest.mark.unit
def test_looks_like_price_accepts_k_suffix_of_any_length():
    """k-suffix on 1, 2, 3, or 4 digit numbers must all be accepted."""
    for tok in ("1k", "99k", "250k", "1200k"):
        assert _looks_like_price(tok) is True, f"{tok} should be a price"


@pytest.mark.no_isolated_db
@pytest.mark.unit
def test_looks_like_price_accepts_thousands_separator():
    assert _looks_like_price("250.000") is True
    assert _looks_like_price("1,200,000") is True


@pytest.mark.no_isolated_db
@pytest.mark.unit
def test_looks_like_price_rejects_5_digit_bare():
    """5-digit bare numbers (order-code style) must be rejected."""
    assert _looks_like_price("34880") is False
    assert _looks_like_price("12345") is False


@pytest.mark.no_isolated_db
@pytest.mark.unit
def test_looks_like_price_accepts_4_digit_ending_in_000_or_500():
    """4-digit round VND amounts (1000, 2500, 5000, 50000) must be accepted."""
    assert _looks_like_price("1000") is True
    assert _looks_like_price("2500") is True
    assert _looks_like_price("5000") is True
    assert _looks_like_price("50000") is True


@pytest.mark.no_isolated_db
@pytest.mark.unit
def test_price_extracted_from_message_with_k_suffix():
    """End-to-end: a user message saying '250k' should produce a price
    of 250000 (not 250).
    """
    pool = _make_pool(
        message_rows=[{"content": "Áo này giá 250k không?"}],
    )
    svc = UserProfileService(pool)
    prefs = svc.get_preferences("t1", "u1")
    assert prefs.price_max == 250000


@pytest.mark.no_isolated_db
@pytest.mark.unit
def test_order_code_in_message_does_not_become_price():
    """If the user pastes an order code containing a 5-digit fragment, it
    must not pollute the price_max.
    """
    pool = _make_pool(
        message_rows=[{"content": "Đơn của tôi là ORD-_000-34880"}],
    )
    svc = UserProfileService(pool)
    prefs = svc.get_preferences("t1", "u1")
    assert prefs.price_max is None


@pytest.mark.no_isolated_db
@pytest.mark.unit
def test_price_extracted_from_message_with_1200k():
    """4-digit k-suffix (1200k) must yield 1,200,000 (regression for the
    bug where 'k' was outside the capture group, giving only 1200).
    """
    pool = _make_pool(
        message_rows=[{"content": "Mua áo 1200k nha"}],
    )
    svc = UserProfileService(pool)
    prefs = svc.get_preferences("t1", "u1")
    assert prefs.price_max == 1_200_000
