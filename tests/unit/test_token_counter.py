"""Unit tests for the token counting utility.

Validates the behaviour of ``app.utils.token_counter``: tiktoken-backed
counts, None/empty handling, and per-message overhead.
"""
from __future__ import annotations

import pytest

from app.models.chat import Message
from app.utils.token_counter import (
    _TIKTOKEN_METHOD,
    count_messages_tokens,
    count_text_tokens,
    token_counting_method,
)


@pytest.mark.unit
def test_count_text_tokens_basic() -> None:
    assert count_text_tokens("hello") > 0
    assert count_text_tokens("hello") == count_text_tokens("hello")


@pytest.mark.unit
def test_count_text_tokens_handles_empty_and_none() -> None:
    assert count_text_tokens("") == 0
    assert count_text_tokens(None) == 0


@pytest.mark.unit
def test_count_text_tokens_scales_with_length() -> None:
    short = count_text_tokens("hi")
    long = count_text_tokens("hi " + "this is a longer sentence " * 20)
    assert long > short


@pytest.mark.unit
def test_count_messages_tokens_includes_overhead() -> None:
    msgs = [Message(role="user", content="hi")]
    tokens = count_messages_tokens(msgs)
    # At least 4 (overhead) + a few for content + 2 priming
    assert tokens > 6


@pytest.mark.unit
def test_count_messages_tokens_handles_empty_list() -> None:
    # Empty list still gets the 2 priming tokens (OpenAI cookbook formula).
    tokens = count_messages_tokens([])
    assert tokens >= 2


@pytest.mark.unit
def test_token_counting_method_uses_tiktoken() -> None:
    # tiktoken ships in our venv; the method should be the tiktoken one.
    assert token_counting_method() == _TIKTOKEN_METHOD
